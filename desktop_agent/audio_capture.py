import io
import math
import threading
import time
import wave
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


SAMPLE_WIDTH_BYTES = 2
DEFAULT_MAX_SECONDS = 20.0
DEFAULT_INITIAL_SILENCE_SECONDS = 5.0
DEFAULT_TRAILING_SILENCE_SECONDS = 1.2
DEFAULT_PREROLL_SECONDS = 0.25
DEFAULT_BLOCK_SECONDS = 0.05
DEFAULT_SPEECH_RMS_THRESHOLD = 180.0
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 96_000
MAX_WAV_BYTES = 4 * 1024 * 1024


class AudioCaptureError(RuntimeError):
    """La captura del micrófono no pudo producir audio válido."""


class AudioCaptureCancelled(AudioCaptureError):
    """El usuario canceló la captura antes de enviarla."""


class NoSpeechDetected(AudioCaptureError):
    """No se detectó una frase dentro del límite local."""


@dataclass(frozen=True)
class CapturedAudio:
    wav_bytes: bytes
    duration_seconds: float
    capture_ms: float
    sample_rate: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.wav_bytes, bytes)
            or not 44 < len(self.wav_bytes) <= MAX_WAV_BYTES
        ):
            raise AudioCaptureError("El WAV capturado no es válido.")
        if (
            type(self.duration_seconds) not in (int, float)
            or not math.isfinite(float(self.duration_seconds))
            or float(self.duration_seconds) <= 0
        ):
            raise AudioCaptureError("La duración capturada no es válida.")
        if (
            type(self.capture_ms) not in (int, float)
            or not math.isfinite(float(self.capture_ms))
            or float(self.capture_ms) < 0
        ):
            raise AudioCaptureError("La latencia de captura no es válida.")
        if type(self.sample_rate) is not int or not (
            MIN_SAMPLE_RATE <= self.sample_rate <= MAX_SAMPLE_RATE
        ):
            raise AudioCaptureError("La frecuencia capturada no es válida.")
        object.__setattr__(self, "duration_seconds", float(self.duration_seconds))
        object.__setattr__(self, "capture_ms", float(self.capture_ms))


class AudioInputStream(Protocol):
    def __enter__(self) -> "AudioInputStream": ...

    def __exit__(self, *args: object) -> object: ...

    def read(self, frames: int) -> tuple[object, bool]: ...


SampleRateProvider = Callable[[], int]
StreamFactory = Callable[[int, int], AudioInputStream]
Clock = Callable[[], float]


def _default_sample_rate() -> int:
    import sounddevice

    details = sounddevice.query_devices(kind="input")
    raw_rate = details["default_samplerate"]
    rate = int(round(float(raw_rate)))
    if not MIN_SAMPLE_RATE <= rate <= MAX_SAMPLE_RATE:
        raise AudioCaptureError("El micrófono informa una frecuencia no válida.")
    return rate


def _default_stream_factory(
    sample_rate: int,
    block_size: int,
) -> AudioInputStream:
    import sounddevice

    return sounddevice.RawInputStream(
        samplerate=sample_rate,
        blocksize=block_size,
        channels=1,
        dtype="int16",
    )


def _pcm_rms(frame: bytes) -> float:
    if not frame or len(frame) % SAMPLE_WIDTH_BYTES:
        raise AudioCaptureError("El micrófono devolvió un bloque inválido.")
    samples = memoryview(frame).cast("h")
    mean_square = math.fsum(sample * sample for sample in samples) / len(samples)
    return math.sqrt(mean_square)


def _encode_wav(frames: list[bytes], sample_rate: int) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(frames))
    value = target.getvalue()
    if len(value) > MAX_WAV_BYTES:
        raise AudioCaptureError("La captura supera el tamaño permitido.")
    return value


class SoundDeviceWavRecorder:
    """Captura una frase PCM y la conserva únicamente como WAV en memoria."""

    def __init__(
        self,
        *,
        max_seconds: float = DEFAULT_MAX_SECONDS,
        initial_silence_seconds: float = DEFAULT_INITIAL_SILENCE_SECONDS,
        trailing_silence_seconds: float = DEFAULT_TRAILING_SILENCE_SECONDS,
        preroll_seconds: float = DEFAULT_PREROLL_SECONDS,
        block_seconds: float = DEFAULT_BLOCK_SECONDS,
        speech_rms_threshold: float = DEFAULT_SPEECH_RMS_THRESHOLD,
        sample_rate_provider: SampleRateProvider = _default_sample_rate,
        stream_factory: StreamFactory = _default_stream_factory,
        clock: Clock = time.monotonic,
    ) -> None:
        durations = (
            max_seconds,
            initial_silence_seconds,
            trailing_silence_seconds,
            preroll_seconds,
            block_seconds,
        )
        if any(
            type(value) not in (int, float)
            or not math.isfinite(float(value))
            or float(value) <= 0
            for value in durations
        ):
            raise ValueError("Los límites de captura no son válidos.")
        if float(initial_silence_seconds) > float(max_seconds):
            raise ValueError("El silencio inicial supera la captura máxima.")
        if (
            type(speech_rms_threshold) not in (int, float)
            or not math.isfinite(float(speech_rms_threshold))
            or float(speech_rms_threshold) <= 0
        ):
            raise ValueError("El umbral de voz no es válido.")
        self._max_seconds = float(max_seconds)
        self._initial_silence_seconds = float(initial_silence_seconds)
        self._trailing_silence_seconds = float(trailing_silence_seconds)
        self._preroll_seconds = float(preroll_seconds)
        self._block_seconds = float(block_seconds)
        self._speech_rms_threshold = float(speech_rms_threshold)
        self._sample_rate_provider = sample_rate_provider
        self._stream_factory = stream_factory
        self._clock = clock
        self._finish_requested = threading.Event()
        self.recording = False
        self.level = 0.0
        self.phase = "idle"

    def finish(self) -> bool:
        """Termina la frase sin descartarla; cancelar sigue siendo otra operación."""
        if not self.recording:
            return False
        self._finish_requested.set()
        return True

    def capture(self, cancellation: threading.Event) -> CapturedAudio:
        if not isinstance(cancellation, threading.Event):
            raise TypeError("La cancelación de audio no es válida.")
        if cancellation.is_set():
            raise AudioCaptureCancelled("Captura cancelada.")

        started = self._clock()
        self._finish_requested.clear()
        self.phase = "starting"
        try:
            sample_rate = self._sample_rate_provider()
            if type(sample_rate) is not int or not (
                MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE
            ):
                raise AudioCaptureError("La frecuencia del micrófono no es válida.")
            block_size = max(1, round(sample_rate * self._block_seconds))
            block_seconds = block_size / sample_rate
            max_blocks = max(1, math.ceil(self._max_seconds / block_seconds))
            initial_blocks = max(
                1,
                math.ceil(self._initial_silence_seconds / block_seconds),
            )
            trailing_blocks = max(
                1,
                math.ceil(self._trailing_silence_seconds / block_seconds),
            )
            preroll_blocks = max(
                1,
                math.ceil(self._preroll_seconds / block_seconds),
            )
            captured: list[bytes] = []
            preroll: deque[bytes] = deque(maxlen=preroll_blocks)
            speech_started = False
            voiced_streak = 0
            trailing_silence = 0

            with self._stream_factory(sample_rate, block_size) as stream:
                self.recording = True
                self.phase = "recording"
                for block_index in range(max_blocks):
                    if cancellation.is_set():
                        raise AudioCaptureCancelled("Captura cancelada.")
                    if self._finish_requested.is_set():
                        break
                    raw_frame, overflowed = stream.read(block_size)
                    if overflowed:
                        raise AudioCaptureError(
                            "El micrófono perdió parte de la captura."
                        )
                    frame = bytes(raw_frame)
                    if len(frame) != block_size * SAMPLE_WIDTH_BYTES:
                        raise AudioCaptureError(
                            "El micrófono devolvió un bloque incompleto."
                        )
                    rms = _pcm_rms(frame)
                    self.level = min(1.0, rms / 4000.0)
                    voiced = rms >= self._speech_rms_threshold

                    if not speech_started:
                        preroll.append(frame)
                        voiced_streak = voiced_streak + 1 if voiced else 0
                        if voiced_streak >= 2:
                            speech_started = True
                            captured.extend(preroll)
                            preroll.clear()
                        elif block_index + 1 >= initial_blocks:
                            break
                        continue

                    captured.append(frame)
                    trailing_silence = 0 if voiced else trailing_silence + 1
                    if trailing_silence >= trailing_blocks:
                        break
        except (AudioCaptureCancelled, NoSpeechDetected, AudioCaptureError):
            raise
        except Exception:
            raise AudioCaptureError("No se pudo acceder al micrófono.") from None
        finally:
            self.recording = False
            self.level = 0.0
            self.phase = "finished"

        if cancellation.is_set():
            raise AudioCaptureCancelled("Captura cancelada.")
        if not speech_started or not captured:
            raise NoSpeechDetected("No se detectó una frase.")

        frame_count = sum(len(frame) for frame in captured) // SAMPLE_WIDTH_BYTES
        duration_seconds = frame_count / sample_rate
        wav_bytes = _encode_wav(captured, sample_rate)
        capture_ms = max(0.0, (self._clock() - started) * 1_000)
        return CapturedAudio(
            wav_bytes,
            duration_seconds,
            capture_ms,
            sample_rate,
        )
