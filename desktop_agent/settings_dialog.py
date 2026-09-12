"""Ventana de configuración; no prueba la API ni inicia grabaciones al abrirse."""

from desktop_agent.app_settings import AppSettings, AppSettingsStore, SettingsError
from desktop_agent.audio_capture import AudioCaptureError
from desktop_agent.audio_devices import list_input_devices


def show_settings(parent, settings: AppSettings, store: AppSettingsStore, on_saved) -> None:
    import tkinter as tk
    from tkinter import ttk

    window = tk.Toplevel(parent)
    window.title("Configuración · Desktop Agent")
    window.transient(parent)
    window.grab_set()
    window.resizable(False, False)
    body = ttk.Frame(window, padding=22)
    body.grid(sticky="nsew")
    body.columnconfigure(0, weight=1)
    ttk.Label(body, text="Dejá el asistente listo para usar", font=("Segoe UI", 15, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 12))
    ttk.Label(body, text="API key de OpenAI (se muestra enmascarada)").grid(row=1, column=0, sticky="w")
    key = tk.StringVar()
    entry = ttk.Entry(body, textvariable=key, show="•", width=67)
    entry.grid(row=2, column=0, sticky="ew", pady=5)
    key_message = "Hay una clave configurada. Dejá vacío para conservarla." if settings.api_key else "Pegá la clave completa con Ctrl+V."
    ttk.Label(body, text=key_message).grid(row=3, column=0, sticky="w")
    forget = tk.BooleanVar(value=False)
    remember = tk.BooleanVar(value=settings.remember_key)
    ttk.Checkbutton(body, text="Recordar la clave cifrada para este usuario de Windows", variable=remember).grid(row=4, column=0, sticky="w", pady=(6, 0))
    ttk.Checkbutton(body, text="Quitar la clave guardada y de esta sesión", variable=forget).grid(row=5, column=0, sticky="w")
    ai = tk.BooleanVar(value=settings.ai_enabled)
    voice = tk.BooleanVar(value=settings.voice_enabled)
    autosend = tk.BooleanVar(value=settings.auto_send_voice)
    hotkey = tk.BooleanVar(value=settings.voice_hotkey)
    for row, label, value in (
        (6, "Interpretar frases libres con IA", ai),
        (7, "Transcribir mi voz con OpenAI al pulsar Hablar o el atajo", voice),
        (8, "Enviar automáticamente la transcripción como orden", autosend),
        (9, "Activar Ctrl+Alt+Espacio (voz) y Ctrl+Alt+Esc (emergencia)", hotkey),
    ):
        ttk.Checkbutton(body, text=label, variable=value).grid(row=row, column=0, sticky="w", pady=3)
    ttk.Label(body, text="El envío automático conserva las confirmaciones de acciones sensibles.", foreground="#475569").grid(row=10, column=0, sticky="w", pady=(0, 10))
    ttk.Label(body, text="Micrófono").grid(row=11, column=0, sticky="w")
    devices = ()
    device_error = ""
    try:
        devices = list_input_devices()
    except AudioCaptureError as error:
        device_error = str(error)
    choices = {"Predeterminado de Windows": None}
    for device in devices:
        choices[f"{device.label} · #{device.index}"] = device.key
    initial = next((label for label, value in choices.items() if value == settings.microphone), None)
    if initial is None:
        initial = "Micrófono guardado (desconectado)"
        choices[initial] = settings.microphone
    selected = tk.StringVar(value=initial)
    ttk.Combobox(body, textvariable=selected, values=tuple(choices), state="readonly", width=65).grid(row=12, column=0, sticky="ew", pady=5)
    ttk.Label(body, text=device_error or "La selección se resuelve de nuevo al conectar o desconectar dispositivos.", wraplength=580).grid(row=13, column=0, sticky="w")
    budget_row = ttk.Frame(body)
    budget_row.grid(row=14, column=0, sticky="w", pady=14)
    ttk.Label(budget_row, text="Tope mensual compartido · USD").pack(side="left", padx=(0, 8))
    budget = tk.StringVar(value=f"{settings.monthly_budget_usd:.2f}")
    ttk.Entry(budget_row, textvariable=budget, width=10).pack(side="left")
    ttk.Label(body, text="Guardar reinicia el asistente cuando está libre; el consumo acumulado se conserva.", wraplength=580).grid(row=15, column=0, sticky="w")
    error_text = tk.StringVar()
    ttk.Label(body, textvariable=error_text, foreground="#b91c1c", wraplength=580).grid(row=16, column=0, sticky="w", pady=8)

    def save() -> None:
        try:
            credential = None if forget.get() else (key.get().strip() or settings.api_key)
            if (ai.get() or voice.get()) and not credential:
                raise SettingsError("Agregá una clave o desactivá IA y transcripción de voz.")
            updated = AppSettings(
                ai_enabled=ai.get(), voice_enabled=voice.get(),
                monthly_budget_usd=float(budget.get().replace(",", ".")),
                microphone=choices[selected.get()], auto_send_voice=autosend.get(),
                voice_hotkey=hotkey.get(), remember_key=remember.get() and credential is not None,
                api_key=credential,
            )
            store.save(updated)
        except (SettingsError, ValueError) as error:
            error_text.set(str(error) if isinstance(error, SettingsError) else "Ingresá un importe válido para el tope mensual.")
            return
        key.set("")
        window.destroy()
        on_saved(updated)

    buttons = ttk.Frame(body)
    buttons.grid(row=17, column=0, sticky="e")
    ttk.Button(buttons, text="Cancelar", command=window.destroy).pack(side="left", padx=6)
    ttk.Button(buttons, text="Guardar y aplicar", command=save).pack(side="left")
    entry.focus_set()
