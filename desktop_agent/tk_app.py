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
    SingleInstanceLock,
)
from desktop_agent.local_service import (
    DesktopAgentService,
    ServiceError,
    ServiceSnapshot,
    ServiceState,
    TaskHistoryEntry,
)
from desktop_agent.logging_config import configure_logging
from desktop_agent.permissions import ConfirmationChannel, ConfirmationDecision
from desktop_agent.provider_config import (
    ProviderConfigurationError,
    load_provider_config,
)
from desktop_agent.usage_budget import MonthlyUsageLedger, UsageLedgerError
from desktop_agent.voice import (
    VoiceController,
    VoiceError,
    VoiceResultStatus,
    VoiceState,
    VoiceUpdate,
    is_voice_cancel_command,
)
from desktop_agent.windows_session import WindowsSessionMonitor
from desktop_agent.windows_speech import WindowsSpeechBackend

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
    """Vista Tkinter del servicio; no contiene lógica de ejecución."""

    def __init__(
        self,
        root: object,
        service: DesktopAgentService,
        voice: VoiceController | None = None,
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._root = root
        self._service = service
        self._voice = voice
        self._tk = tk
        self._ttk = ttk
        self._closing = False
        self._history_ids: set[str] = set()

        root.title(f"Desktop Agent v{__version__}")
        root.geometry("940x700")
        root.minsize(760, 560)
        root.protocol("WM_DELETE_WINDOW", self._request_close)

        frame = ttk.Frame(root, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(6, weight=1)

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

        voice_frame = ttk.LabelFrame(frame, text="Voz local", padding=8)
        voice_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        voice_frame.columnconfigure(2, weight=1)
        self._voice_start = ttk.Button(
            voice_frame,
            text="Hablar una frase",
            command=self._start_voice,
            state="normal" if voice is not None else "disabled",
        )
        self._voice_start.grid(row=0, column=0, padx=(0, 6))
        self._voice_cancel = ttk.Button(
            voice_frame,
            text="Cancelar voz",
            command=self._cancel_voice,
            state="disabled",
        )
        self._voice_cancel.grid(row=0, column=1, padx=(0, 8))
        self._transcript = ttk.Entry(voice_frame)
        self._transcript.grid(row=0, column=2, sticky="ew", padx=(0, 8))
        self._voice_submit = ttk.Button(
            voice_frame,
            text="Enviar transcripción",
            command=self._submit_voice,
            state="normal" if voice is not None else "disabled",
        )
        self._voice_submit.grid(row=0, column=3)
        self._voice_status_text = tk.StringVar(
            value=(
                "Voz: audio local; el texto enviado usa la configuración de IA."
                if voice is not None
                else "Voz: backend no disponible."
            )
        )
        ttk.Label(
            voice_frame,
            textvariable=self._voice_status_text,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

        controls = ttk.Frame(frame)
        controls.grid(row=3, column=0, sticky="ew", pady=10)
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
        self._cancel = ttk.Button(
            controls,
            text="Cancelar seleccionada",
            command=self._cancel_selected,
        )
        self._cancel.grid(row=0, column=2, padx=(0, 8))
        self._resume = ttk.Button(
            controls,
            text="Reanudar",
            command=self._resume_service,
            state="disabled",
        )
        self._resume.grid(row=0, column=3, padx=(0, 8))
        self._close = ttk.Button(
            controls, text="Cerrar agente", command=self._request_close
        )
        self._close.grid(row=0, column=4)

        self._status_text = tk.StringVar(value="Estado: listo")
        self._usage_text = tk.StringVar(
            value=format_usage(service.snapshot.controller.monthly_usage)
        )
        self._latency_text = tk.StringVar(value="Latencia: sin medición")
        self._tool_text = tk.StringVar(value="Herramienta activa: ninguna")
        status = ttk.Frame(frame)
        status.grid(row=4, column=0, sticky="ew", pady=(0, 8))
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
        ttk.Label(status, textvariable=self._tool_text).grid(
            row=3, column=0, sticky="w"
        )

        confirmation = ttk.LabelFrame(
            frame,
            text="Confirmación",
            padding=8,
        )
        confirmation.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        confirmation.columnconfigure(0, weight=1)
        self._confirmation_text = tk.StringVar(
            value="Sin confirmaciones pendientes."
        )
        ttk.Label(
            confirmation,
            textvariable=self._confirmation_text,
        ).grid(row=0, column=0, sticky="w")
        self._approve = ttk.Button(
            confirmation,
            text="Aprobar una vez",
            command=lambda: self._resolve_confirmation(True),
            state="disabled",
        )
        self._approve.grid(row=0, column=1, padx=(8, 4))
        self._reject = ttk.Button(
            confirmation,
            text="Rechazar",
            command=lambda: self._resolve_confirmation(False),
            state="disabled",
        )
        self._reject.grid(row=0, column=2, padx=(4, 0))

        body = ttk.Panedwindow(frame, orient="horizontal")
        body.grid(row=6, column=0, sticky="nsew")
        history_frame = ttk.LabelFrame(body, text="Tareas", padding=8)
        result_frame = ttk.LabelFrame(body, text="Resultado y evidencia", padding=8)
        body.add(history_frame, weight=3)
        body.add(result_frame, weight=2)
        history_frame.columnconfigure(0, weight=1)
        history_frame.rowconfigure(0, weight=1)
        self._history = ttk.Treeview(
            history_frame,
            columns=("state", "tool", "label"),
            show="headings",
            selectmode="browse",
        )
        self._history.heading("state", text="Estado")
        self._history.heading("tool", text="Herramienta")
        self._history.heading("label", text="Tarea")
        self._history.column("state", width=115, stretch=False)
        self._history.column("tool", width=130, stretch=False)
        self._history.column("label", width=300, stretch=True)
        history_scrollbar = ttk.Scrollbar(
            history_frame,
            orient="vertical",
            command=self._history.yview,
        )
        self._history.configure(yscrollcommand=history_scrollbar.set)
        self._history.grid(row=0, column=0, sticky="nsew")
        history_scrollbar.grid(row=0, column=1, sticky="ns")
        self._history.bind("<<TreeviewSelect>>", lambda _: self._show_selected())
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
            request_id = self._service.submit(command)
        except (ServiceError, ValueError) as error:
            self._append(str(error))
            return
        self._command.delete(0, self._tk.END)
        self._status_text.set(f"Estado: orden {request_id} recibida")

    def _request_stop(self) -> None:
        self._service.request_stop()
        self._status_text.set("Estado: detención solicitada")

    def _request_emergency_stop(self) -> None:
        if self._voice is not None:
            self._voice.cancel()
        self._service.request_stop(emergency=True)
        self._status_text.set("Estado: EMERGENCIA solicitada")

    def _request_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._voice is not None:
            self._voice.cancel()
        self._set_controls_enabled(False)
        self._status_text.set("Estado: cerrando de forma segura")
        self._service.request_close()

    def _poll(self) -> None:
        if self._voice is not None:
            for update in self._voice.drain_updates():
                self._apply_voice_update(update)
        for update in self._service.poll():
            self._apply_update(update)
        snapshot = self._service.snapshot
        self._refresh_snapshot(snapshot)
        if self._closing and snapshot.state is ServiceState.CLOSED:
            self._root.destroy()
            return
        self._root.after(POLL_INTERVAL_MS, self._poll)

    def _start_voice(self) -> None:
        if self._voice is None:
            return
        if self._service.snapshot.state is not ServiceState.ACTIVE:
            self._append("La voz requiere un servicio local activo.")
            return
        try:
            self._voice.start()
        except VoiceError as error:
            self._append(str(error))

    def _cancel_voice(self) -> None:
        if self._voice is not None and self._voice.cancel():
            self._voice_status_text.set("Voz: cancelación solicitada.")

    def _submit_voice(self) -> None:
        transcript = self._transcript.get()
        try:
            task_id = self._service.submit_voice_transcript(transcript)
        except (ServiceError, ValueError) as error:
            self._append(str(error))
            return
        self._status_text.set(f"Estado: voz enviada como {task_id}")

    def _apply_voice_update(self, update: VoiceUpdate) -> None:
        if update.state is VoiceState.LISTENING:
            self._voice_status_text.set(
                "Voz: escuchando una frase; Cancelar voz detiene la captura."
            )
            self._voice_start.configure(state="disabled")
            self._voice_cancel.configure(state="normal")
            return
        self._voice_cancel.configure(state="disabled")
        result = update.result
        if result is None:
            return
        latency = (
            f"captura {result.capture_ms:.0f} ms · "
            f"transcripción {result.transcription_ms:.0f} ms · "
            f"total {update.total_ms:.0f} ms"
        )
        if result.status in {
            VoiceResultStatus.READY,
            VoiceResultStatus.AMBIGUOUS,
        }:
            assert result.transcript is not None
            self._transcript.delete(0, self._tk.END)
            self._transcript.insert(0, result.transcript)
            if (
                result.status is VoiceResultStatus.READY
                and is_voice_cancel_command(result.transcript)
            ):
                self._service.request_stop(emergency=True)
                self._transcript.delete(0, self._tk.END)
                self._voice_status_text.set(
                    f"Voz: emergencia solicitada · {latency}"
                )
                return
            prefix = (
                "Voz: revisá y corregí la transcripción ambigua"
                if result.status is VoiceResultStatus.AMBIGUOUS
                else "Voz: revisá la transcripción antes de enviarla"
            )
            self._voice_status_text.set(f"{prefix} · {latency}")
        elif result.status is VoiceResultStatus.NO_SPEECH:
            self._voice_status_text.set(f"Voz: no se detectó una frase · {latency}")
        elif result.status is VoiceResultStatus.CANCELLED:
            self._voice_status_text.set(f"Voz: captura cancelada · {latency}")
        elif result.status is VoiceResultStatus.UNAVAILABLE:
            self._voice_status_text.set(
                "Voz: Windows no tiene un reconocedor español disponible."
            )
        else:
            self._voice_status_text.set("Voz: fallo local redactado.")

    def _apply_update(self, update: ControllerUpdate) -> None:
        if update.kind is ControllerUpdateKind.PROGRESS:
            stage = {
                CommandStage.INTERPRETING: "interpretando",
                CommandStage.EXECUTING: "ejecutando",
                CommandStage.FINISHED: "finalizando",
            }.get(update.stage, "procesando")
            self._status_text.set(f"Estado: {stage}")
            self._tool_text.set(
                f"Herramienta activa: {update.tool_name or 'ninguna'}"
            )
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
            self._tool_text.set("Herramienta activa: ninguna")
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

    def _refresh_snapshot(self, snapshot: ServiceSnapshot) -> None:
        self._usage_text.set(format_usage(snapshot.controller.monthly_usage))
        self._tool_text.set(
            f"Herramienta activa: {snapshot.active_tool or 'ninguna'}"
        )
        self._refresh_history(snapshot.history)
        pending = snapshot.pending_confirmation
        if pending is None:
            self._confirmation_text.set("Sin confirmaciones pendientes.")
            self._approve.configure(state="disabled")
            self._reject.configure(state="disabled")
        else:
            request = pending.request
            destination = request.destination or "destino implícito"
            self._confirmation_text.set(
                f"{request.effect.value} · {request.risk_level.value} · "
                f"destino: {destination}"
            )
            self._approve.configure(state="normal")
            self._reject.configure(state="normal")
        suspended = snapshot.state is ServiceState.SUSPENDED
        if suspended:
            if self._voice is not None:
                self._voice.cancel()
            self._status_text.set(
                f"Estado: suspendido ({snapshot.suspension_reason})"
            )
        if not self._closing:
            self._set_service_controls(snapshot.state)

    def _refresh_history(
        self,
        history: tuple[TaskHistoryEntry, ...],
    ) -> None:
        current = {entry.task_id for entry in history}
        for removed in self._history_ids - current:
            self._history.delete(removed)
        for entry in history:
            values = (
                entry.state.value,
                entry.active_tool or "—",
                entry.label,
            )
            if entry.task_id in self._history_ids:
                self._history.item(entry.task_id, values=values)
            else:
                self._history.insert(
                    "",
                    "end",
                    iid=entry.task_id,
                    values=values,
                )
        self._history_ids = current

    def _show_selected(self) -> None:
        selection = self._history.selection()
        if not selection:
            return
        task_id = selection[0]
        entry = next(
            (
                item
                for item in self._service.snapshot.history
                if item.task_id == task_id
            ),
            None,
        )
        if entry is None:
            return
        evidence = entry.evidence
        displayed_tool = entry.active_tool or (
            evidence.tool_name if evidence is not None else None
        )
        lines = [
            f"Tarea: {entry.label}",
            f"Estado: {entry.state.value}",
            f"Herramienta: {displayed_tool or 'ninguna'}",
            f"Resultado: {entry.result or 'pendiente'}",
        ]
        if evidence is not None:
            total_time = (
                f"{evidence.total_ms} ms"
                if evidence.total_ms is not None
                else "no disponible"
            )
            lines.extend(
                (
                    "Éxito observable: "
                    f"{'sí' if evidence.observable_success else 'no'}",
                    f"Motivo: {evidence.reason}",
                    f"Tiempo total: {total_time}",
                )
            )
        self._set_result("\n".join(lines))

    def _cancel_selected(self) -> None:
        selection = self._history.selection()
        if not selection:
            self._append("Seleccioná una tarea para cancelarla.")
            return
        try:
            cancelled = self._service.cancel_task(selection[0])
        except ServiceError as error:
            self._append(str(error))
            return
        if not cancelled:
            self._append("La tarea ya no puede cancelarse.")

    def _resume_service(self) -> None:
        try:
            self._service.resume()
        except ServiceError as error:
            self._append(str(error))

    def _resolve_confirmation(self, approved: bool) -> None:
        pending = self._service.snapshot.pending_confirmation
        if pending is None:
            return
        request = pending.request
        decision = ConfirmationDecision(
            request.request_id,
            request.challenge,
            request.subject_fingerprint,
            approved,
            ConfirmationChannel.LOCAL,
        )
        try:
            self._service.resolve_confirmation(pending.task_id, decision)
        except ServiceError as error:
            self._append(str(error))

    def _append(self, message: str) -> None:
        self._result.configure(state="normal")
        if self._result.index("end-1c") != "1.0":
            self._result.insert("end", "\n\n")
        self._result.insert("end", message)
        self._result.see("end")
        self._result.configure(state="disabled")

    def _set_result(self, message: str) -> None:
        self._result.configure(state="normal")
        self._result.delete("1.0", "end")
        self._result.insert("1.0", message)
        self._result.configure(state="disabled")

    def _set_service_controls(self, state: ServiceState) -> None:
        active = state is ServiceState.ACTIVE
        suspended = state is ServiceState.SUSPENDED
        normal = "normal" if active else "disabled"
        self._command.configure(state=normal)
        self._execute.configure(state=normal)
        self._stop.configure(state=normal)
        self._emergency.configure(
            state="normal" if state is not ServiceState.CLOSED else "disabled"
        )
        self._cancel.configure(state=normal)
        self._resume.configure(state="normal" if suspended else "disabled")
        self._close.configure(
            state="disabled" if state is ServiceState.CLOSED else "normal"
        )
        voice_available = self._voice is not None
        listening = (
            voice_available and self._voice.state is VoiceState.LISTENING
        )
        self._voice_start.configure(
            state=(
                "normal"
                if active and voice_available and not listening
                else "disabled"
            )
        )
        self._voice_cancel.configure(
            state="normal" if listening else "disabled"
        )
        self._voice_submit.configure(
            state="normal" if active and voice_available else "disabled"
        )
        self._transcript.configure(
            state="normal" if active and voice_available else "disabled"
        )

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._command.configure(state=state)
        self._execute.configure(state=state)
        self._stop.configure(state=state)
        self._emergency.configure(state=state)
        self._cancel.configure(state=state)
        self._resume.configure(state=state)
        self._voice_start.configure(state=state)
        self._voice_cancel.configure(state=state)
        self._voice_submit.configure(state=state)
        self._transcript.configure(state=state)
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
    service: DesktopAgentService | None = None
    voice: VoiceController | None = None
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
        service = DesktopAgentService(
            controller,
            logger,
            session_monitor=WindowsSessionMonitor(),
        )
        voice = VoiceController(WindowsSpeechBackend(), logger)
        root = tk.Tk()
        TkDesktopAgentApp(root, service, voice)
        root.mainloop()
        voice_closed = voice.close(timeout=2.0)
        service_closed = service.close(timeout=10.0)
        return 0 if voice_closed and service_closed else 1
    except (OSError, tk.TclError) as error:
        print(f"No se pudo iniciar la interfaz: {error}", file=sys.stderr)
        return 1
    finally:
        if voice is not None:
            voice.close(timeout=1.0)
        if service is not None:
            service.close(timeout=1.0)
        elif controller is not None:
            controller.close(timeout=1.0)
        instance_lock.close()
