"""Ventana de configuración; no prueba la API ni inicia grabaciones al abrirse."""

import webbrowser

from desktop_agent.app_settings import AppSettings, AppSettingsStore, SettingsError
from desktop_agent.audio_capture import AudioCaptureError
from desktop_agent.audio_devices import list_input_devices
from desktop_agent.spotify import SPOTIFY_REDIRECT_URI


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
    spotify_box = ttk.LabelFrame(body, text="Spotify", padding=10)
    spotify_box.grid(row=14, column=0, sticky="ew", pady=(10, 0))
    spotify_box.columnconfigure(0, weight=1)
    spotify_enabled = tk.BooleanVar(value=settings.spotify_enabled)
    ttk.Checkbutton(
        spotify_box,
        text="Controlar reproducción con mi cuenta de Spotify Premium",
        variable=spotify_enabled,
    ).grid(row=0, column=0, sticky="w")
    ttk.Label(spotify_box, text="Client ID de Spotify Developer").grid(
        row=1, column=0, sticky="w", pady=(7, 0)
    )
    spotify_client_id = tk.StringVar(value=settings.spotify_client_id or "")
    ttk.Entry(spotify_box, textvariable=spotify_client_id, width=67).grid(
        row=2, column=0, sticky="ew", pady=4
    )
    ttk.Button(
        spotify_box,
        text="Abrir Spotify Developer",
        command=lambda: webbrowser.open("https://developer.spotify.com/dashboard"),
    ).grid(row=2, column=1, padx=(8, 0))
    ttk.Label(
        spotify_box,
        text=f"Redirect URI exacta: {SPOTIFY_REDIRECT_URI}",
        foreground="#475569",
    ).grid(row=3, column=0, columnspan=2, sticky="w")
    ttk.Label(spotify_box, text="Dispositivo preferido (opcional, nombre exacto)").grid(
        row=4, column=0, sticky="w", pady=(7, 0)
    )
    spotify_device = tk.StringVar(value=settings.spotify_device_name or "")
    ttk.Entry(spotify_box, textvariable=spotify_device, width=67).grid(
        row=5, column=0, sticky="ew", pady=4
    )
    spotify_disconnect = tk.BooleanVar(value=False)
    connected_text = (
        "Cuenta autorizada. La primera orden renovará la sesión automáticamente."
        if settings.spotify_refresh_token
        else "La primera orden abrirá Spotify para que autorices la cuenta una sola vez."
    )
    ttk.Label(spotify_box, text=connected_text, wraplength=580).grid(
        row=6, column=0, columnspan=2, sticky="w"
    )
    ttk.Checkbutton(
        spotify_box,
        text="Desconectar la cuenta de Spotify guardada",
        variable=spotify_disconnect,
        state="normal" if settings.spotify_refresh_token else "disabled",
    ).grid(row=7, column=0, columnspan=2, sticky="w")

    budget_row = ttk.Frame(body)
    budget_row.grid(row=15, column=0, sticky="w", pady=14)
    ttk.Label(budget_row, text="Tope mensual compartido · USD").pack(side="left", padx=(0, 8))
    budget = tk.StringVar(value=f"{settings.monthly_budget_usd:.2f}")
    ttk.Entry(budget_row, textvariable=budget, width=10).pack(side="left")
    ttk.Label(body, text="Guardar reinicia el asistente cuando está libre; el consumo acumulado se conserva.", wraplength=580).grid(row=16, column=0, sticky="w")
    error_text = tk.StringVar()
    ttk.Label(body, textvariable=error_text, foreground="#b91c1c", wraplength=580).grid(row=17, column=0, sticky="w", pady=8)

    def save() -> None:
        try:
            credential = None if forget.get() else (key.get().strip() or settings.api_key)
            if (ai.get() or voice.get()) and not credential:
                raise SettingsError("Agregá una clave o desactivá IA y transcripción de voz.")
            client_id = spotify_client_id.get().strip() or None
            device_name = spotify_device.get().strip() or None
            spotify_token = (
                None
                if spotify_disconnect.get() or client_id != settings.spotify_client_id
                else settings.spotify_refresh_token
            )
            updated = AppSettings(
                ai_enabled=ai.get(), voice_enabled=voice.get(),
                monthly_budget_usd=float(budget.get().replace(",", ".")),
                microphone=choices[selected.get()], auto_send_voice=autosend.get(),
                voice_hotkey=hotkey.get(), remember_key=remember.get() and credential is not None,
                api_key=credential,
                spotify_enabled=spotify_enabled.get(),
                spotify_client_id=client_id,
                spotify_device_name=device_name,
                spotify_refresh_token=spotify_token,
            )
            store.save(updated)
        except (SettingsError, ValueError) as error:
            error_text.set(str(error) if isinstance(error, SettingsError) else "Ingresá un importe válido para el tope mensual.")
            return
        key.set("")
        window.destroy()
        on_saved(updated)

    buttons = ttk.Frame(body)
    buttons.grid(row=18, column=0, sticky="e")
    ttk.Button(buttons, text="Cancelar", command=window.destroy).pack(side="left", padx=6)
    ttk.Button(buttons, text="Guardar y aplicar", command=save).pack(side="left")
    entry.focus_set()
