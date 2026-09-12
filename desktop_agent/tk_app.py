import os
import sys
from collections.abc import Mapping

from desktop_agent import __version__
from desktop_agent.app_settings import AppSettings, AppSettingsStore, SettingsError
from desktop_agent.audio_devices import create_recorder
from desktop_agent.hotkeys import GlobalHotkeys
from desktop_agent.settings_dialog import show_settings
from desktop_agent.audio_capture import SoundDeviceWavRecorder
from desktop_agent.browser_bridge import (
    BrowserBridgeSnapshot,
    BrowserBridgeState,
    BrowserKind,
)
from desktop_agent.browser_preferences import (
    BrowserPreference,
    BrowserPreferenceError,
    PreferredBrowser,
)
from desktop_agent.browser_runtime import BrowserRuntime
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
from desktop_agent.openai_voice import (
    BudgetedOpenAIVoiceBackend,
    OpenAITranscriptionProvider,
)
from desktop_agent.permissions import ConfirmationChannel, ConfirmationDecision
from desktop_agent.usage_budget import MonthlyUsageLedger, UsageLedgerError
from desktop_agent.voice import (
    VoiceController,
    VoiceError,
    VoiceFailureReason,
    VoiceResultStatus,
    VoiceState,
    VoiceUpdate,
    is_voice_cancel_command,
)
from desktop_agent.voice_transcription_config import (
    VoiceTranscriptionConfig,
    VoiceTranscriptionConfigurationError,
    load_voice_transcription_config,
)
from desktop_agent.windows_session import WindowsSessionMonitor

POLL_INTERVAL_MS = 80
BROWSER_LABELS = {
    PreferredBrowser.SYSTEM_DEFAULT: "Predeterminado de Windows",
    PreferredBrowser.CHROME: "Google Chrome",
    PreferredBrowser.OPERA_GX: "Opera GX",
}


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


def format_voice_failure(reason: VoiceFailureReason | None) -> str:
    messages = {
        VoiceFailureReason.PROVIDER_SETUP: (
            "no se pudo iniciar el cliente de OpenAI; revisá la instalación."
        ),
        VoiceFailureReason.AUTHENTICATION: (
            "la API key es inválida, fue revocada o pertenece a otro proyecto."
        ),
        VoiceFailureReason.PERMISSION: (
            "la API key o el proyecto no tienen permiso para transcribir audio."
        ),
        VoiceFailureReason.MODEL_UNAVAILABLE: (
            "gpt-transcribe no está disponible para este proyecto."
        ),
        VoiceFailureReason.QUOTA_OR_RATE_LIMIT: (
            "la cuenta no tiene cuota o créditos de API disponibles, o alcanzó "
            "un límite temporal."
        ),
        VoiceFailureReason.REQUEST_REJECTED: (
            "OpenAI rechazó el audio o los parámetros de la solicitud."
        ),
        VoiceFailureReason.TIMEOUT: (
            "OpenAI no respondió antes del límite de tiempo."
        ),
        VoiceFailureReason.NETWORK: (
            "no se pudo conectar con OpenAI; revisá la conexión."
        ),
        VoiceFailureReason.SERVICE_UNAVAILABLE: (
            "OpenAI informó una falla temporal del servicio."
        ),
        VoiceFailureReason.INVALID_RESPONSE: (
            "OpenAI respondió sin una transcripción utilizable."
        ),
        VoiceFailureReason.UNKNOWN: (
            "la transcripción externa falló por una causa no identificada."
        ),
        None: "la transcripción externa falló por una causa no identificada.",
    }
    return f"Voz: {messages.get(reason, messages[None])}"


def format_browser_bridge(
    snapshot: BrowserBridgeSnapshot,
    preference: BrowserPreference,
) -> str:
    if not preference.use_current_session:
        return "Sesión web: apertura directa; extensión no seleccionada."
    expected = {
        PreferredBrowser.CHROME: BrowserKind.CHROME,
        PreferredBrowser.OPERA_GX: BrowserKind.OPERA_GX,
    }[preference.browser]
    if snapshot.state is BrowserBridgeState.CONNECTED:
        if snapshot.browser is expected:
            return f"Sesión web: extensión conectada a {BROWSER_LABELS[preference.browser]}."
        connected = {
            BrowserKind.CHROME: "Google Chrome",
            BrowserKind.OPERA_GX: "Opera GX",
        }.get(snapshot.browser, "otro navegador")
        return f"Sesión web: extensión conectada a {connected}; falta el elegido."
    if snapshot.state is BrowserBridgeState.WAITING:
        return "Sesión web: esperando que la extensión se conecte."
    if snapshot.state is BrowserBridgeState.ERROR:
        return "Sesión web: el puente local no pudo iniciarse o se interrumpió."
    if snapshot.state is BrowserBridgeState.CLOSED:
        return "Sesión web: puente cerrado."
    return "Sesión web: puente local sin iniciar."


def _close_optional_voice(
    voice: VoiceController | None,
    timeout: float,
) -> bool:
    return True if voice is None else voice.close(timeout=timeout)


class TkDesktopAgentApp:
    """Vista Tkinter del servicio; no contiene lógica de ejecución."""

    def __init__(
        self,
        root: object,
        service: DesktopAgentService,
        voice: VoiceController | None = None,
        browser_runtime: BrowserRuntime | None = None,
        *,
        settings: AppSettings | None = None,
        settings_store: AppSettingsStore | None = None,
        recorder: SoundDeviceWavRecorder | None = None,
        hotkeys: GlobalHotkeys | None = None,
        settings_warning: str | None = None,
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._root = root
        self._service = service
        self._voice = voice
        self._browser_runtime = browser_runtime
        self._tk = tk
        self._ttk = ttk
        self._closing = False
        self._history_ids: set[str] = set()
        self._settings = settings or AppSettings()
        self._settings_store = settings_store
        self._recorder = recorder
        self._hotkeys = hotkeys
        self._voice_capture_id: str | None = None
        self._handled_captures: set[str] = set()
        self.next_settings: AppSettings | None = None

        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background="#f4f6fa", foreground="#192538")
        style.configure("TButton", padding=(10, 6))
        style.configure("TEntry", padding=6, fieldbackground="white")
        style.configure("Treeview", rowheight=28, background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("Accent.TButton", background="#245bd7", foreground="white")
        style.map("Accent.TButton", background=[("active", "#1647b4")])

        root.title(f"Desktop Agent v{__version__}")
        root.geometry("1100x860")
        root.minsize(900, 720)
        root.protocol("WM_DELETE_WINDOW", self._request_close)

        frame = ttk.Frame(root, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(7, weight=1)

        heading = ttk.Frame(frame)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(heading, text="¿Qué querés hacer?", font=("Segoe UI", 20, "bold")).pack(side="left")
        self._settings_button = ttk.Button(heading, text="Configuración", command=self._open_settings, state="normal" if settings_store else "disabled")
        self._settings_button.pack(side="right")
        ttk.Button(heading, text="Ejemplos", command=self._show_examples).pack(side="right", padx=8)
        command_row = ttk.Frame(frame)
        command_row.grid(row=1, column=0, sticky="ew")
        command_row.columnconfigure(0, weight=1)
        self._command = ttk.Entry(command_row)
        self._command.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._command.bind("<Return>", lambda _: self._submit())
        self._execute = ttk.Button(
            command_row, text="Ejecutar", command=self._submit, style="Accent.TButton"
        )
        self._execute.grid(row=0, column=1)

        voice_frame = ttk.LabelFrame(frame, text="Voz", padding=10)
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
        self._transcript.bind("<Return>", lambda _: self._submit_voice())
        self._voice_submit = ttk.Button(
            voice_frame,
            text="Enviar transcripción",
            command=self._submit_voice,
            state="normal" if voice is not None else "disabled",
        )
        self._voice_submit.grid(row=0, column=3)
        self._voice_status_text = tk.StringVar(
            value=(
                "Voz: al pulsar, el audio se envía a OpenAI para transcribir."
                if voice is not None
                else "Voz: transcripción externa no configurada."
            )
        )
        ttk.Label(
            voice_frame,
            textvariable=self._voice_status_text,
            wraplength=990,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        voice_tools = ttk.Frame(voice_frame)
        voice_tools.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        self._voice_finish = ttk.Button(voice_tools, text="Terminé de hablar", command=self._finish_voice, state="disabled")
        self._voice_finish.pack(side="left")
        self._audio_level = ttk.Progressbar(voice_tools, maximum=100, length=130)
        self._audio_level.pack(side="left", padx=10)
        self._voice_mode = tk.StringVar(value="Envío automático" if self._settings.auto_send_voice else "Revisar y enviar")
        ttk.Label(voice_tools, textvariable=self._voice_mode).pack(side="left")
        self._hotkey_text = tk.StringVar(value="Atajo desactivado · activalo en Configuración")
        ttk.Label(voice_frame, textvariable=self._hotkey_text, foreground="#475569").grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

        browser_frame = ttk.LabelFrame(frame, text="Navegador web", padding=8)
        browser_frame.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        browser_frame.columnconfigure(1, weight=1)
        ttk.Label(browser_frame, text="Abrir con").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        initial_preference = BrowserPreference()
        browser_error: str | None = None
        if browser_runtime is not None:
            try:
                initial_preference = browser_runtime.preferences.load()
            except BrowserPreferenceError:
                browser_error = "Sesión web: configuración dañada; no se modificó."
        self._saved_browser_preference = initial_preference
        self._browser_choice = tk.StringVar(
            value=BROWSER_LABELS[initial_preference.browser]
        )
        self._browser_combo = ttk.Combobox(
            browser_frame,
            textvariable=self._browser_choice,
            values=tuple(BROWSER_LABELS.values()),
            state="readonly" if browser_runtime is not None else "disabled",
        )
        self._browser_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self._browser_combo.bind(
            "<<ComboboxSelected>>",
            lambda _: self._browser_preference_changed(),
        )
        self._browser_session = tk.BooleanVar(
            value=initial_preference.use_current_session
        )
        self._browser_session_check = ttk.Checkbutton(
            browser_frame,
            text="Usar una pestaña gestionada de mi sesión actual",
            variable=self._browser_session,
            command=self._save_browser_preference,
        )
        self._browser_session_check.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(6, 0),
        )
        self._browser_save = ttk.Button(
            browser_frame,
            text="Guardar",
            command=self._save_browser_preference,
            state="normal" if browser_runtime is not None else "disabled",
        )
        self._browser_save.grid(row=0, column=2)
        self._browser_status_text = tk.StringVar(
            value=browser_error
            or (
                format_browser_bridge(browser_runtime.snapshot, initial_preference)
                if browser_runtime is not None
                else "Sesión web: configuración no disponible."
            )
        )
        ttk.Label(
            browser_frame,
            textvariable=self._browser_status_text,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._sync_browser_session_option()

        controls = ttk.Frame(frame)
        controls.grid(row=4, column=0, sticky="ew", pady=10)
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
        status.grid(row=5, column=0, sticky="ew", pady=(0, 8))
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
        confirmation.grid(row=6, column=0, sticky="ew", pady=(0, 8))
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
        body.grid(row=7, column=0, sticky="nsew")
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
        self._history.column("tool", width=145, stretch=False)
        self._history.column("label", width=290, minwidth=130, stretch=True)
        history_scrollbar = ttk.Scrollbar(
            history_frame,
            orient="vertical",
            command=self._history.yview,
        )
        self._history.configure(yscrollcommand=history_scrollbar.set)
        self._history.grid(row=0, column=0, sticky="nsew")
        history_scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scroll = ttk.Scrollbar(history_frame, orient="horizontal", command=self._history.xview)
        self._history.configure(xscrollcommand=horizontal_scroll.set)
        horizontal_scroll.grid(row=1, column=0, sticky="ew")
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
        self._show_examples()
        if settings_warning:
            self._append(settings_warning)
        root.after(POLL_INTERVAL_MS, self._poll)

    def _show_examples(self) -> None:
        self._set_result(
            "Probá con una orden\n\n"
            "• Abrí calculadora\n• Abrí Steam\n• Abrí VoiceMeeter Banana\n"
            "• Abrí League of Legends\n• Abrí God of War Ragnarok\n"
            "• Poné en YouTube qué tan malo puedo ser\n• Detener YouTube\n"
            "• Abrí Spotify app (instalado) o abrir Spotify (web)\n\n"
            "Con IA activada también podés decirlo con tus palabras.\n"
            "Configuración permite elegir el micrófono, recordar la clave y cambiar el tope mensual.\n\n"
            "La app puede abrir esos programas; todavía no controla su interior ni reproduce playlists de Spotify."
        )

    def _open_settings(self) -> None:
        snapshot = self._service.snapshot.controller
        if self._closing or self._settings_store is None or self._root.grab_current() is not None:
            return
        if snapshot.state is not ControllerState.IDLE or snapshot.pending_commands or (self._voice and self._voice.state is VoiceState.LISTENING):
            self._append("Esperá a que termine la orden o cancelala antes de cambiar la configuración.")
            return
        def saved(settings: AppSettings) -> None:
            self.next_settings = settings
            self._request_close()

        show_settings(self._root, self._settings, self._settings_store, saved)

    def _finish_voice(self) -> None:
        if self._recorder is not None:
            self._recorder.finish()

    def _browser_preference_changed(self) -> None:
        self._sync_browser_session_option()
        self._save_browser_preference()

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
        self._cancel_voice()
        self._service.request_stop()
        self._status_text.set("Estado: detención solicitada")

    def _request_emergency_stop(self) -> None:
        self._cancel_voice()
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
        if self._hotkeys is not None:
            self._hotkey_text.set(self._hotkeys.status)
            for event in self._hotkeys.drain():
                if event == "emergency":
                    self._request_emergency_stop()
                elif not self._closing and self._root.grab_current() is None:
                    if self._recorder is not None and self._recorder.recording:
                        self._finish_voice()
                    elif self._voice is not None and self._voice.state is not VoiceState.LISTENING:
                        self._start_voice()
        if self._voice is not None:
            for update in self._voice.drain_updates():
                if not self._closing:
                    self._apply_voice_update(update)
        if self._recorder is not None:
            self._audio_level.configure(value=self._recorder.level * 100)
            self._voice_finish.configure(state="normal" if self._recorder.recording and not self._closing else "disabled")
            if self._voice is not None and self._voice.state is VoiceState.LISTENING and self._recorder.phase == "finished":
                self._voice_status_text.set("Voz: transcribiendo con OpenAI… Podés cancelar sin ejecutar la orden.")
        for update in self._service.poll():
            self._apply_update(update)
        snapshot = self._service.snapshot
        self._refresh_snapshot(snapshot)
        self._refresh_browser_status()
        if self._closing and snapshot.state is ServiceState.CLOSED:
            self._root.destroy()
            return
        self._root.after(POLL_INTERVAL_MS, self._poll)

    def _selected_browser(self) -> PreferredBrowser:
        selected = self._browser_choice.get()
        return next(
            browser
            for browser, label in BROWSER_LABELS.items()
            if label == selected
        )

    def _sync_browser_session_option(self) -> None:
        enabled = (
            self._browser_runtime is not None
            and self._selected_browser() is not PreferredBrowser.SYSTEM_DEFAULT
            and not self._closing
        )
        if not enabled:
            self._browser_session.set(False)
        self._browser_session_check.configure(
            state="normal" if enabled else "disabled"
        )

    def _current_browser_preference(self) -> BrowserPreference:
        return BrowserPreference(
            self._selected_browser(),
            bool(self._browser_session.get()),
        )

    def _save_browser_preference(self) -> None:
        if self._browser_runtime is None or self._closing:
            return
        try:
            preference = self._current_browser_preference()
            self._browser_runtime.preferences.save(preference)
        except BrowserPreferenceError as error:
            self._browser_status_text.set(f"Sesión web: {error}")
            return
        self._saved_browser_preference = preference
        self._browser_status_text.set(
            format_browser_bridge(self._browser_runtime.snapshot, preference)
        )

    def _refresh_browser_status(self) -> None:
        if self._browser_runtime is None:
            return
        self._browser_status_text.set(
            format_browser_bridge(
                self._browser_runtime.snapshot,
                self._saved_browser_preference,
            )
        )

    def _start_voice(self) -> None:
        if self._voice is None or self._closing:
            return
        if self._service.snapshot.state is not ServiceState.ACTIVE:
            self._append("La voz requiere un servicio local activo.")
            return
        try:
            self._transcript.delete(0, self._tk.END)
            if self._recorder is not None:
                self._recorder.phase = "starting"
            self._voice_capture_id = self._voice.start()
        except VoiceError as error:
            self._append(str(error))

    def _cancel_voice(self) -> None:
        self._voice_capture_id = None
        self._transcript.delete(0, self._tk.END)
        if self._voice is not None:
            self._voice.cancel()
            self._voice_status_text.set("Voz: cancelación solicitada.")

    def _submit_voice(self) -> bool:
        if self._closing or (self._voice is not None and self._voice.state is VoiceState.LISTENING):
            return False
        transcript = self._transcript.get()
        try:
            task_id = self._service.submit_voice_transcript(transcript)
        except (ServiceError, ValueError) as error:
            self._append(str(error))
            return False
        self._status_text.set(f"Estado: voz enviada como {task_id}")
        self._transcript.delete(0, self._tk.END)
        return True

    def _apply_voice_update(self, update: VoiceUpdate) -> None:
        if update.capture_id != self._voice_capture_id or update.capture_id in self._handled_captures:
            return
        if update.state is VoiceState.LISTENING:
            self._voice_status_text.set(
                "Voz: capturando una frase; al finalizar se enviará a OpenAI."
            )
            self._voice_start.configure(state="disabled")
            self._voice_cancel.configure(state="normal")
            return
        self._voice_cancel.configure(state="disabled")
        result = update.result
        if result is None:
            return
        self._handled_captures = {update.capture_id}
        latency = (
            f"captura {result.capture_ms:.0f} ms · "
            f"transcripción {result.transcription_ms:.0f} ms · "
            f"total {update.total_ms:.0f} ms"
        )
        if result.estimated_cost_usd is not None:
            latency += f" · API USD {result.estimated_cost_usd:.6f}"
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
            if self._settings.auto_send_voice and result.status is VoiceResultStatus.READY and self._root.grab_current() is None:
                if self._submit_voice():
                    self._voice_status_text.set(f"Voz: transcripción enviada · {latency}")
        elif result.status is VoiceResultStatus.NO_SPEECH:
            self._voice_status_text.set(f"Voz: no se detectó una frase · {latency}")
        elif result.status is VoiceResultStatus.CANCELLED:
            self._voice_status_text.set(f"Voz: captura cancelada · {latency}")
        elif result.status is VoiceResultStatus.UNAVAILABLE:
            self._voice_status_text.set(
                "Voz: no se pudo acceder al micrófono."
            )
        elif result.status is VoiceResultStatus.BUDGET_EXCEEDED:
            self._voice_status_text.set(
                "Voz: el presupuesto mensual no permite otra transcripción."
            )
        else:
            self._voice_status_text.set(
                f"{format_voice_failure(result.failure_reason)} · {latency}"
            )

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
                {"queued": "En cola", "running": "En curso", "succeeded": "Completada", "failed": "Falló", "cancelled": "Cancelada"}.get(entry.state.value, entry.state.value),
                entry.active_tool or (entry.evidence.tool_name if entry.evidence else None) or "—",
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
            state="normal" if active and voice_available and not listening and self._transcript.get().strip() else "disabled"
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
        self._browser_combo.configure(
            state="readonly" if enabled and self._browser_runtime else "disabled"
        )
        self._browser_save.configure(
            state="normal" if enabled and self._browser_runtime else "disabled"
        )
        if enabled:
            self._sync_browser_session_option()
        else:
            self._browser_session_check.configure(state="disabled")
        self._close.configure(state=state)


def run_gui(environ: Mapping[str, str] | None = None) -> int:
    base = dict(os.environ if environ is None else environ)
    store = AppSettingsStore()
    warning = None
    try:
        settings = store.load() if store.path.exists() else AppSettings.from_environment(base)
    except SettingsError as error:
        settings = AppSettings()
        warning = str(error) + " Abrí Configuración para corregirla."
    while True:
        restart: list[AppSettings] = []
        result = _run_gui_session(settings.environment(base), settings, store, restart, warning)
        if result != 0 or not restart:
            return result
        settings = restart[0]
        warning = None


def _run_gui_session(environ, settings, settings_store, restart, settings_warning=None) -> int:
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
    browser_runtime: BrowserRuntime | None = None
    hotkeys: GlobalHotkeys | None = None
    try:
        logger = configure_logging()
        from desktop_agent.cli import build_executor, build_interpreter

        source = os.environ if environ is None else environ
        startup_warnings = [settings_warning] if settings_warning else []
        voice_config: VoiceTranscriptionConfig | None
        try:
            loaded_voice_config = load_voice_transcription_config(source)
            voice_config = (
                loaded_voice_config if loaded_voice_config.enabled else None
            )
        except VoiceTranscriptionConfigurationError:
            startup_warnings.append(
                "Configuración de voz externa inválida; la voz queda deshabilitada.",
            )
            voice_config = None

        monthly_budget = settings.monthly_budget_usd
        usage_ledger = MonthlyUsageLedger(monthly_budget)
        try:
            initial_usage = usage_ledger.current_snapshot()
        except UsageLedgerError:
            initial_usage = None

        browser_runtime = BrowserRuntime()
        browser_runtime.start()
        executor, playback_controller = build_executor(
            logger,
            browser_preferences=browser_runtime.preferences,
            browser_bridge=browser_runtime.bridge,
        )
        interpreter = build_interpreter(
            source,
            budget_factory=lambda _config: usage_ledger,
            warning_output=startup_warnings.append,
        )
        processor = CommandProcessor(executor, interpreter, logger)
        controller = LocalAgentController(
            processor,
            playback_controller,
            logger,
            initial_usage=initial_usage,
        )
        service = DesktopAgentService(
            controller,
            logger,
            session_monitor=WindowsSessionMonitor(),
        )
        recorder = None
        if voice_config is not None:
            recorder = create_recorder(settings.microphone)
            voice_backend = BudgetedOpenAIVoiceBackend(
                recorder,
                OpenAITranscriptionProvider(voice_config),
                usage_ledger,
                logger,
                usage_callback=controller.update_monthly_usage,
            )
            voice = VoiceController(voice_backend, logger)
        if settings.voice_hotkey:
            hotkeys = GlobalHotkeys()
            hotkeys.start()
        root = tk.Tk()
        app = TkDesktopAgentApp(root, service, voice, browser_runtime,
            settings=settings, settings_store=settings_store, recorder=recorder,
            hotkeys=hotkeys, settings_warning="\n".join(startup_warnings) or None)
        root.mainloop()
        voice_closed = _close_optional_voice(voice, timeout=2.0)
        service_closed = service.close(timeout=10.0)
        if voice_closed and service_closed and app.next_settings is not None:
            restart.append(app.next_settings)
        return 0 if voice_closed and service_closed else 1
    except (OSError, tk.TclError) as error:
        print(f"No se pudo iniciar la interfaz: {error}", file=sys.stderr)
        return 1
    finally:
        if hotkeys is not None:
            hotkeys.close()
        if voice is not None:
            voice.close(timeout=1.0)
        if service is not None:
            service.close(timeout=1.0)
        elif controller is not None:
            controller.close(timeout=1.0)
        if browser_runtime is not None:
            browser_runtime.close()
        instance_lock.close()
