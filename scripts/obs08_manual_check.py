"""Demo local de OBS-08: crea y captura solo una ventana con datos ficticios."""

import logging
import tkinter as tk

from desktop_agent.observation import (
    ObservationError,
    ObservationLimits,
    ObservationService,
    RedactionRegion,
    encode_bmp,
)
from desktop_agent.windows_capture import WindowsWindowCaptureBackend

WINDOW_TITLE = "Desktop Agent OBS-08 - DATOS FICTICIOS"


def main() -> int:
    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.geometry("420x240+80+80")
    root.configure(background="#174a7e")
    tk.Label(
        root,
        text="CLIENTE FICTICIO: EJEMPLO 123",
        background="#174a7e",
        foreground="white",
        font=("Segoe UI", 14, "bold"),
    ).pack(pady=(36, 14))
    tk.Label(
        root,
        text="campo simulado que será redactado",
        background="#f4c542",
        foreground="black",
        padx=20,
        pady=10,
    ).pack()
    root.update_idletasks()
    root.update()

    service: ObservationService | None = None
    try:
        backend = WindowsWindowCaptureBackend()
        target = backend.find_window_exact(WINDOW_TITLE)
        service = ObservationService(
            backend,
            logging.getLogger("obs08-manual"),
            limits=ObservationLimits(
                max_width=640,
                max_height=480,
                max_pixels=307_200,
                max_captures=1,
            ),
        )
        redaction = RedactionRegion(100, 100, 220, 55)
        observation = service.capture(target, redactions=(redaction,))
        frame = service.read_frame(observation.observation_id, target)
        encoded = encode_bmp(frame)
        if not encoded.startswith(b"BM") or len(set(frame.pixels)) < 3:
            raise ObservationError("La imagen real no pudo validarse.")
        offset = redaction.y * frame.stride + redaction.x * 4
        if frame.pixels[offset : offset + 4] != b"\x00\x00\x00\xff":
            raise ObservationError("La redacción real no pudo validarse.")
        current_target = service.mark_state_changed(target)
        try:
            service.read_frame(observation.observation_id, current_target)
        except ObservationError:
            pass
        else:
            raise ObservationError("La captura obsoleta siguió disponible.")
        print(
            "OBS_MANUAL_OK: ventana ficticia capturada, redactada e "
            "invalidada sin persistir la imagen."
        )
        return 0
    except ObservationError as error:
        print(f"OBS_MANUAL_FAILED: {error}")
        return 1
    finally:
        if service is not None:
            service.close()
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
