"""Entrada compatible: abre la GUI con su configuración persistente."""

import os
import sys
from collections.abc import Callable, Mapping

from desktop_agent.provider_config import (
    AI_ENABLED_ENV,
    AI_MONTHLY_BUDGET_ENV,
    OPENAI_API_KEY_ENV,
)
from desktop_agent.tk_app import run_gui
from desktop_agent.voice_transcription_config import (
    VOICE_TRANSCRIPTION_ENABLED_ENV,
)


SecretReader = Callable[[str], str | None]
GuiRunner = Callable[[Mapping[str, str]], int]


def _read_secret_dialog(prompt: str) -> str | None:
    """Solicita la clave en un Entry enmascarado que admite pegar en Windows."""

    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return simpledialog.askstring(
            "Desktop Agent — OpenAI API key",
            prompt,
            show="*",
            parent=root,
        )
    finally:
        root.destroy()


def start_voice_gui(
    base_environ: Mapping[str, str] | None = None,
    *,
    secret_reader: SecretReader = _read_secret_dialog,
    gui_runner: GuiRunner = run_gui,
) -> int:
    """Crea un entorno efímero con voz e interpretación natural habilitadas."""

    source = dict(os.environ if base_environ is None else base_environ)
    api_key = source.get(OPENAI_API_KEY_ENV)
    if api_key is None:
        try:
            api_key = secret_reader(
                "Pegá la API key para voz y órdenes naturales. "
                "Se mostrará enmascarada y no se guardará:",
            )
        except (Exception, KeyboardInterrupt):
            print("No se recibió la credencial; no se inició la GUI.", file=sys.stderr)
            return 2
    if not isinstance(api_key, str) or not api_key.strip():
        print("No se recibió la credencial; no se inició la GUI.", file=sys.stderr)
        return 2
    if api_key != api_key.strip():
        print("La credencial no debe incluir espacios exteriores.", file=sys.stderr)
        return 2

    source[OPENAI_API_KEY_ENV] = api_key
    source[AI_ENABLED_ENV] = "true"
    source[VOICE_TRANSCRIPTION_ENABLED_ENV] = "true"
    source.setdefault(AI_MONTHLY_BUDGET_ENV, "1.00")
    return gui_runner(source)


def main() -> int:
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
