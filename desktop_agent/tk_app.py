import os
import sys
from collections.abc import Mapping

from desktop_agent import __version__
from desktop_agent.command_processor import CommandProcessor, CommandStage
from desktop_agent.interpretation import MonthlyUsageSnapshot
from desktop_agent.local_controller import (
    AlreadyRunningError,
    ControllerState,
    ControllerUpdate,
    ControllerUpdateKind,
    LocalAgentController,
    LocalControllerError,
    SingleInstanceLock,
)
from desktop_agent.logging_config import configure_logging
from desktop_agent.provider_config import (
    ProviderConfigurationError,
    load_provider_config,
)
from desktop_agent.usage_budget import MonthlyUsageLedger, UsageLedgerError

POLL_INTERVAL_MS = 80


def format_usage(snapshot: MonthlyUsageSnapshot | None) -> str:
    if snapshot is None:
        return "Uso IA: no disponible"
    return (
        f"Uso IA {snapshot.month}: {snapshot.total_tokens} tokens · "
        f"USD {snapshot.estimated_cost_usd:.6f} / "
        f"USD {snapshot.budget_usd:.2f}"
    )


def format_latency(update: ControllerUpdate) -> str:
    if update.total_ms is None:
        return "Latencia: sin medición"
    queue_ms = update.queue_ms or 0.0
    execution_ms = max(0.0, update.total_ms - queue_ms)
    return (
        f"Latencia: total {update.total_ms:.0f} ms · "
        f"cola {queue_ms:.0f} ms · ejecución {execution_ms:.0f} ms"
    )


def _read_initial_usage(
    environ: Mapping[str, str] | None = None,
) -> MonthlyUsageSnapshot | None:
    try:
        config = load_provider_config(environ)
        return MonthlyUsageLedger(config.monthly_budget_usd).current_snapshot()
    except (ProviderConfigurationError, UsageLedgerError, OSError):
        return None


class TkDesktopAgentApp:
    """Vista Tkinter del controlador; no contiene lógica de ejecución."""

    def __init__(self, root: object, controller: LocalAgentController) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._root = root
        self._controller = controller
        self._tk = tk
        self._ttk = ttk
        self._closing = False

        root.title(f"Desktop Agent v{__version__}")
        root.geometry("760x520")
        root.minsize(620, 420)
        root.protocol("WM_DELETE_WINDOW", self._request_close)

        frame = ttk.Frame(root, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(4, weight=1)

        ttk.Label(frame, text="Orden").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        command_row = ttk.Frame(frame)
        command_row.grid(row=1, column=0, sticky="ew")
        command_row.columnconfigure(0, weight=1)
        self._command = ttk.Entry(command_row)
        self._command.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._command.bind("<Return>", lambda _: self._submit())
        self._execute = ttk.Button(
            command_row, text="Ejecutar", command=self._submit
        )
        self._execute.grid(row=0, column=1)

        controls = ttk.Frame(frame)
        controls.grid(row=2, column=0, sticky="ew", pady=10)
        self._stop = ttk.Button(
            controls, text="Detener", command=self._request_stop
        )
        self._stop.grid(row=0, column=0, padx=(0, 8))
        self._emergency = ttk.Button(
            controls,
            text="Emergencia",
            command=self._request_emergency_stop,
        )
        self._emergency.grid(row=0, column=1, padx=(0, 8))
        self._close = ttk.Button(
            controls, text="Cerrar agente", command=self._request_close
        )
        self._close.grid(row=0, column=2)

        self._status_text = tk.StringVar(value="Estado: listo")
        self._usage_text = tk.StringVar(
            value=format_usage(controller.snapshot.monthly_usage)
        )
        self._latency_text = tk.StringVar(value="Latencia: sin medición")
        status = ttk.Frame(frame)
        status.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self._status_text).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(status, textvariable=self._usage_text).grid(
            row=1, column=0, sticky="w"
        )
        ttk.Label(status, textvariable=self._latency_text).grid(
            row=2, column=0, sticky="w"
        )

        result_frame = ttk.LabelFrame(frame, text="Resultado", padding=8)
        result_frame.grid(row=4, column=0, sticky="nsew")
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
        self._result = tk.Text(
            result_frame,
            height=12,
            wrap="word",
            state="disabled",
            takefocus=True,
        )
        scrollbar = ttk.Scrollbar(
            result_frame, orient="vertical", command=self._result.yview
        )
        self._result.configure(yscrollcommand=scrollbar.set)
        self._result.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self._command.focus_set()
        root.after(POLL_INTERVAL_MS, self._poll)

    def _submit(self) -> None:
        if self._closing:
            return
        command = self._command.get()
        try:
            request_id = self._controller.submit(command)
        except LocalControllerError as error:
            self._append(str(error))
            return
        self._command.delete(0, self._tk.END)
        self._status_text.set(f"Estado: orden {request_id} recibida")

    def _request_stop(self) -> None:
        self._controller.request_stop()
        self._status_text.set("Estado: detención solicitada")

    def _request_emergency_stop(self) -> None:
        self._controller.request_stop(emergency=True)
        self._status_text.set("Estado: EMERGENCIA solicitada")

    def _request_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._set_controls_enabled(False)
        self._status_text.set("Estado: cerrando de forma segura")
        self._controller.request_close()

    def _poll(self) -> None:
        for update in self._controller.drain_updates():
            self._apply_update(update)
        snapshot = self._controller.snapshot
        self._usage_text.set(format_usage(snapshot.monthly_usage))
        if self._closing and snapshot.state is ControllerState.CLOSED:
            self._root.destroy()
            return
        self._root.after(POLL_INTERVAL_MS, self._poll)

    def _apply_update(self, update: ControllerUpdate) -> None:
        if update.kind is ControllerUpdateKind.PROGRESS:
            stage = {
                CommandStage.INTERPRETING: "interpretando",
                CommandStage.EXECUTING: "ejecutando",
                CommandStage.FINISHED: "finalizando",
            }.get(update.stage, "procesando")
            self._status_text.set(f"Estado: {stage}")
            return
        if update.kind is ControllerUpdateKind.RESULT:
            assert update.execution is not None
            self._append("\n".join(update.execution.messages))
            self._latency_text.set(format_latency(update))
            self._status_text.set(
                "Estado: completado"
                if update.execution.success
                else "Estado: no completado"
            )
            return
        if update.kind in {
            ControllerUpdateKind.STOP_RESULT,
            ControllerUpdateKind.CANCELLED,
            ControllerUpdateKind.ERROR,
        }:
            if update.message:
                self._append(update.message)
            return
        if update.kind is ControllerUpdateKind.STATE:
            state_text = {
                ControllerState.IDLE: "listo",
                ControllerState.RUNNING: "ejecutando",
                ControllerState.STOPPING: "deteniendo",
                ControllerState.CLOSED: "cerrado",
            }[update.state]
            self._status_text.set(f"Estado: {state_text}")

    def _append(self, message: str) -> None:
        self._result.configure(state="normal")
        if self._result.index("end-1c") != "1.0":
            self._result.insert("end", "\n\n")
        self._result.insert("end", message)
        self._result.see("end")
        self._result.configure(state="disabled")

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._command.configure(state=state)
        self._execute.configure(state=state)
        self._stop.configure(state=state)
        self._emergency.configure(state=state)
        self._close.configure(state=state)


def run_gui(environ: Mapping[str, str] | None = None) -> int:
    """Inicia una única consola nativa y conserva el núcleo durante toda la sesión."""

    try:
        import tkinter as tk
    except ModuleNotFoundError:
        print(
            "Tkinter no está disponible. Instalá Python con soporte Tcl/Tk.",
            file=sys.stderr,
        )
        return 1

    try:
        instance_lock = SingleInstanceLock()
        instance_lock.acquire()
    except (AlreadyRunningError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2

    controller: LocalAgentController | None = None
    try:
        logger = configure_logging()
        from desktop_agent.cli import build_executor, build_interpreter

        executor, playback_controller = build_executor(logger)
        interpreter = build_interpreter(
            environ if environ is not None else os.environ,
            warning_output=lambda message: print(message, file=sys.stderr),
        )
        processor = CommandProcessor(executor, interpreter, logger)
        controller = LocalAgentController(
            processor,
            playback_controller,
            logger,
            initial_usage=_read_initial_usage(environ),
        )
        root = tk.Tk()
        TkDesktopAgentApp(root, controller)
        root.mainloop()
        return 0 if controller.close(timeout=10.0) else 1
    except tk.TclError as error:
        print(f"No se pudo iniciar la interfaz: {error}", file=sys.stderr)
        return 1
    finally:
        if controller is not None:
            controller.close(timeout=1.0)
        instance_lock.close()
