"""Prueba opt-in del Chrome habitual; sin IA, claves de API ni cambio de preferencias."""
import argparse
import time

from desktop_agent.browser_bridge import BrowserBridgeOperation, BrowserBridgeState, BrowserKind
from desktop_agent.browser_preferences import BrowserPreference, PreferredBrowser, StaticBrowserPreferenceSource
from desktop_agent.browser_runtime import BrowserRuntime
from desktop_agent.cli import build_executor
from desktop_agent.command_processor import CommandProcessor
from desktop_agent.interpretation import HybridInterpreter
from desktop_agent.logging_config import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Autoriza dos reproducciones de prueba y su pausa final en Chrome.")
    args = parser.parse_args()
    if not args.run:
        print("No se ejecutó nada. Usar --run sólo con autorización para dos reproducciones en Chrome.")
        return 0
    logger = configure_logging()
    runtime = BrowserRuntime()
    playback = None
    processor = None
    try:
        runtime.start()
        deadline = time.monotonic() + 5
        while runtime.snapshot.state is BrowserBridgeState.WAITING and time.monotonic() < deadline:
            time.sleep(.1)
        print("Puente:", runtime.snapshot.state.value, flush=True)
        if runtime.snapshot.state is not BrowserBridgeState.CONNECTED or runtime.snapshot.browser is not BrowserKind.CHROME:
            print("No se controló otra sesión. Comprobar extensión activa en Chrome; no se instaló ni recargó automáticamente.")
            return 1
        preferences = StaticBrowserPreferenceSource(BrowserPreference(PreferredBrowser.CHROME, True))
        executor, playback = build_executor(logger, browser_preferences=preferences, browser_bridge=runtime.bridge)
        processor = CommandProcessor(executor, HybridInterpreter(), logger)
        for command in ("poné en youtube qué tan malo puedo ser", "poné lofi hip hop en youtube"):
            result = processor.execute(command)
            print(f"Recorrido: success={result.success} duration_ms={result.duration_ms:.0f}", flush=True)
            if not result.success:
                return 1
        stopped = processor.execute("detener youtube")
        if not stopped.success:
            return 1
        response = runtime.bridge.request(BrowserBridgeOperation.YOUTUBE_READ, {}, BrowserKind.CHROME)
        paused = bool(response.success and response.payload and response.payload.get("paused") is True)
        print(f"Pausa final comprobada={paused}; pestaña no cerrada. Audio físico y misma pestaña requieren observación del usuario.")
        return 0 if paused else 1
    finally:
        try:
            if playback is not None and playback.has_active_session and processor is not None:
                processor.execute("detener youtube")
        finally:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
