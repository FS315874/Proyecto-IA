import logging

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
from desktop_agent.local_service import DesktopAgentService, TaskState
from desktop_agent.models import ToolResult
from desktop_agent.tk_app import TkDesktopAgentApp


class QaProcessor:
    def execute(self, command, output=print, progress=None):
        if progress is not None:
            progress(CommandProgress(CommandStage.INTERPRETING))
            progress(CommandProgress(CommandStage.EXECUTING, "qa_ui_tool"))
            progress(CommandProgress(CommandStage.FINISHED, "qa_ui_tool"))
        interpretation = InterpretationResult(
            action=None,
            path=InterpretationPath.DETERMINISTIC,
            status=InterpretationStatus.SUCCESS,
            duration_ms=1,
            provider_configured=False,
        )
        return CommandExecution(
            True,
            ("Resultado ficticio observable.",),
            interpretation,
            2,
            ToolResult(True, "Resultado ficticio observable."),
            "qa_ui_tool",
        )


class QaPlayback:
    @property
    def has_active_session(self) -> bool:
        return False

    def stop(self) -> ToolResult:
        return ToolResult(True, "Sin reproducción.")


def main() -> int:
    import tkinter as tk

    logger = logging.getLogger("ui-smoke")
    controller = LocalAgentController(QaProcessor(), QaPlayback(), logger)
    service = DesktopAgentService(controller, logger)
    root = tk.Tk()
    root.withdraw()
    TkDesktopAgentApp(root, service)
    service.submit("orden ficticia de interfaz")
    root.after(400, service.request_close)
    root.after(2500, root.destroy)
    root.mainloop()
    service.close(2)
    history = service.snapshot.history
    if (
        len(history) != 1
        or history[0].state is not TaskState.SUCCEEDED
        or history[0].evidence is None
        or not history[0].evidence.observable_success
    ):
        print("UI_QA_FAILED")
        return 1
    print(
        "UI_QA_OK: Tk real renderizo historial, herramienta y evidencia; "
        "servicio local cerro sin efectos externos."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
