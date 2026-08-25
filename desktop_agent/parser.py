import re
import unicodedata

from desktop_agent.browser_contract import normalize_search_query
from desktop_agent.catalog import (
    APPLICATION_ALIASES,
    SUPPORTED_APPLICATIONS,
    SUPPORTED_SITES,
)
from desktop_agent.models import Action, Intent, RiskLevel

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
_YOUTUBE_STOP_PATTERN = re.compile(
    r"^\s*(?:deten[eé]|detener|par[aá]|parar)\s+youtube\s*$",
    re.IGNORECASE,
)


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

    if _YOUTUBE_STOP_PATTERN.fullmatch(command):
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
    if len(parts) != 2 or parts[0] not in {"abrir", "abri"}:
        return None

    target = parts[1]
    site = SUPPORTED_SITES.get(target)
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
