import re
import unicodedata

from desktop_agent.browser_contract import normalize_search_query
from desktop_agent.approved_targets import TargetKind, normalize_target_name
from desktop_agent.catalog import (
    APPLICATION_ALIASES,
    SITE_ALIASES,
    SUPPORTED_APPLICATIONS,
    SUPPORTED_SITES,
)
from desktop_agent.models import Action, Intent, RiskLevel
from desktop_agent.output_audio import (
    normalize_output_device_name,
    validate_output_percent,
)

_YOUTUBE_PLAY_PATTERNS = (
    re.compile(
        r"^\s*(?:pon[eé]|reproduc[ií]|reproduce|reproducir)\s+"
        r"en\s+youtube\s+(.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:pon[eé]|reproduc[ií]|reproduce|reproducir)\s+"
        r"(.+?)\s+en\s+youtube\s*$",
        re.IGNORECASE,
    ),
)
_YOUTUBE_STOP_PATTERNS = (
    re.compile(
        r"^\s*(?:deten[eé]|detener|par[aá]|parar)\s+youtube\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:paus[aá]|pausar)\s+(?:youtube|lo\s+que\s+"
        r"(?:est[aá]|estaba)\s+sonando\s+en\s+youtube)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*pon[eé]\s+(?:en\s+)?pausa\s+(?:al|el)\s+video\s+"
        r"que\s+(?:estoy|estaba)\s+(?:mirando|viendo)\s+en\s+youtube\s*$",
        re.IGNORECASE,
    ),
)
_YOUTUBE_RESUME_PATTERNS = (
    re.compile(
        r"^\s*(?:reproduc[ií]|reproduce|reproducir|segu[ií]|seguir)\s+"
        r"(?:reproduciendo\s+)?lo\s+que\s+(?:estoy|estaba)\s+"
        r"(?:mirando|viendo)\s+en\s+youtube\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:reanud[aá]|reanudar)\s+(?:el\s+)?video\s+"
        r"(?:de|en)\s+youtube\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*pon[eé]\s+(?:en\s+)?play\s+(?:al|el)\s+video\s+"
        r"(?:de|en)\s+youtube\s*$",
        re.IGNORECASE,
    ),
)
_SPOTIFY_PAUSE_PATTERNS = (
    re.compile(
        r"^\s*(?:paus[aá]|pausar|deten[eé]|detener|par[aá]|parar)\s+"
        r"(?:spotify|la\s+canci[oó]n\s+(?:de|en)\s+spotify|lo\s+que\s+"
        r"(?:est[aá]|estaba)\s+sonando\s+en\s+spotify)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*pon[eé]\s+(?:en\s+)?pausa\s+(?:a\s+)?(?:spotify|la\s+canci[oó]n\s+"
        r"(?:de|en)\s+spotify|lo\s+que\s+(?:est[aá]|estaba)\s+sonando\s+"
        r"en\s+spotify)\s*$",
        re.IGNORECASE,
    ),
)
_SPOTIFY_RESUME_PATTERNS = (
    re.compile(
        r"^\s*(?:reanud[aá]|reanudar|continu[aá]|continuar)\s+"
        r"(?:spotify|la\s+canci[oó]n\s+(?:de|en)\s+spotify|lo\s+que\s+"
        r"(?:est[aá]|estaba)\s+sonando\s+en\s+spotify)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:segu[ií]|seguir)\s+(?:reproduciendo\s+)?(?:spotify|lo\s+que\s+"
        r"(?:est[aá]|estaba)\s+sonando\s+en\s+spotify)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*pon[eé]\s+(?:en\s+)?play\s+(?:a\s+)?(?:spotify|la\s+canci[oó]n\s+"
        r"(?:de|en)\s+spotify)\s*$",
        re.IGNORECASE,
    ),
)
_SPOTIFY_NEXT_PATTERNS = (
    re.compile(
        r"^\s*(?:pas[aá]|pasar|salt[aá]|saltar|pon[eé])\s+(?:a\s+)?(?:la\s+)?"
        r"siguiente\s+(?:canci[oó]n\s+)?(?:en|de)\s+spotify\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*siguiente\s+(?:canci[oó]n\s+)?(?:en|de)\s+spotify\s*$", re.IGNORECASE),
)
_SPOTIFY_PREVIOUS_PATTERNS = (
    re.compile(
        r"^\s*(?:volv[eé]|volver|pas[aá]|pasar|pon[eé])\s+(?:a\s+)?(?:la\s+)?"
        r"(?:canci[oó]n\s+)?anterior\s+(?:en|de)\s+spotify\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*(?:canci[oó]n\s+)?anterior\s+(?:en|de)\s+spotify\s*$", re.IGNORECASE),
)
_SPOTIFY_VOLUME_PATTERNS = (
    re.compile(
        r"^\s*pon[eé]\s+(?:el\s+)?volumen\s+(?:de|en)\s+spotify\s+"
        r"(?:a|al|en)\s+(\d{1,3})\s*%?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:sub[ií]|baj[aá]|ajust[aá])\s+(?:el\s+)?volumen\s+"
        r"(?:de|en)\s+spotify\s+(?:a|al|en)\s+(\d{1,3})\s*%?\s*$",
        re.IGNORECASE,
    ),
)
_OUTPUT_VOLUME_PATTERNS = (
    re.compile(
        r"^\s*(?:pon[eé]|ajust[aá]|sub[ií]|baj[aá])\s+(?:el\s+)?volumen\s+"
        r"(?:de|en|para)\s+(.+?)\s+(?:a|al|en)\s+"
        r"(?:(?:el\s+)?nivel\s+de\s+)?(\d{1,3})\s*%?\s*$",
        re.IGNORECASE,
    ),
)
_SPOTIFY_PLAYLIST_PATTERNS = (
    re.compile(
        r"^\s*(?:pon[eé]|reproduc[ií]|reproduce|reproducir)\s+(?:en\s+spotify\s+)?"
        r"(?:mi|la)\s+playlist\s+(.+?)(?:\s+en\s+spotify)?\s*$",
        re.IGNORECASE,
    ),
)
_SPOTIFY_SEARCH_PATTERNS = (
    re.compile(
        r"^\s*busc[aá]\s+(?:en\s+spotify\s+)?(?:la\s+canci[oó]n\s+)?"
        r"(.+?)(?:\s+en\s+spotify)?\s*$",
        re.IGNORECASE,
    ),
)
_SPOTIFY_TRACK_PATTERNS = (
    re.compile(
        r"^\s*(?:pon[eé]|reproduc[ií]|reproduce|reproducir)\s+en\s+spotify\s+"
        r"(?:la\s+canci[oó]n\s+)?(.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:pon[eé]|reproduc[ií]|reproduce|reproducir)\s+"
        r"(?:la\s+canci[oó]n\s+)?(.+?)\s+en\s+spotify\s*$",
        re.IGNORECASE,
    ),
)
_LEADING_SENTENCE_PUNCTUATION = re.compile(r"^\s*[¡¿]+\s*")
_TRAILING_SENTENCE_PUNCTUATION = re.compile(r"\s*[.!?¡¿]+\s*$")
_APPROVED_TARGET_PATTERNS = (
    (
        re.compile(r"^\s*(?:abr[ií]|abrir|abre|mostr[aá]|mostrar)\s+(?:el\s+)?proyecto\s+(.+?)\s*$", re.IGNORECASE),
        TargetKind.PROJECT,
    ),
    (
        re.compile(
            r"^\s*(?:abr[ií]|abrir|abre|mostr[aá]|mostrar)\s+"
            r"(?:(?:la\s+)?documentaci[oó]n|(?:el\s+)?(?:archivo|documento))\s+"
            r"(?:de\s+)?(.+?)\s*$",
            re.IGNORECASE,
        ),
        TargetKind.DOCUMENT,
    ),
)


def _without_sentence_boundary_punctuation(command: str) -> str:
    """Quita sólo puntuación que un transcriptor agrega a la frase completa."""

    without_leading = _LEADING_SENTENCE_PUNCTUATION.sub("", command)
    return _TRAILING_SENTENCE_PUNCTUATION.sub("", without_leading)


def _normalize(command: str) -> str:
    without_accents = "".join(
        character
        for character in unicodedata.normalize("NFKD", command.casefold())
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_accents).strip()


def parse_command(command: str) -> Action | None:
    """Convierte un comando conocido en una acción segura y estructurada."""

    if not isinstance(command, str):
        return None
    command = _without_sentence_boundary_punctuation(command)

    for pattern, kind in _APPROVED_TARGET_PATTERNS:
        match = pattern.fullmatch(command)
        if match is not None:
            target = match.group(1).strip()
            try:
                normalize_target_name(target)
            except ValueError:
                return None
            return Action(
                intent=Intent.OPEN_APPLICATION,
                tool_name="open_approved_target",
                arguments={"name": target, "kind": kind.value},
                risk_level=RiskLevel.SAFE,
                requires_confirmation=False,
            )

    spotify_controls = (
        (_SPOTIFY_PAUSE_PATTERNS, "pause_spotify"),
        (_SPOTIFY_RESUME_PATTERNS, "resume_spotify"),
        (_SPOTIFY_NEXT_PATTERNS, "next_spotify"),
        (_SPOTIFY_PREVIOUS_PATTERNS, "previous_spotify"),
    )
    for patterns, tool_name in spotify_controls:
        if any(pattern.fullmatch(command) for pattern in patterns):
            return Action(
                intent=Intent.MEDIA_PLAYBACK,
                tool_name=tool_name,
                arguments={},
                risk_level=RiskLevel.SAFE,
                requires_confirmation=False,
            )

    for pattern in _SPOTIFY_VOLUME_PATTERNS:
        match = pattern.fullmatch(command)
        if match is not None:
            percent = int(match.group(1))
            if percent > 100:
                return None
            return Action(
                intent=Intent.MEDIA_PLAYBACK,
                tool_name="set_spotify_volume",
                arguments={"percent": str(percent)},
                risk_level=RiskLevel.SAFE,
                requires_confirmation=False,
            )

    for pattern in _OUTPUT_VOLUME_PATTERNS:
        match = pattern.fullmatch(command)
        if match is None:
            continue
        device = match.group(1).strip()
        percent = str(int(match.group(2)))
        try:
            normalize_output_device_name(device)
            validate_output_percent(percent)
        except ValueError:
            return None
        return Action(
            intent=Intent.SYSTEM_CHANGE,
            tool_name="set_output_volume",
            arguments={"device": device, "percent": percent},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

    spotify_searches = (
        (_SPOTIFY_PLAYLIST_PATTERNS, "play_spotify_playlist", "name"),
        (_SPOTIFY_SEARCH_PATTERNS, "search_spotify_track", "query"),
        (_SPOTIFY_TRACK_PATTERNS, "play_spotify_track", "query"),
    )
    for patterns, tool_name, argument_name in spotify_searches:
        for pattern in patterns:
            match = pattern.fullmatch(command)
            if match is None:
                continue
            try:
                target = normalize_search_query(match.group(1))
            except ValueError:
                return None
            return Action(
                intent=Intent.MEDIA_PLAYBACK,
                tool_name=tool_name,
                arguments={argument_name: target},
                risk_level=RiskLevel.SAFE,
                requires_confirmation=False,
            )

    if any(pattern.fullmatch(command) for pattern in _YOUTUBE_RESUME_PATTERNS):
        return Action(
            intent=Intent.BROWSER_NAVIGATION,
            tool_name="resume_youtube",
            arguments={},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

    if any(pattern.fullmatch(command) for pattern in _YOUTUBE_STOP_PATTERNS):
        return Action(
            intent=Intent.BROWSER_NAVIGATION,
            tool_name="stop_youtube",
            arguments={},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

    for pattern in _YOUTUBE_PLAY_PATTERNS:
        match = pattern.fullmatch(command)
        if match is None:
            continue
        try:
            query = normalize_search_query(match.group(1))
        except ValueError:
            return None
        return Action(
            intent=Intent.BROWSER_NAVIGATION,
            tool_name="play_youtube",
            arguments={"query": query},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

    normalized = _normalize(command)
    parts = normalized.split(" ", maxsplit=1)
    if len(parts) != 2 or parts[0] not in {"abrir", "abri", "abre"}:
        return None

    target = parts[1]
    target_parts = target.split(" ", maxsplit=1)
    if len(target_parts) == 2 and target_parts[0] in {"el", "la"}:
        target = target_parts[1]
    site_key = SITE_ALIASES.get(target)
    site = SUPPORTED_SITES.get(site_key) if site_key is not None else None
    if site is not None:
        return Action(
            intent=Intent.OPEN_URL,
            tool_name="open_url",
            arguments={"url": site.url},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

    application_key = APPLICATION_ALIASES.get(target)
    if application_key in SUPPORTED_APPLICATIONS:
        return Action(
            intent=Intent.OPEN_APPLICATION,
            tool_name="open_application",
            arguments={"name": application_key},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

    return None
