"""Control acotado de Spotify mediante OAuth PKCE y la Web API oficial."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Protocol

from desktop_agent.browser_contract import normalize_search_query
from desktop_agent.models import ToolResult
from desktop_agent.tools.applications import open_application

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:43817/callback"
SPOTIFY_SCOPES = (
    "user-modify-playback-state",
    "user-read-playback-state",
    "playlist-read-private",
    "playlist-read-collaborative",
)
MAX_RESPONSE_BYTES = 2_000_000
MAX_PLAYLIST_PAGES = 20
SPOTIFY_COMMAND_SUCCESS_STATUSES = {200, 202, 204}


class SpotifyError(RuntimeError):
    """Fallo esperado y redactado de configuración, autorización o reproducción."""

    def __init__(self, code: str, message: str, stage: str) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage


@dataclass(frozen=True)
class SpotifyConfig:
    enabled: bool = False
    client_id: str | None = None
    refresh_token: str | None = field(default=None, repr=False)
    preferred_device_name: str | None = None
    timeout_seconds: float = 8.0
    authorization_timeout_seconds: float = 180.0

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("La activación de Spotify no es válida.")
        if self.client_id is not None and (
            not isinstance(self.client_id, str)
            or not self.client_id
            or self.client_id != self.client_id.strip()
            or len(self.client_id) > 200
            or any(character.isspace() for character in self.client_id)
        ):
            raise ValueError("El Client ID de Spotify no es válido.")
        if self.refresh_token is not None and (
            not isinstance(self.refresh_token, str)
            or not self.refresh_token
            or len(self.refresh_token) > 8192
            or any(character.isspace() for character in self.refresh_token)
        ):
            raise ValueError("La autorización de Spotify no es válida.")
        if self.preferred_device_name is not None and (
            not isinstance(self.preferred_device_name, str)
            or not self.preferred_device_name
            or self.preferred_device_name != self.preferred_device_name.strip()
            or len(self.preferred_device_name) > 200
        ):
            raise ValueError("El dispositivo preferido no es válido.")
        if (
            type(self.timeout_seconds) not in (int, float)
            or not 1 <= float(self.timeout_seconds) <= 120
        ):
            raise ValueError("El timeout de Spotify no es válido.")
        if (
            type(self.authorization_timeout_seconds) not in (int, float)
            or not 30 <= float(self.authorization_timeout_seconds) <= 300
        ):
            raise ValueError("El timeout de autorización de Spotify no es válido.")
        if self.enabled and self.client_id is None:
            raise ValueError("Spotify necesita un Client ID.")


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse: ...


class UrlLibTransport:
    """Transporte stdlib con tamaño y timeout limitados."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
                status = int(response.status)
        except urllib.error.HTTPError as error:
            payload = error.read(MAX_RESPONSE_BYTES + 1)
            status = int(error.code)
        except (OSError, TimeoutError, urllib.error.URLError):
            raise SpotifyError(
                "spotify_network",
                "No se pudo conectar con Spotify.",
                "spotify_request",
            ) from None
        if len(payload) > MAX_RESPONSE_BYTES:
            raise SpotifyError(
                "spotify_invalid_response",
                "Spotify devolvió una respuesta demasiado grande.",
                "spotify_request",
            )
        return HttpResponse(status, payload)


@dataclass(frozen=True)
class TokenGrant:
    access_token: str = field(repr=False)
    expires_in: int
    refresh_token: str | None = field(repr=False)


class SpotifyAuthorizer(Protocol):
    def authorize(self, client_id: str, timeout: float) -> TokenGrant: ...

    def refresh(
        self,
        client_id: str,
        refresh_token: str,
        timeout: float,
    ) -> TokenGrant: ...


def _json_object(response: HttpResponse, stage: str) -> dict[str, object]:
    try:
        value = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SpotifyError(
            "spotify_invalid_response",
            "Spotify devolvió una respuesta ilegible.",
            stage,
        ) from None
    if not isinstance(value, dict):
        raise SpotifyError(
            "spotify_invalid_response",
            "Spotify devolvió una respuesta inválida.",
            stage,
        )
    return value


def _token_grant(response: HttpResponse) -> TokenGrant:
    if response.status != 200:
        code = "spotify_authentication" if response.status in {400, 401} else "spotify_network"
        raise SpotifyError(
            code,
            "Spotify rechazó la autorización. Revisá el Client ID y volvé a conectar la cuenta.",
            "spotify_authorization",
        )
    payload = _json_object(response, "spotify_authorization")
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in")
    refresh_token = payload.get("refresh_token")
    if (
        not isinstance(access_token, str)
        or not access_token
        or type(expires_in) is not int
        or not 1 <= expires_in <= 86_400
        or (refresh_token is not None and (not isinstance(refresh_token, str) or not refresh_token))
    ):
        raise SpotifyError(
            "spotify_invalid_response",
            "Spotify devolvió credenciales incompletas.",
            "spotify_authorization",
        )
    return TokenGrant(access_token, expires_in, refresh_token)


class LoopbackSpotifyAuthorizer:
    """Autoriza al usuario en el navegador sin almacenar un client secret."""

    def __init__(
        self,
        transport: HttpTransport | None = None,
        browser_opener: Callable[[str], bool] = webbrowser.open,
        redirect_uri: str = SPOTIFY_REDIRECT_URI,
    ) -> None:
        self._transport = transport or UrlLibTransport()
        self._browser_opener = browser_opener
        self._redirect_uri = redirect_uri

    def authorize(self, client_id: str, timeout: float) -> TokenGrant:
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(32)
        query = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": self._redirect_uri,
                "scope": " ".join(SPOTIFY_SCOPES),
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "state": state,
            }
        )
        code = self._receive_code(f"{SPOTIFY_AUTHORIZE_URL}?{query}", state, timeout)
        body = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
            }
        ).encode("ascii")
        response = self._transport.request(
            "POST",
            SPOTIFY_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=body,
            timeout=min(timeout, 30.0),
        )
        grant = _token_grant(response)
        if grant.refresh_token is None:
            raise SpotifyError(
                "spotify_invalid_response",
                "Spotify no entregó una autorización renovable.",
                "spotify_authorization",
            )
        return grant

    def refresh(
        self,
        client_id: str,
        refresh_token: str,
        timeout: float,
    ) -> TokenGrant:
        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            }
        ).encode("ascii")
        response = self._transport.request(
            "POST",
            SPOTIFY_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=body,
            timeout=timeout,
        )
        return _token_grant(response)

    def _receive_code(self, url: str, expected_state: str, timeout: float) -> str:
        parsed_redirect = urllib.parse.urlsplit(self._redirect_uri)
        port = parsed_redirect.port
        if parsed_redirect.hostname != "127.0.0.1" or port is None:
            raise SpotifyError(
                "spotify_configuration",
                "La dirección de retorno de Spotify no es segura.",
                "spotify_authorization",
            )
        result: dict[str, str] = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - contrato de BaseHTTPRequestHandler
                parsed = urllib.parse.urlsplit(self.path)
                values = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                if parsed.path != parsed_redirect.path:
                    self._reply(404, "Solicitud no reconocida.")
                    return
                received_state = values.get("state", [""])[0]
                if not hmac.compare_digest(received_state, expected_state):
                    result["error"] = "state"
                    self._reply(400, "La autorización no pudo validarse.")
                    return
                if values.get("error"):
                    result["error"] = "denied"
                    self._reply(403, "Autorización cancelada. Ya podés cerrar esta pestaña.")
                    return
                received_code = values.get("code", [""])[0]
                if not received_code:
                    result["error"] = "missing"
                    self._reply(400, "Spotify no devolvió el código esperado.")
                    return
                result["code"] = received_code
                self._reply(200, "Spotify quedó conectado. Podés volver a Desktop Agent.")

            def _reply(self, status: int, message: str) -> None:
                body = (
                    "<!doctype html><meta charset='utf-8'><title>Desktop Agent</title>"
                    f"<p>{message}</p>"
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *args: object) -> None:
                return

        try:
            server = HTTPServer(("127.0.0.1", port), CallbackHandler)
        except OSError:
            raise SpotifyError(
                "spotify_configuration",
                "No se pudo reservar el puerto local de Spotify. Cerrá otra instancia y reintentá.",
                "spotify_authorization",
            ) from None
        try:
            try:
                opened = self._browser_opener(url)
            except Exception:
                opened = False
            if not opened:
                raise SpotifyError(
                    "spotify_authorization",
                    "No se pudo abrir la autorización de Spotify en el navegador.",
                    "spotify_authorization",
                )
            deadline = time.monotonic() + timeout
            while not result:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                server.timeout = min(remaining, 0.5)
                try:
                    server.handle_request()
                except OSError:
                    raise SpotifyError(
                        "spotify_network",
                        "Se interrumpió el retorno local de Spotify.",
                        "spotify_authorization",
                    ) from None
        finally:
            server.server_close()
        if "code" in result:
            return result["code"]
        if result.get("error") == "denied":
            raise SpotifyError(
                "spotify_authorization",
                "Cancelaste la autorización de Spotify; no se ejecutó la orden.",
                "spotify_authorization",
            )
        raise SpotifyError(
            "spotify_authorization",
            "Spotify no completó la autorización a tiempo.",
            "spotify_authorization",
        )


class SpotifyTokenSession:
    """Mantiene el access token en memoria y persiste sólo el refresh token."""

    def __init__(
        self,
        config: SpotifyConfig,
        authorizer: SpotifyAuthorizer,
        save_refresh_token: Callable[[str], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._authorizer = authorizer
        self._save_refresh_token = save_refresh_token
        self._clock = clock
        self._refresh_token = config.refresh_token
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def access_token(self, *, force_refresh: bool = False) -> str:
        if not self._config.enabled or self._config.client_id is None:
            raise SpotifyError(
                "spotify_configuration",
                "Activá Spotify y agregá el Client ID desde Configuración.",
                "spotify_authorization",
            )
        with self._lock:
            if (
                not force_refresh
                and self._access_token is not None
                and self._clock() < self._expires_at
            ):
                return self._access_token
            if self._refresh_token is None:
                grant = self._authorizer.authorize(
                    self._config.client_id,
                    self._config.authorization_timeout_seconds,
                )
            else:
                grant = self._authorizer.refresh(
                    self._config.client_id,
                    self._refresh_token,
                    self._config.timeout_seconds,
                )
            refresh_token = grant.refresh_token or self._refresh_token
            if refresh_token is None:
                raise SpotifyError(
                    "spotify_invalid_response",
                    "Spotify no entregó una autorización renovable.",
                    "spotify_authorization",
                )
            if refresh_token != self._refresh_token:
                try:
                    self._save_refresh_token(refresh_token)
                except Exception:
                    raise SpotifyError(
                        "spotify_configuration",
                        "No se pudo guardar de forma segura la autorización de Spotify.",
                        "spotify_authorization",
                    ) from None
                self._refresh_token = refresh_token
            self._access_token = grant.access_token
            self._expires_at = self._clock() + max(1, grant.expires_in - 30)
            return grant.access_token


@dataclass(frozen=True)
class SpotifyDevice:
    device_id: str
    name: str
    device_type: str
    is_active: bool
    is_restricted: bool
    supports_volume: bool
    volume_percent: int | None


@dataclass(frozen=True)
class SpotifyTrack:
    uri: str
    name: str
    artists: tuple[str, ...]


@dataclass(frozen=True)
class SpotifyPlaylist:
    uri: str
    name: str


@dataclass(frozen=True)
class SpotifyPlaybackState:
    is_playing: bool
    item: SpotifyTrack | None
    context_uri: str | None
    device: SpotifyDevice | None
    progress_ms: int | None = None


class SpotifyWebApi:
    """Cliente mínimo; todos los endpoints y parámetros se construyen localmente."""

    def __init__(
        self,
        tokens: SpotifyTokenSession,
        transport: HttpTransport | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        self._tokens = tokens
        self._transport = transport or UrlLibTransport()
        self._timeout = float(timeout_seconds)

    def devices(self) -> tuple[SpotifyDevice, ...]:
        payload = self._request_json("GET", "/me/player/devices", expected={200})
        assert payload is not None
        raw_devices = payload.get("devices")
        if not isinstance(raw_devices, list):
            self._invalid_response("spotify_device")
        devices: list[SpotifyDevice] = []
        for raw in raw_devices:
            if not isinstance(raw, dict):
                self._invalid_response("spotify_device")
            device_id = raw.get("id")
            name = raw.get("name")
            device_type = raw.get("type")
            is_active = raw.get("is_active")
            is_restricted = raw.get("is_restricted")
            supports_volume = raw.get("supports_volume", False)
            volume = raw.get("volume_percent")
            if device_id is None:
                # La API documenta IDs nulos para ciertos dispositivos. No son
                # direccionables, pero tampoco deben invalidar al resto.
                continue
            if (
                not isinstance(device_id, str)
                or not device_id
                or not isinstance(name, str)
                or not name
                or not isinstance(device_type, str)
                or type(is_active) is not bool
                or type(is_restricted) is not bool
                or type(supports_volume) is not bool
                or (volume is not None and (type(volume) is not int or not 0 <= volume <= 100))
            ):
                self._invalid_response("spotify_device")
            devices.append(
                SpotifyDevice(
                    device_id,
                    name,
                    device_type,
                    is_active,
                    is_restricted,
                    supports_volume,
                    volume,
                )
            )
        return tuple(devices)

    def playback(self) -> SpotifyPlaybackState | None:
        payload = self._request_json("GET", "/me/player", expected={200, 204})
        if payload is None:
            return None
        is_playing = payload.get("is_playing")
        if type(is_playing) is not bool:
            self._invalid_response("spotify_verify")
        device = self._parse_device(payload.get("device"))
        item = self._parse_track(payload.get("item"), optional=True)
        context = payload.get("context")
        context_uri = None
        if context is not None:
            if not isinstance(context, dict) or not isinstance(context.get("uri"), str):
                self._invalid_response("spotify_verify")
            context_uri = context["uri"]
        progress_ms = payload.get("progress_ms")
        if progress_ms is not None and (
            type(progress_ms) is not int or progress_ms < 0
        ):
            self._invalid_response("spotify_verify")
        return SpotifyPlaybackState(
            is_playing,
            item,
            context_uri,
            device,
            progress_ms,
        )

    def search_track(self, query: str) -> SpotifyTrack | None:
        normalized = normalize_search_query(query)
        suffix = urllib.parse.urlencode({"q": normalized, "type": "track", "limit": 5})
        payload = self._request_json("GET", f"/search?{suffix}", expected={200})
        assert payload is not None
        tracks = payload.get("tracks")
        if not isinstance(tracks, dict) or not isinstance(tracks.get("items"), list):
            self._invalid_response("spotify_search")
        for item in tracks["items"]:
            if item is not None:
                return self._parse_track(item)
        return None

    def playlists(self) -> tuple[SpotifyPlaylist, ...]:
        playlists: list[SpotifyPlaylist] = []
        for page in range(MAX_PLAYLIST_PAGES):
            suffix = urllib.parse.urlencode({"limit": 50, "offset": page * 50})
            payload = self._request_json(
                "GET",
                f"/me/playlists?{suffix}",
                expected={200},
            )
            assert payload is not None
            items = payload.get("items")
            if not isinstance(items, list):
                self._invalid_response("spotify_search")
            for item in items:
                if not isinstance(item, dict):
                    self._invalid_response("spotify_search")
                uri = item.get("uri")
                name = item.get("name")
                if not isinstance(uri, str) or not uri.startswith("spotify:playlist:") or not isinstance(name, str) or not name:
                    self._invalid_response("spotify_search")
                playlists.append(SpotifyPlaylist(uri, name))
            if len(items) < 50:
                break
        return tuple(playlists)

    def start_track(self, uri: str, device_id: str) -> None:
        self._player_command(
            "PUT",
            "/me/player/play",
            device_id,
            {"uris": [uri]},
            "spotify_playback",
        )

    def start_context(self, uri: str, device_id: str) -> None:
        self._player_command(
            "PUT",
            "/me/player/play",
            device_id,
            {"context_uri": uri},
            "spotify_playback",
        )

    def resume(self, device_id: str) -> None:
        self._player_command(
            "PUT", "/me/player/play", device_id, None, "spotify_playback"
        )

    def pause(self, device_id: str) -> None:
        self._player_command(
            "PUT", "/me/player/pause", device_id, None, "spotify_playback"
        )

    def next(self, device_id: str) -> None:
        self._player_command(
            "POST", "/me/player/next", device_id, None, "spotify_playback"
        )

    def previous(self, device_id: str) -> None:
        self._player_command(
            "POST", "/me/player/previous", device_id, None, "spotify_playback"
        )

    def set_volume(self, device_id: str, percent: int) -> None:
        suffix = urllib.parse.urlencode(
            {"device_id": device_id, "volume_percent": percent}
        )
        self._request_json(
            "PUT",
            f"/me/player/volume?{suffix}",
            expected={204},
            stage="spotify_playback",
        )

    def _player_command(
        self,
        method: str,
        path: str,
        device_id: str,
        body: dict[str, object] | None,
        stage: str,
    ) -> None:
        suffix = urllib.parse.urlencode({"device_id": device_id})
        self._request_json(
            method,
            f"{path}?{suffix}",
            expected=SPOTIFY_COMMAND_SUCCESS_STATUSES,
            json_body=body,
            stage=stage,
            discard_body=True,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        expected: set[int],
        json_body: dict[str, object] | None = None,
        stage: str = "spotify_request",
        discard_body: bool = False,
    ) -> dict[str, object] | None:
        if not path.startswith("/"):
            raise ValueError("La ruta de Spotify debe ser relativa.")
        body = None
        headers: dict[str, str] = {}
        if json_body is not None:
            body = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        response = self._authorized_request(method, path, headers, body)
        if response.status not in expected:
            self._raise_status(response.status, stage)
        if discard_body or response.status == 204 or not response.body:
            return None
        return _json_object(response, stage)

    def _authorized_request(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> HttpResponse:
        token = self._tokens.access_token()
        request_headers = {**headers, "Authorization": f"Bearer {token}"}
        response = self._transport.request(
            method,
            SPOTIFY_API_BASE_URL + path,
            headers=request_headers,
            body=body,
            timeout=self._timeout,
        )
        if response.status != 401:
            return response
        token = self._tokens.access_token(force_refresh=True)
        return self._transport.request(
            method,
            SPOTIFY_API_BASE_URL + path,
            headers={**headers, "Authorization": f"Bearer {token}"},
            body=body,
            timeout=self._timeout,
        )

    @staticmethod
    def _raise_status(status: int, stage: str) -> None:
        if status == 401:
            code = "spotify_authentication"
            message = "La autorización de Spotify venció o fue revocada. Volvé a conectar la cuenta."
        elif status == 403:
            code = "spotify_permission"
            message = "Spotify rechazó el control. Verificá que la cuenta sea Premium y que hayas concedido los permisos."
        elif status == 404:
            code = "spotify_no_device"
            message = "Spotify no encontró un reproductor disponible. Abrí Spotify en esta computadora y reintentá."
        elif status == 429:
            code = "spotify_rate_limit"
            message = "Spotify pidió esperar antes de otra orden. Reintentá más tarde."
        else:
            code = "spotify_service"
            message = "Spotify no pudo completar la operación."
        raise SpotifyError(code, message, stage)

    @staticmethod
    def _invalid_response(stage: str) -> None:
        raise SpotifyError(
            "spotify_invalid_response",
            "Spotify devolvió un estado que Desktop Agent no pudo validar.",
            stage,
        )

    def _parse_device(self, raw: object) -> SpotifyDevice | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            self._invalid_response("spotify_verify")
        device_id = raw.get("id")
        if device_id is None:
            return None
        name = raw.get("name")
        device_type = raw.get("type")
        is_active = raw.get("is_active")
        is_restricted = raw.get("is_restricted")
        supports_volume = raw.get("supports_volume", False)
        volume = raw.get("volume_percent")
        if (
            not isinstance(device_id, str)
            or not device_id
            or not isinstance(name, str)
            or not name
            or not isinstance(device_type, str)
            or type(is_active) is not bool
            or type(is_restricted) is not bool
            or type(supports_volume) is not bool
            or (volume is not None and (type(volume) is not int or not 0 <= volume <= 100))
        ):
            self._invalid_response("spotify_verify")
        return SpotifyDevice(
            device_id,
            name,
            device_type,
            is_active,
            is_restricted,
            supports_volume,
            volume,
        )

    def _parse_track(self, raw: object, *, optional: bool = False) -> SpotifyTrack | None:
        if raw is None and optional:
            return None
        if not isinstance(raw, dict):
            self._invalid_response("spotify_search")
        uri = raw.get("uri")
        if optional and (
            raw.get("type") == "episode"
            or (isinstance(uri, str) and uri.startswith("spotify:episode:"))
        ):
            return None
        name = raw.get("name")
        artists_raw = raw.get("artists")
        if (
            not isinstance(uri, str)
            or not uri.startswith("spotify:track:")
            or not isinstance(name, str)
            or not name
            or not isinstance(artists_raw, list)
        ):
            self._invalid_response("spotify_search")
        artists: list[str] = []
        for artist in artists_raw:
            if not isinstance(artist, dict) or not isinstance(artist.get("name"), str):
                self._invalid_response("spotify_search")
            artists.append(artist["name"])
        if not artists:
            self._invalid_response("spotify_search")
        return SpotifyTrack(uri, name, tuple(artists))


def _normalized_name(value: str) -> str:
    without_accents = "".join(
        character
        for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", "", without_accents)


def _open_spotify_protocol(uri: str) -> bool:
    """Solicita a Windows abrir un URI registrado, sin invocar una shell."""

    if os.name != "nt":
        return False
    try:
        os.startfile(uri)  # type: ignore[attr-defined]
    except OSError:
        return False
    return True


def open_spotify_search(
    query: str,
    *,
    protocol_opener: Callable[[str], bool] = _open_spotify_protocol,
    browser_opener: Callable[[str], bool] = webbrowser.open,
) -> bool:
    """Abre una búsqueda visible; la consulta se normaliza y codifica como datos."""

    normalized = normalize_search_query(query)
    encoded = urllib.parse.quote(normalized, safe="")
    if protocol_opener(f"spotify:search:{encoded}"):
        return True
    try:
        return bool(browser_opener(f"https://open.spotify.com/search/{encoded}"))
    except (OSError, webbrowser.Error):
        return False


class SpotifyPlaybackTool:
    """Herramientas explícitas de Spotify con selección y verificación local."""

    def __init__(
        self,
        api: SpotifyWebApi | None,
        logger: logging.Logger,
        *,
        enabled: bool,
        preferred_device_name: str | None = None,
        application_opener: Callable[[str], ToolResult] = open_application,
        search_opener: Callable[[str], bool] = open_spotify_search,
        sleeper: Callable[[float], None] = time.sleep,
        device_attempts: int = 6,
        verify_attempts: int = 5,
    ) -> None:
        self._api = api
        self._logger = logger
        self._enabled = enabled
        self._preferred_device_name = preferred_device_name
        self._application_opener = application_opener
        self._search_opener = search_opener
        self._sleep = sleeper
        self._device_attempts = device_attempts
        self._verify_attempts = verify_attempts

    def play_track(self, query: str) -> ToolResult:
        return self._execute("play_track", lambda: self._play_track(query))

    def play_playlist(self, name: str) -> ToolResult:
        return self._execute("play_playlist", lambda: self._play_playlist(name))

    def search_track(self, query: str) -> ToolResult:
        return self._execute("search_track", lambda: self._search_track(query))

    def pause(self) -> ToolResult:
        return self._execute("pause", self._pause)

    def resume(self) -> ToolResult:
        return self._execute("resume", self._resume)

    def next(self) -> ToolResult:
        return self._execute("next", lambda: self._skip(next_track=True))

    def previous(self) -> ToolResult:
        return self._execute("previous", lambda: self._skip(next_track=False))

    def set_volume(self, percent: str) -> ToolResult:
        return self._execute("volume", lambda: self._set_volume(percent))

    def _execute(self, operation: str, action: Callable[[], ToolResult]) -> ToolResult:
        if not self._enabled or self._api is None:
            return ToolResult(
                False,
                "Spotify no está configurado. Activá la integración y agregá el Client ID en Configuración.",
                "spotify_configuration",
                "spotify_authorization",
            )
        self._logger.info("Spotify operation: %s", operation)
        try:
            return action()
        except (TypeError, ValueError):
            return ToolResult(
                False,
                "La orden de Spotify contiene un valor inválido.",
                "spotify_invalid_input",
                "spotify_request",
            )
        except SpotifyError as error:
            return ToolResult(False, str(error), error.code, error.stage)

    def _play_track(self, query: str) -> ToolResult:
        api = self._required_api()
        track = api.search_track(normalize_search_query(query))
        if track is None:
            raise SpotifyError(
                "spotify_no_results",
                "Spotify no encontró una canción para esa búsqueda.",
                "spotify_search",
            )
        device = self._desktop_device()
        api.start_track(track.uri, device.device_id)
        self._verify_playing(item_uri=track.uri)
        return ToolResult(True, f"Reproduciendo en Spotify: {track.name} — {', '.join(track.artists)}.")

    def _play_playlist(self, name: str) -> ToolResult:
        normalized = normalize_search_query(name)
        playlists = self._required_api().playlists()
        requested = _normalized_name(normalized)
        matches = [item for item in playlists if _normalized_name(item.name) == requested]
        if not matches:
            matches = [item for item in playlists if requested in _normalized_name(item.name)]
        if not matches:
            raise SpotifyError(
                "spotify_no_results",
                "Spotify no encontró una playlist con ese nombre en tu biblioteca.",
                "spotify_search",
            )
        if len(matches) != 1:
            raise SpotifyError(
                "spotify_ambiguous",
                "Hay más de una playlist que coincide. Decí el nombre completo para elegir una sola.",
                "spotify_search",
            )
        playlist = matches[0]
        device = self._desktop_device()
        self._required_api().start_context(playlist.uri, device.device_id)
        self._verify_playing(context_uri=playlist.uri)
        return ToolResult(True, f"Reproduciendo la playlist de Spotify: {playlist.name}.")

    def _search_track(self, query: str) -> ToolResult:
        normalized = normalize_search_query(query)
        track = self._required_api().search_track(normalized)
        if track is None:
            raise SpotifyError(
                "spotify_no_results",
                "Spotify no encontró una canción para esa búsqueda.",
                "spotify_search",
            )
        if not self._search_opener(normalized):
            raise SpotifyError(
                "spotify_ui_open",
                "Spotify encontró resultados, pero no se pudo abrir la búsqueda visible.",
                "spotify_search",
            )
        return ToolResult(
            True,
            f"Búsqueda visible abierta en Spotify. Primer resultado: {track.name} — {', '.join(track.artists)}.",
        )

    def _pause(self) -> ToolResult:
        api = self._required_api()
        current = api.playback()
        if current is None or current.device is None or not current.is_playing:
            return ToolResult(True, "Spotify no estaba reproduciendo.")
        api.pause(current.device.device_id)
        self._verify_paused()
        return ToolResult(True, "Spotify quedó en pausa.")

    def _resume(self) -> ToolResult:
        api = self._required_api()
        current = api.playback()
        if current is not None and current.is_playing:
            return ToolResult(True, "Spotify ya estaba reproduciendo.")
        device = current.device if current is not None else None
        if device is None:
            device = self._desktop_device()
        api.resume(device.device_id)
        self._verify_playing()
        return ToolResult(True, "Spotify reanudó la reproducción.")

    def _skip(self, *, next_track: bool) -> ToolResult:
        api = self._required_api()
        current = api.playback()
        if current is None or current.device is None or current.item is None:
            raise SpotifyError(
                "spotify_no_device",
                "No hay una reproducción de Spotify que se pueda controlar.",
                "spotify_playback",
            )
        if next_track:
            api.next(current.device.device_id)
        else:
            api.previous(current.device.device_id)
        state = self._verify_skip(current)
        direction = "siguiente" if next_track else "anterior"
        return ToolResult(True, f"Spotify pasó a la canción {direction}: {state.item.name}.")

    def _set_volume(self, percent: str) -> ToolResult:
        if not isinstance(percent, str) or re.fullmatch(r"\d{1,3}", percent) is None:
            raise ValueError()
        numeric_percent = int(percent)
        if not 0 <= numeric_percent <= 100:
            raise ValueError()
        api = self._required_api()
        current = api.playback()
        device = current.device if current is not None else None
        if device is None:
            device = self._desktop_device()
        if device.is_restricted or not device.supports_volume:
            raise SpotifyError(
                "spotify_permission",
                "Ese dispositivo no permite cambiar el volumen desde Spotify.",
                "spotify_playback",
            )
        api.set_volume(device.device_id, numeric_percent)
        for attempt in range(self._verify_attempts):
            if attempt:
                self._sleep(0.25)
            state = api.playback()
            if state is not None and state.device is not None and state.device.volume_percent == numeric_percent:
                return ToolResult(True, f"Volumen de Spotify ajustado a {numeric_percent} %.")
        raise SpotifyError(
            "spotify_not_confirmed",
            "Spotify aceptó el volumen, pero no se pudo confirmar el valor final.",
            "spotify_verify",
        )

    def _desktop_device(self) -> SpotifyDevice:
        api = self._required_api()
        devices = api.devices()
        device = self._choose_device(devices)
        if device is not None:
            return device
        opened = self._application_opener("spotify")
        if not opened.success:
            raise SpotifyError(
                "spotify_no_device",
                "No se encontró un reproductor de Spotify en esta computadora.",
                "spotify_device",
            )
        for _ in range(self._device_attempts):
            self._sleep(0.5)
            device = self._choose_device(api.devices())
            if device is not None:
                return device
        raise SpotifyError(
            "spotify_no_device",
            "Spotify se abrió, pero todavía no aparece como dispositivo disponible. Esperá unos segundos y reintentá.",
            "spotify_device",
        )

    def _choose_device(self, devices: tuple[SpotifyDevice, ...]) -> SpotifyDevice | None:
        usable = [device for device in devices if not device.is_restricted]
        if self._preferred_device_name is not None:
            preferred = [
                device
                for device in usable
                if _normalized_name(device.name)
                == _normalized_name(self._preferred_device_name)
            ]
            if len(preferred) == 1:
                return preferred[0]
            if len(preferred) > 1:
                raise SpotifyError(
                    "spotify_ambiguous",
                    "Más de un dispositivo coincide con el nombre configurado.",
                    "spotify_device",
                )
            return None
        computers = [
            device for device in usable if device.device_type.casefold() == "computer"
        ]
        active_computers = [device for device in computers if device.is_active]
        if len(active_computers) == 1:
            return active_computers[0]
        if len(computers) == 1:
            return computers[0]
        if len(computers) > 1:
            raise SpotifyError(
                "spotify_ambiguous",
                "Hay varias computadoras disponibles. Indicá el nombre del dispositivo en Configuración.",
                "spotify_device",
            )
        return None

    def _verify_playing(
        self,
        *,
        item_uri: str | None = None,
        context_uri: str | None = None,
    ) -> SpotifyPlaybackState:
        api = self._required_api()
        for attempt in range(self._verify_attempts):
            if attempt:
                self._sleep(0.25)
            state = api.playback()
            if state is None or not state.is_playing:
                continue
            if item_uri is not None and (state.item is None or state.item.uri != item_uri):
                continue
            if context_uri is not None and state.context_uri != context_uri:
                continue
            return state
        raise SpotifyError(
            "spotify_not_confirmed",
            "Spotify aceptó la orden, pero no se pudo confirmar la reproducción.",
            "spotify_verify",
        )

    def _verify_paused(self) -> None:
        api = self._required_api()
        for attempt in range(self._verify_attempts):
            if attempt:
                self._sleep(0.25)
            state = api.playback()
            if state is None or not state.is_playing:
                return
        raise SpotifyError(
            "spotify_not_confirmed",
            "Spotify aceptó la pausa, pero el reproductor sigue activo.",
            "spotify_verify",
        )

    def _verify_skip(
        self,
        previous: SpotifyPlaybackState,
    ) -> SpotifyPlaybackState:
        api = self._required_api()
        assert previous.item is not None
        for attempt in range(self._verify_attempts):
            if attempt:
                self._sleep(0.25)
            state = api.playback()
            if state is None or state.item is None:
                continue
            if state.item.uri != previous.item.uri:
                return state
            if (
                previous.progress_ms is not None
                and state.progress_ms is not None
                and state.progress_ms + 1_000 < previous.progress_ms
            ):
                # Dos posiciones claramente distintas también prueban el salto cuando
                # la cola contiene la misma canción dos veces o «anterior» reinicia.
                return state
        raise SpotifyError(
            "spotify_not_confirmed",
            "Spotify recibió la orden, pero la canción no cambió.",
            "spotify_verify",
        )

    def _required_api(self) -> SpotifyWebApi:
        if self._api is None:
            raise SpotifyError(
                "spotify_configuration",
                "Spotify no está configurado.",
                "spotify_authorization",
            )
        return self._api


def build_spotify_tool(
    config: SpotifyConfig,
    logger: logging.Logger,
    save_refresh_token: Callable[[str], None],
) -> SpotifyPlaybackTool:
    """Compone la integración real sin hacer red ni abrir Spotify al construirla."""

    if not config.enabled:
        return SpotifyPlaybackTool(
            None,
            logger,
            enabled=False,
            preferred_device_name=config.preferred_device_name,
        )
    authorizer = LoopbackSpotifyAuthorizer()
    tokens = SpotifyTokenSession(config, authorizer, save_refresh_token)
    api = SpotifyWebApi(tokens, timeout_seconds=config.timeout_seconds)
    return SpotifyPlaybackTool(
        api,
        logger,
        enabled=True,
        preferred_device_name=config.preferred_device_name,
    )
