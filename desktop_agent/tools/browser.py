import webbrowser
from collections.abc import Callable
from urllib.parse import urlparse

from desktop_agent.models import ToolResult

BrowserOpener = Callable[[str], bool]


def open_url(
    url: str,
    *,
    opener: BrowserOpener = webbrowser.open_new_tab,
) -> ToolResult:
    """Abre una URL HTTP(S) con el navegador predeterminado del sistema."""

    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return ToolResult(success=False, message="La URL no es HTTP(S) válida.")

    opened = opener(url)
    if not opened:
        return ToolResult(
            success=False,
            message="No se pudo confirmar la apertura del sitio. Revisá el navegador elegido y la conexión de la extensión en Sesión web.",
        )

    return ToolResult(success=True, message=f"URL abierta correctamente: {url}")
