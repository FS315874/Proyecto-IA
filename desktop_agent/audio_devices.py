"""Selección explícita y estable del micrófono, sin capturar audio al enumerar."""

from dataclasses import dataclass

from desktop_agent.audio_capture import AudioCaptureError, SoundDeviceWavRecorder


@dataclass(frozen=True)
class InputDevice:
    index: int
    key: str
    label: str
    sample_rate: int


def list_input_devices() -> tuple[InputDevice, ...]:
    try:
        import sounddevice as sd
        hosts = sd.query_hostapis()
        result = []
        for index, device in enumerate(sd.query_devices()):
            if device["max_input_channels"] > 0:
                host = hosts[device["hostapi"]]["name"]
                name = device["name"]
                result.append(InputDevice(index, f"{host}::{name}", f"{name} ({host})", round(device["default_samplerate"])))
        return tuple(result)
    except Exception:
        raise AudioCaptureError("No se pudo consultar la lista de micrófonos de Windows.") from None


def resolve_input_device(key: str, devices: tuple[InputDevice, ...]) -> InputDevice:
    matches = [device for device in devices if device.key == key]
    if len(matches) != 1:
        raise AudioCaptureError("El micrófono elegido ya no está disponible o es ambiguo. Volvé a seleccionarlo.")
    return matches[0]


def create_recorder(microphone: str | None, **kwargs) -> SoundDeviceWavRecorder:
    if microphone is None:
        return SoundDeviceWavRecorder(**kwargs)

    # Resolvemos al capturar, porque conectar un USB puede cambiar los índices.
    selected: list[InputDevice] = []

    def sample_rate() -> int:
        selected[:] = [resolve_input_device(microphone, list_input_devices())]
        return selected[0].sample_rate

    def stream(rate: int, block_size: int):
        import sounddevice as sd
        return sd.RawInputStream(device=selected[0].index, samplerate=rate, blocksize=block_size, channels=1, dtype="int16")

    return SoundDeviceWavRecorder(sample_rate_provider=sample_rate, stream_factory=stream, **kwargs)
