import io
import json
import logging
import socket
import threading
import unittest
import urllib.parse
import urllib.request

from desktop_agent.models import ToolResult
from desktop_agent.spotify import (
    HttpResponse,
    LoopbackSpotifyAuthorizer,
    SpotifyConfig,
    SpotifyDevice,
    SpotifyError,
    SpotifyPlaybackState,
    SpotifyPlaybackTool,
    SpotifyPlaylist,
    SpotifyTokenSession,
    SpotifyTrack,
    SpotifyWebApi,
    TokenGrant,
    open_spotify_search,
)


class FakeAuthorizer:
    def __init__(self, authorize_grant=None, refresh_grant=None):
        self.authorize_grant = authorize_grant
        self.refresh_grant = refresh_grant
        self.authorize_calls = []
        self.refresh_calls = []

    def authorize(self, client_id, timeout):
        self.authorize_calls.append((client_id, timeout))
        return self.authorize_grant

    def refresh(self, client_id, refresh_token, timeout):
        self.refresh_calls.append((client_id, refresh_token, timeout))
        return self.refresh_grant


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, *, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body, timeout))
        if not self.responses:
            raise AssertionError("No hay una respuesta simulada.")
        return self.responses.pop(0)


class FakeTokens:
    def __init__(self):
        self.calls = []

    def access_token(self, *, force_refresh=False):
        self.calls.append(force_refresh)
        return "refreshed-access" if force_refresh else "initial-access"


def device(*, active=True, volume=40):
    return SpotifyDevice(
        "desktop-id",
        "DESKTOP",
        "Computer",
        active,
        False,
        True,
        volume,
    )


def track(name="Song", uri="spotify:track:one"):
    return SpotifyTrack(uri, name, ("Artist",))


def state(*, playing=True, item=None, context=None, volume=40, progress=10_000):
    return SpotifyPlaybackState(
        playing,
        item if item is not None else track(),
        context,
        device(active=playing, volume=volume),
        progress,
    )


class FakeApi:
    def __init__(self):
        self.track_result = track()
        self.playlist_results = (SpotifyPlaylist("spotify:playlist:seven", "7W7"),)
        self.device_results = (device(active=False),)
        self.playback_results = []
        self.calls = []

    def search_track(self, query):
        self.calls.append(("search", query))
        return self.track_result

    def playlists(self):
        self.calls.append(("playlists",))
        return self.playlist_results

    def devices(self):
        self.calls.append(("devices",))
        return self.device_results

    def playback(self):
        self.calls.append(("playback",))
        if not self.playback_results:
            return None
        if len(self.playback_results) == 1:
            return self.playback_results[0]
        return self.playback_results.pop(0)

    def start_track(self, uri, device_id):
        self.calls.append(("start_track", uri, device_id))

    def start_context(self, uri, device_id):
        self.calls.append(("start_context", uri, device_id))

    def pause(self, device_id):
        self.calls.append(("pause", device_id))

    def resume(self, device_id):
        self.calls.append(("resume", device_id))

    def next(self, device_id):
        self.calls.append(("next", device_id))

    def previous(self, device_id):
        self.calls.append(("previous", device_id))

    def set_volume(self, device_id, percent):
        self.calls.append(("volume", device_id, percent))


class SpotifyConfigAndTokenTests(unittest.TestCase):
    def test_configuration_and_token_repr_do_not_expose_refresh_tokens(self):
        config = SpotifyConfig(
            enabled=True,
            client_id="client-id",
            refresh_token="private-refresh-token",
        )
        grant = TokenGrant("private-access-token", 3600, "private-refresh-token")

        self.assertNotIn("private-refresh-token", repr(config))
        self.assertNotIn("private-access-token", repr(grant))
        self.assertNotIn("private-refresh-token", repr(grant))

    def test_first_authorization_is_persisted_and_access_token_is_cached(self):
        saved = []
        authorizer = FakeAuthorizer(
            authorize_grant=TokenGrant("access", 3600, "refresh")
        )
        session = SpotifyTokenSession(
            SpotifyConfig(enabled=True, client_id="client-id"),
            authorizer,
            saved.append,
            clock=lambda: 100,
        )

        self.assertEqual(session.access_token(), "access")
        self.assertEqual(session.access_token(), "access")
        self.assertEqual(authorizer.authorize_calls, [("client-id", 180.0)])
        self.assertEqual(authorizer.refresh_calls, [])
        self.assertEqual(saved, ["refresh"])

    def test_existing_refresh_token_uses_refresh_flow_and_keeps_it_when_not_rotated(self):
        saved = []
        authorizer = FakeAuthorizer(
            refresh_grant=TokenGrant("access", 3600, None)
        )
        session = SpotifyTokenSession(
            SpotifyConfig(
                enabled=True,
                client_id="client-id",
                refresh_token="existing-refresh",
            ),
            authorizer,
            saved.append,
            clock=lambda: 100,
        )

        self.assertEqual(session.access_token(), "access")
        self.assertEqual(
            authorizer.refresh_calls,
            [("client-id", "existing-refresh", 8.0)],
        )
        self.assertEqual(saved, [])

    def test_disabled_session_fails_before_authorization(self):
        authorizer = FakeAuthorizer()
        session = SpotifyTokenSession(
            SpotifyConfig(),
            authorizer,
            lambda _: None,
        )
        with self.assertRaises(SpotifyError) as context:
            session.access_token()
        self.assertEqual(context.exception.code, "spotify_configuration")
        self.assertEqual(authorizer.authorize_calls, [])

    def test_refresh_request_uses_pkce_client_id_without_client_secret(self):
        transport = FakeTransport(
            [
                HttpResponse(
                    200,
                    json.dumps(
                        {"access_token": "access", "expires_in": 3600}
                    ).encode(),
                )
            ]
        )
        grant = LoopbackSpotifyAuthorizer(transport=transport).refresh(
            "client-id",
            "refresh-token",
            5,
        )

        self.assertEqual(grant.access_token, "access")
        body = transport.calls[0][3].decode()
        self.assertIn("client_id=client-id", body)
        self.assertIn("refresh_token=refresh-token", body)
        self.assertNotIn("client_secret", body)

    def test_loopback_authorization_waits_for_and_validates_callback(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        callback_errors = []
        callback_thread = None

        def open_authorization(url):
            nonlocal callback_thread
            state_value = urllib.parse.parse_qs(
                urllib.parse.urlsplit(url).query
            )["state"][0]

            def send_callback():
                try:
                    callback = (
                        f"{redirect_uri}?"
                        + urllib.parse.urlencode(
                            {"code": "authorization-code", "state": state_value}
                        )
                    )
                    with urllib.request.urlopen(callback, timeout=2) as response:
                        response.read()
                except Exception as error:  # La aserción posterior conserva el fallo.
                    callback_errors.append(error)

            callback_thread = threading.Thread(target=send_callback, daemon=True)
            callback_thread.start()
            return True

        transport = FakeTransport(
            [
                HttpResponse(
                    200,
                    json.dumps(
                        {
                            "access_token": "access",
                            "expires_in": 3600,
                            "refresh_token": "refresh",
                        }
                    ).encode(),
                )
            ]
        )
        authorizer = LoopbackSpotifyAuthorizer(
            transport=transport,
            browser_opener=open_authorization,
            redirect_uri=redirect_uri,
        )

        grant = authorizer.authorize("client-id", 2)
        if callback_thread is not None:
            callback_thread.join(2)

        self.assertEqual(grant.access_token, "access")
        self.assertEqual(callback_errors, [])
        token_body = transport.calls[0][3].decode()
        self.assertIn("code=authorization-code", token_body)
        self.assertIn("code_verifier=", token_body)
        self.assertNotIn("client_secret", token_body)

    def test_loopback_browser_failure_is_structured_and_does_not_request_token(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        transport = FakeTransport([])

        def fail_to_open(_url):
            raise RuntimeError("private")

        authorizer = LoopbackSpotifyAuthorizer(
            transport=transport,
            browser_opener=fail_to_open,
            redirect_uri=f"http://127.0.0.1:{port}/callback",
        )

        with self.assertRaises(SpotifyError) as context:
            authorizer.authorize("client-id", 2)

        self.assertEqual(context.exception.code, "spotify_authorization")
        self.assertNotIn("private", str(context.exception))
        self.assertEqual(transport.calls, [])


class SpotifyWebApiTests(unittest.TestCase):
    def test_visible_search_prefers_encoded_spotify_uri(self):
        protocol_calls = []
        browser_calls = []

        opened = open_spotify_search(
            "AC/DC canción",
            protocol_opener=lambda uri: protocol_calls.append(uri) is None,
            browser_opener=lambda url: browser_calls.append(url) is None,
        )

        self.assertTrue(opened)
        self.assertEqual(
            protocol_calls,
            ["spotify:search:AC%2FDC%20canci%C3%B3n"],
        )
        self.assertEqual(browser_calls, [])

    def test_visible_search_falls_back_to_spotify_web(self):
        browser_calls = []

        opened = open_spotify_search(
            "Song Name",
            protocol_opener=lambda _: False,
            browser_opener=lambda url: browser_calls.append(url) is None,
        )

        self.assertTrue(opened)
        self.assertEqual(
            browser_calls,
            ["https://open.spotify.com/search/Song%20Name"],
        )

    def test_retries_once_with_fresh_token_after_401(self):
        tokens = FakeTokens()
        payload = json.dumps({"devices": []}).encode()
        transport = FakeTransport([HttpResponse(401, b"{}"), HttpResponse(200, payload)])
        api = SpotifyWebApi(tokens, transport)

        self.assertEqual(api.devices(), ())
        self.assertEqual(tokens.calls, [False, True])
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(
            transport.calls[1][2]["Authorization"],
            "Bearer refreshed-access",
        )

    def test_search_and_playback_accept_only_expected_shapes(self):
        tokens = FakeTokens()
        search = {
            "tracks": {
                "items": [
                    {
                        "uri": "spotify:track:one",
                        "name": "Song",
                        "artists": [{"name": "Artist"}],
                    }
                ]
            }
        }
        playback = {
            "is_playing": True,
            "item": search["tracks"]["items"][0],
            "context": {"uri": "spotify:playlist:seven"},
            "device": {
                "id": "desktop-id",
                "name": "DESKTOP",
                "type": "Computer",
                "is_active": True,
                "is_restricted": False,
                "supports_volume": True,
                "volume_percent": 40,
            },
        }
        transport = FakeTransport(
            [
                HttpResponse(200, json.dumps(search).encode()),
                HttpResponse(200, json.dumps(playback).encode()),
            ]
        )
        api = SpotifyWebApi(tokens, transport)

        self.assertEqual(api.search_track("Song"), track())
        current = api.playback()
        self.assertTrue(current.is_playing)
        self.assertEqual(current.context_uri, "spotify:playlist:seven")
        self.assertIn("q=Song", transport.calls[0][1])

    def test_premium_or_permission_failure_is_classified(self):
        transport = FakeTransport([HttpResponse(403, b"{}")])
        api = SpotifyWebApi(FakeTokens(), transport)

        with self.assertRaises(SpotifyError) as context:
            api.resume("desktop-id")

        self.assertEqual(context.exception.code, "spotify_permission")
        self.assertNotIn("{}", str(context.exception))

    def test_player_commands_accept_documented_success_family_and_use_expected_verbs(self):
        transport = FakeTransport(
            [
                HttpResponse(200, b"non-json success body"),
                HttpResponse(202, b"accepted"),
                HttpResponse(204, b""),
            ]
        )
        api = SpotifyWebApi(FakeTokens(), transport)

        api.next("desktop-id")
        api.previous("desktop-id")
        api.pause("desktop-id")

        self.assertEqual([call[0] for call in transport.calls], ["POST", "POST", "PUT"])

    def test_player_commands_reject_status_outside_closed_success_set(self):
        api = SpotifyWebApi(
            FakeTokens(),
            FakeTransport([HttpResponse(201, b"")]),
        )

        with self.assertRaises(SpotifyError) as context:
            api.pause("desktop-id")

        self.assertEqual(context.exception.code, "spotify_service")

    def test_playback_tolerates_episode_and_nullable_device_id(self):
        playback = {
            "is_playing": True,
            "item": {
                "type": "episode",
                "uri": "spotify:episode:one",
                "name": "Episode",
            },
            "context": {"uri": "spotify:show:one"},
            "device": {
                "id": None,
                "name": "Speaker",
                "type": "Speaker",
                "is_active": True,
                "is_restricted": False,
                "supports_volume": True,
                "volume_percent": 40,
            },
        }
        devices = {
            "devices": [
                playback["device"],
                {
                    "id": "desktop-id",
                    "name": "DESKTOP",
                    "type": "Computer",
                    "is_active": False,
                    "is_restricted": False,
                    "supports_volume": True,
                    "volume_percent": 30,
                },
            ]
        }
        transport = FakeTransport(
            [
                HttpResponse(200, json.dumps(playback).encode()),
                HttpResponse(200, json.dumps(devices).encode()),
            ]
        )
        api = SpotifyWebApi(FakeTokens(), transport)

        current = api.playback()
        available = api.devices()

        self.assertIsNone(current.item)
        self.assertIsNone(current.device)
        self.assertEqual([item.device_id for item in available], ["desktop-id"])


class SpotifyPlaybackToolTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.output = io.StringIO()
        self.logger = logging.Logger(self.id(), level=logging.INFO)
        self.logger.addHandler(logging.StreamHandler(self.output))
        self.tool = SpotifyPlaybackTool(
            self.api,
            self.logger,
            enabled=True,
            application_opener=lambda _: ToolResult(False, "not opened"),
            search_opener=lambda _: True,
            sleeper=lambda _: None,
            device_attempts=1,
            verify_attempts=2,
        )

    def test_track_search_play_and_verification_use_structured_values(self):
        self.api.playback_results = [state(item=track())]

        result = self.tool.play_track("Secret Song")

        self.assertTrue(result.success)
        self.assertIn(("search", "Secret Song"), self.api.calls)
        self.assertIn(
            ("start_track", "spotify:track:one", "desktop-id"),
            self.api.calls,
        )
        self.assertNotIn("Secret Song", self.output.getvalue())

    def test_playlist_name_ignores_spaces_but_rejects_ambiguity(self):
        self.api.playback_results = [
            state(context="spotify:playlist:seven")
        ]
        result = self.tool.play_playlist("7 W7")
        self.assertTrue(result.success)
        self.assertIn(
            ("start_context", "spotify:playlist:seven", "desktop-id"),
            self.api.calls,
        )

        self.api.playlist_results = (
            SpotifyPlaylist("spotify:playlist:one", "7W7"),
            SpotifyPlaylist("spotify:playlist:two", "7 W7"),
        )
        ambiguous = self.tool.play_playlist("7W7")
        self.assertFalse(ambiguous.success)
        self.assertEqual(ambiguous.error_code, "spotify_ambiguous")

    def test_pause_and_resume_control_current_state_without_search(self):
        self.api.playback_results = [state(playing=True), state(playing=False)]
        paused = self.tool.pause()
        self.assertTrue(paused.success)
        self.assertIn(("pause", "desktop-id"), self.api.calls)
        self.assertNotIn("search", [call[0] for call in self.api.calls])

    def test_search_opens_visible_spotify_query_without_logging_it(self):
        opened = []
        tool = SpotifyPlaybackTool(
            self.api,
            self.logger,
            enabled=True,
            application_opener=lambda _: ToolResult(False, "not opened"),
            search_opener=lambda query: opened.append(query) is None,
            sleeper=lambda _: None,
        )

        result = tool.search_track("Secret Song")

        self.assertTrue(result.success)
        self.assertEqual(opened, ["Secret Song"])
        self.assertIn("Búsqueda visible", result.message)
        self.assertNotIn("Secret Song", self.output.getvalue())

    def test_search_fails_honestly_when_visible_view_cannot_open(self):
        tool = SpotifyPlaybackTool(
            self.api,
            self.logger,
            enabled=True,
            search_opener=lambda _: False,
        )

        result = tool.search_track("Song")

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "spotify_ui_open")

    def test_next_requires_observable_track_or_position_change(self):
        self.api.playback_results = [
            state(item=track("One", "spotify:track:one"), progress=20_000),
            state(item=track("One", "spotify:track:one"), progress=21_000),
            state(item=track("One", "spotify:track:one"), progress=22_000),
        ]

        result = self.tool.next()

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "spotify_not_confirmed")

    def test_next_confirms_a_different_track_and_previous_can_confirm_a_restart(self):
        first = track("One", "spotify:track:one")
        second = track("Two", "spotify:track:two")
        self.api.playback_results = [
            state(item=first, progress=20_000),
            state(item=second, progress=500),
        ]

        advanced = self.tool.next()

        self.assertTrue(advanced.success)
        self.api.calls.clear()
        self.api.playback_results = [
            state(item=second, progress=20_000),
            state(item=second, progress=500),
        ]

        restarted = self.tool.previous()

        self.assertTrue(restarted.success)
        self.assertIn(("previous", "desktop-id"), self.api.calls)

        self.api.calls.clear()
        self.api.playback_results = [state(playing=False), state(playing=True)]
        resumed = self.tool.resume()
        self.assertTrue(resumed.success)
        self.assertIn(("resume", "desktop-id"), self.api.calls)
        self.assertNotIn("search", [call[0] for call in self.api.calls])

    def test_volume_is_validated_and_confirmed(self):
        self.api.playback_results = [state(volume=40), state(volume=35)]

        result = self.tool.set_volume("35")

        self.assertTrue(result.success)
        self.assertIn(("volume", "desktop-id", 35), self.api.calls)
        invalid = self.tool.set_volume("101")
        self.assertFalse(invalid.success)
        self.assertEqual(invalid.error_code, "spotify_invalid_input")

    def test_does_not_choose_a_phone_or_guess_between_computers(self):
        phone = SpotifyDevice("phone", "Phone", "Smartphone", True, False, True, 20)
        self.api.device_results = (phone,)
        self.api.playback_results = [state(item=track())]
        result = self.tool.play_track("Song")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "spotify_no_device")

        computer_two = SpotifyDevice("second", "OTHER", "Computer", False, False, True, 20)
        self.api.device_results = (device(active=False), computer_two)
        result = self.tool.play_track("Song")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "spotify_ambiguous")

    def test_disabled_tool_has_no_external_effect(self):
        tool = SpotifyPlaybackTool(None, self.logger, enabled=False)
        result = tool.play_track("Song")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "spotify_configuration")


if __name__ == "__main__":
    unittest.main()
