import json
import math
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from desktop_agent.voice import (
    MAX_TRANSCRIPT_LENGTH,
    VoiceBackendResult,
    VoiceError,
    VoiceResultStatus,
    normalize_transcript,
)


_ALLOWED_CULTURES = frozenset({"es-UY", "es-AR", "es-ES"})
_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "transcript",
        "confidence",
        "culture",
        "capture_ms",
        "transcription_ms",
    }
)


@runtime_checkable
class SpeechProcess(Protocol):
    def communicate(self, timeout: float | None = None) -> tuple[str, str]: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[[tuple[str, ...]], SpeechProcess]
Clock = Callable[[], float]


def _default_script_path() -> Path:
    return Path(__file__).with_name("assets") / "recognize_speech.ps1"


def _default_powershell_path() -> Path:
    return Path(
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    )


class WindowsSpeechBackend:
    """Reconoce una frase localmente; no crea archivos ni abre conexiones."""

    def __init__(
        self,
        culture: str = "es-UY",
        max_seconds: int = 10,
        min_confidence: float = 0.55,
        script_path: Path | None = None,
        powershell_path: Path | None = None,
        process_factory: ProcessFactory | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        if culture not in _ALLOWED_CULTURES:
            raise ValueError("La cultura de voz no está permitida.")
        if type(max_seconds) is not int or not 1 <= max_seconds <= 20:
            raise ValueError("La duración de voz debe estar entre 1 y 20 segundos.")
        if (
            type(min_confidence) not in (int, float)
            or not math.isfinite(float(min_confidence))
            or not 0 <= float(min_confidence) <= 1
        ):
            raise ValueError("La confianza mínima no es válida.")
        selected_script = script_path or _default_script_path()
        selected_powershell = powershell_path or _default_powershell_path()
        if not isinstance(selected_script, Path) or not selected_script.is_absolute():
            raise ValueError("La ruta del helper de voz debe ser absoluta.")
        if (
            not isinstance(selected_powershell, Path)
            or not selected_powershell.is_absolute()
        ):
            raise ValueError("La ruta de PowerShell debe ser absoluta.")
        self._culture = culture
        self._max_seconds = max_seconds
        self._min_confidence = float(min_confidence)
        self._script_path = selected_script
        self._powershell_path = selected_powershell
        self._process_factory = process_factory or self._start_process
        self._clock = clock

    def recognize(self, cancellation: threading.Event) -> VoiceBackendResult:
        if not isinstance(cancellation, threading.Event):
            raise TypeError("La cancelación de voz no es válida.")
        if cancellation.is_set():
            return self._simple_result(VoiceResultStatus.CANCELLED)
        if not self._script_path.is_file() or not self._powershell_path.is_file():
            return self._simple_result(VoiceResultStatus.UNAVAILABLE)
        command = (
            str(self._powershell_path),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self._script_path),
            "-Culture",
            self._culture,
            "-MaxSeconds",
            str(self._max_seconds),
        )
        try:
            process = self._process_factory(command)
        except Exception:
            return self._simple_result(VoiceResultStatus.UNAVAILABLE)
        started = self._clock()
        deadline = started + self._max_seconds + 3
        while True:
            if cancellation.is_set():
                self._terminate(process)
                return self._simple_result(VoiceResultStatus.CANCELLED)
            if self._clock() >= deadline:
                self._terminate(process)
                return self._simple_result(VoiceResultStatus.FAILED)
            try:
                stdout, _ = process.communicate(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                continue
            except Exception:
                self._terminate(process)
                return self._simple_result(VoiceResultStatus.FAILED)
        try:
            return self._parse(stdout)
        except VoiceError:
            return self._simple_result(VoiceResultStatus.FAILED)

    def _parse(self, stdout: object) -> VoiceBackendResult:
        if not isinstance(stdout, str) or len(stdout) > 4096:
            raise VoiceError("La salida local de voz no es válida.")
        try:
            value = json.loads(stdout.lstrip("\ufeff").strip())
        except (json.JSONDecodeError, UnicodeError) as error:
            raise VoiceError("La salida local de voz no es JSON válido.") from error
        if not isinstance(value, dict) or set(value) != _RESULT_FIELDS:
            raise VoiceError("La salida local de voz no cumple el esquema.")
        if value["schema_version"] != 1:
            raise VoiceError("La salida local de voz usa otro esquema.")
        try:
            status = VoiceResultStatus(value["status"])
        except (TypeError, ValueError) as error:
            raise VoiceError("El estado local de voz no es válido.") from error
        confidence = value["confidence"]
        capture_ms = value["capture_ms"]
        transcription_ms = value["transcription_ms"]
        culture = value["culture"]
        transcript: str | None = None
        if status in {
            VoiceResultStatus.READY,
            VoiceResultStatus.AMBIGUOUS,
        }:
            transcript = normalize_transcript(value["transcript"])
            if len(transcript) > MAX_TRANSCRIPT_LENGTH:
                raise VoiceError("La transcripción local es demasiado larga.")
            if type(confidence) not in (int, float):
                raise VoiceError("La confianza local no es válida.")
            if (
                status is VoiceResultStatus.READY
                and float(confidence) < self._min_confidence
            ):
                status = VoiceResultStatus.AMBIGUOUS
        elif value["transcript"] != "":
            raise VoiceError("Un fallo local de voz contiene texto inesperado.")
        return VoiceBackendResult(
            status,
            transcript,
            confidence,
            culture,
            capture_ms,
            transcription_ms,
        )

    def _start_process(self, command: tuple[str, ...]) -> SpeechProcess:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=creation_flags,
        )

    @staticmethod
    def _terminate(process: SpeechProcess) -> None:
        try:
            process.terminate()
            process.communicate(timeout=0.5)
        except Exception:
            try:
                process.kill()
                process.communicate(timeout=0.5)
            except Exception:
                pass

    def _simple_result(self, status: VoiceResultStatus) -> VoiceBackendResult:
        return VoiceBackendResult(status, None, 0, "none", 0, 0)
