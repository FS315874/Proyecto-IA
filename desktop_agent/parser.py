import re
import unicodedata
from dataclasses import dataclass

from desktop_agent.models import Action, Intent, RiskLevel


@dataclass(frozen=True)
class Site:
    name: str
    url: str


SUPPORTED_SITES: dict[str, Site] = {
    "youtube": Site(name="YouTube", url="https://www.youtube.com/"),
    "google": Site(name="Google", url="https://www.google.com/"),
    "github": Site(name="GitHub", url="https://github.com/"),
}


def _normalize(command: str) -> str:
    without_accents = "".join(
        character
        for character in unicodedata.normalize("NFKD", command.casefold())
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_accents).strip()


def parse_command(command: str) -> Action | None:
    """Convierte un comando conocido en una acción segura y estructurada."""

    normalized = _normalize(command)
    match = re.fullmatch(r"(?:abrir|abri) ([a-z0-9-]+)", normalized)
    if match is None:
        return None

    site = SUPPORTED_SITES.get(match.group(1))
    if site is None:
        return None

    return Action(
        intent=Intent.OPEN_URL,
        tool_name="open_url",
        arguments={"url": site.url},
        risk_level=RiskLevel.SAFE,
        requires_confirmation=False,
    )

