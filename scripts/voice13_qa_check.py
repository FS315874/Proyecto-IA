import io
import logging
import threading

from desktop_agent.command_processor import (
    CommandExecution,
    CommandProgress,
    CommandStage,
)
from desktop_agent.interpretation import (
    InterpretationPath,
    InterpretationResult,
    InterpretationStatus,
)
from desktop_agent.local_controller import LocalAgentController
from desktop_agent.local_service import DesktopAgentService, TaskKind, TaskState
from desktop_agent.models import ToolResult
from desktop_agent.tk_app import TkDesktopAgentApp
from desktop_agent.voice import (
    VoiceBackendResult,
    VoiceController,
    VoiceResultStatus,
)


class QaVoiceBackend:
    def __init__(self) -> None:
        self.results = [
            VoiceBackendResult(
                VoiceResultStatus.READY,
                "abrir calculadora original",
                0.92,
                "es-UY",
                100,
                10,
            ),
            VoiceBackendResult(
                VoiceResultStatus.READY,
                "cancelar agente",
                0.95,
                "es-UY",
                90,
                8,
            ),
        ]

    def recognize(self, cancellation: threading.Event) -> VoiceBackendResult:
        return self.results.pop(0)


class QaProcessor:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute(self, command, output=print, progress=None):
        self.commands.append(command)
        if progress is not None:
            progress(CommandProgress(CommandStage.INTERPRETING))
            progress(CommandProgress(CommandStage.EXECUTING, "qa_voice_tool"))
            progress(CommandProgress(CommandStage.FINISHED, "qa_voice_tool"))
        interpretation = InterpretationResult(
            action=None,
            path=InterpretationPath.DETERMINISTIC,
            status=InterpretationStatus.SUCCESS,
            duration_ms=1,
            provider_configured=False,
        )
        return CommandExecution(
            True,
            ("Resultado de voz ficticio.",),
            interpretation,
            2,
            ToolResult(True, "Resultado de voz ficticio."),
            "qa_voice_tool",
        )


class QaPlayback:
    def __init__(self) -> None:
        self.active = True
        self.stop_calls = 0

    @property
    def has_active_session(self) -> bool:
        return self.active

    def stop(self) -> ToolResult:
        self.stop_calls += 1
        self.active = False
        return ToolResult(True, "Detenido por voz.")


def main() -> int:
    import tkinter as tk

    stream = io.StringIO()
    logger = logging.Logger("voice-ui-qa")
    logger.addHandler(logging.StreamHandler(stream))
    processor = QaProcessor()
    playback = QaPlayback()
    controller = LocalAgentController(processor, playback, logger)
    service = DesktopAgentService(controller, logger)
    voice = VoiceController(QaVoiceBackend(), logger)
    root = tk.Tk()
    root.withdraw()
    app = TkDesktopAgentApp(root, service, voice)

    root.after(30, app._voice_start.invoke)

    def correct_and_submit() -> None:
        app._transcript.delete(0, tk.END)
        app._transcript.insert(0, "abrir calculadora corregido")
        app._voice_submit.invoke()

    root.after(180, correct_and_submit)
    root.after(300, app._voice_start.invoke)
    root.after(700, service.request_close)
    root.after(2500, root.destroy)
    root.mainloop()
    voice.close(2)
    service.close(2)

    history = service.snapshot.history
    private_values = (
        "abrir calculadora original",
        "abrir calculadora corregido",
        "cancelar agente",
    )
    if (
        processor.commands != ["abrir calculadora corregido"]
        or playback.stop_calls != 1
        or len(history) != 1
        or history[0].kind is not TaskKind.VOICE_COMMAND
        or history[0].state is not TaskState.SUCCEEDED
        or any(value in stream.getvalue() for value in private_values)
    ):
        print("VOICE_QA_FAILED")
        return 1
    print(
        "VOICE_QA_OK: transcripcion simulada corregida y enviada por el pipeline; "
        "cancelacion verbal exacta activo emergencia; sin microfono ni red."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
