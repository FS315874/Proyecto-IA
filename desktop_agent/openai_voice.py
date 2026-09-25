import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from desktop_agent.audio_capture import (
    AudioCaptureCancelled,
    AudioCaptureError,
    CapturedAudio,
    NoSpeechDetected,
)
from desktop_agent.interpretation import (
    MonthlyUsageSnapshot,
    ProposalBudgetExceededError,
    ProposalUsage,
)
from desktop_agent.usage_budget import MonthlyUsageLedger, UsageLedgerError
from desktop_agent.voice import (
    VoiceBackendResult,
    VoiceError,
    VoiceFailureReason,
    VoiceResultStatus,
    normalize_transcript,
)
from desktop_agent.voice_transcription_config import VoiceTranscriptionConfig


GPT_TRANSCRIBE_USD_PER_MINUTE = 0.0045
TRANSCRIPTION_LANGUAGE = "es"
TRANSCRIPTION_CULTURE = "es-ES"


class TranscriptionProviderError(RuntimeError):
    """Fallo externo reducido a una categoría segura para la aplicación."""

    def __init__(self, reason: VoiceFailureReason) -> None:
        if not isinstance(reason, VoiceFailureReason):
            raise TypeError("La causa externa de voz no es válida.")
        self.reason = reason
        super().__init__("La transcripción externa no pudo completarse.")


@runtime_checkable
class AudioRecorder(Protocol):
    def capture(self, cancellation: threading.Event) -> CapturedAudio: ...


@dataclass(frozen=True)
class TranscriptionResult:
    transcript: str
    duration_ms: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "transcript", normalize_transcript(self.transcript))
        if (
            type(self.duration_ms) not in (int, float)
            or not math.isfinite(float(self.duration_ms))
            or float(self.duration_ms) < 0
        ):
            raise VoiceError("La latencia de transcripción no es válida.")
        object.__setattr__(self, "duration_ms", float(self.duration_ms))


@runtime_checkable
class TranscriptionProvider(Protocol):
    def transcribe(self, audio: CapturedAudio) -> TranscriptionResult: ...


class TranscriptionsResource(Protocol):
    def create(self, **kwargs: object) -> object: ...


class AudioResource(Protocol):
    transcriptions: TranscriptionsResource


class OpenAIClient(Protocol):
    audio: AudioResource


def _create_openai_client(config: VoiceTranscriptionConfig) -> OpenAIClient:
    try:
        from openai import OpenAI

        api_key = config.api_key
        assert api_key is not None
        return OpenAI(
            api_key=api_key,
            timeout=config.timeout_seconds,
            max_retries=0,
        )
    except Exception:
        raise TranscriptionProviderError(VoiceFailureReason.PROVIDER_SETUP) from None


def classify_provider_error(error: Exception) -> VoiceFailureReason:
    """Clasifica sin copiar mensajes, cuerpos ni otros datos del proveedor."""

    if not isinstance(error, Exception):
        raise TypeError("El error del proveedor no es válido.")
    status_code = getattr(error, "status_code", None)
    if status_code == 401:
        return VoiceFailureReason.AUTHENTICATION
    if status_code == 403:
        return VoiceFailureReason.PERMISSION
    if status_code == 404:
        return VoiceFailureReason.MODEL_UNAVAILABLE
    if status_code == 429:
        return VoiceFailureReason.QUOTA_OR_RATE_LIMIT
    if status_code in {400, 409, 422}:
        return VoiceFailureReason.REQUEST_REJECTED
    if isinstance(status_code, int) and status_code >= 500:
        return VoiceFailureReason.SERVICE_UNAVAILABLE

    error_name = type(error).__name__
    if isinstance(error, TimeoutError) or error_name == "APITimeoutError":
        return VoiceFailureReason.TIMEOUT
    if isinstance(error, ConnectionError) or error_name == "APIConnectionError":
        return VoiceFailureReason.NETWORK
    return VoiceFailureReason.UNKNOWN


class OpenAITranscriptionProvider:
    """Envía un WAV acotado a gpt-transcribe sin hints de comandos."""

    def __init__(
        self,
        config: VoiceTranscriptionConfig,
        client: OpenAIClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(config, VoiceTranscriptionConfig) or not config.enabled:
            raise ValueError("La transcripción externa está deshabilitada.")
        self._config = config
        self._client = client
        self._clock = clock

    def _get_client(self) -> OpenAIClient:
        if self._client is None:
            self._client = _create_openai_client(self._config)
        return self._client

    def transcribe(self, audio: CapturedAudio) -> TranscriptionResult:
        if not isinstance(audio, CapturedAudio):
            raise TypeError("El audio no cumple el contrato de transcripción.")
        started = self._clock()
        try:
            response = self._get_client().audio.transcriptions.create(
                model=self._config.model,
                file=("voice.wav", audio.wav_bytes, "audio/wav"),
                language=TRANSCRIPTION_LANGUAGE,
                timeout=self._config.timeout_seconds,
            )
        except TranscriptionProviderError:
            raise
        except Exception as error:
            raise TranscriptionProviderError(classify_provider_error(error)) from None

        try:
            transcript = response if isinstance(response, str) else response.text
            normalized = normalize_transcript(transcript)
        except (AttributeError, TypeError, VoiceError):
            raise TranscriptionProviderError(
                VoiceFailureReason.INVALID_RESPONSE
            ) from None
        return TranscriptionResult(
            normalized,
            max(0.0, (self._clock() - started) * 1_000),
        )


UsageCallback = Callable[[MonthlyUsageSnapshot], None]


class BudgetedOpenAIVoiceBackend:
    """Captura localmente, aplica presupuesto y recién entonces envía el WAV."""

    def __init__(
        self,
        recorder: AudioRecorder,
        provider: TranscriptionProvider,
        ledger: MonthlyUsageLedger,
        logger: logging.Logger,
        usage_callback: UsageCallback | None = None,
    ) -> None:
        if not isinstance(recorder, AudioRecorder):
            raise TypeError("El grabador no cumple el contrato de voz.")
        if not isinstance(provider, TranscriptionProvider):
            raise TypeError("El transcriptor no cumple el contrato de voz.")
        if not isinstance(ledger, MonthlyUsageLedger):
            raise TypeError("El presupuesto de voz no es válido.")
        if not isinstance(logger, logging.Logger):
            raise TypeError("El logger de voz no es válido.")
        self._recorder = recorder
        self._provider = provider
        self._ledger = ledger
        self._logger = logger
        self._usage_callback = usage_callback

    def recognize(self, cancellation: threading.Event) -> VoiceBackendResult:
        if not isinstance(cancellation, threading.Event):
            raise TypeError("La cancelación de voz no es válida.")
        try:
            audio = self._recorder.capture(cancellation)
        except AudioCaptureCancelled:
            return self._result(VoiceResultStatus.CANCELLED)
        except NoSpeechDetected:
            return self._result(VoiceResultStatus.NO_SPEECH)
        except AudioCaptureError:
            self._log("capture", "failed")
            return self._result(VoiceResultStatus.UNAVAILABLE)
        except Exception:
            self._log("capture", "failed")
            return self._result(VoiceResultStatus.FAILED)

        if cancellation.is_set():
            return self._result(
                VoiceResultStatus.CANCELLED,
                capture_ms=audio.capture_ms,
            )

        estimated_cost = estimate_transcription_cost(audio.duration_seconds)
        try:
            reservation_id, _ = self._ledger.reserve(estimated_cost)
        except ProposalBudgetExceededError:
            self._log("budget", "blocked")
            return self._result(
                VoiceResultStatus.BUDGET_EXCEEDED,
                capture_ms=audio.capture_ms,
            )
        except (UsageLedgerError, ValueError):
            self._log("budget", "failed")
            return self._result(
                VoiceResultStatus.FAILED,
                capture_ms=audio.capture_ms,
            )

        if cancellation.is_set():
            snapshot = self._release_reservation(reservation_id)
            self._notify_usage(snapshot)
            return self._result(
                VoiceResultStatus.CANCELLED,
                capture_ms=audio.capture_ms,
            )

        transcription_started = time.monotonic()
        try:
            transcription = self._provider.transcribe(audio)
        except TranscriptionProviderError as error:
            # Un rechazo explícito no es consumo confirmado. Un timeout conserva
            # una reserva conservadora porque el servidor pudo procesar el audio.
            rejected = error.reason in {
                VoiceFailureReason.PROVIDER_SETUP, VoiceFailureReason.AUTHENTICATION,
                VoiceFailureReason.PERMISSION, VoiceFailureReason.MODEL_UNAVAILABLE,
                VoiceFailureReason.QUOTA_OR_RATE_LIMIT, VoiceFailureReason.REQUEST_REJECTED,
            }
            snapshot = self._release_reservation(reservation_id) if rejected else self._settle_failure(reservation_id)
            self._notify_usage(snapshot)
            self._log("transcription", "failed", error.reason)
            return self._result(
                VoiceResultStatus.FAILED,
                capture_ms=audio.capture_ms,
                transcription_ms=(time.monotonic() - transcription_started) * 1000,
                estimated_cost_usd=0.0 if rejected else estimated_cost,
                failure_reason=error.reason,
            )
        except Exception:
            snapshot = self._settle_failure(reservation_id)
            self._notify_usage(snapshot)
            self._log(
                "transcription",
                "failed",
                VoiceFailureReason.UNKNOWN,
            )
            return self._result(
                VoiceResultStatus.FAILED,
                capture_ms=audio.capture_ms,
                transcription_ms=(time.monotonic() - transcription_started) * 1000,
                estimated_cost_usd=estimated_cost,
                failure_reason=VoiceFailureReason.UNKNOWN,
            )

        try:
            snapshot = self._ledger.settle(
                reservation_id,
                ProposalUsage(0, 0, 0),
                estimated_cost,
            )
        except UsageLedgerError:
            self._log("usage", "failed")
            return self._result(
                VoiceResultStatus.FAILED,
                capture_ms=audio.capture_ms,
                transcription_ms=transcription.duration_ms,
                estimated_cost_usd=estimated_cost,
            )
        self._notify_usage(snapshot)
        self._log("transcription", "success")
        return VoiceBackendResult(
            VoiceResultStatus.READY,
            transcription.transcript,
            None,
            TRANSCRIPTION_CULTURE,
            audio.capture_ms,
            transcription.duration_ms,
            estimated_cost,
        )

    def _settle_failure(
        self,
        reservation_id: str,
    ) -> MonthlyUsageSnapshot | None:
        try:
            return self._ledger.settle(reservation_id, None, None)
        except UsageLedgerError:
            self._log("usage", "failed")
            return None

    def _release_reservation(
        self,
        reservation_id: str,
    ) -> MonthlyUsageSnapshot | None:
        try:
            return self._ledger.release(reservation_id)
        except UsageLedgerError:
            self._log("usage", "failed")
            return None

    def _notify_usage(self, snapshot: MonthlyUsageSnapshot | None) -> None:
        if snapshot is None or self._usage_callback is None:
            return
        try:
            self._usage_callback(snapshot)
        except Exception:
            self._log("usage_callback", "failed")

    def _log(
        self,
        stage: str,
        status: str,
        failure_reason: VoiceFailureReason | None = None,
    ) -> None:
        self._logger.info(
            "Voice provider: model=gpt-transcribe stage=%s status=%s reason=%s",
            stage,
            status,
            failure_reason.value if failure_reason is not None else "none",
        )

    @staticmethod
    def _result(
        status: VoiceResultStatus,
        *,
        capture_ms: float = 0,
        transcription_ms: float = 0,
        estimated_cost_usd: float | None = None,
        failure_reason: VoiceFailureReason | None = None,
    ) -> VoiceBackendResult:
        return VoiceBackendResult(
            status,
            None,
            None,
            "none",
            capture_ms,
            transcription_ms,
            estimated_cost_usd,
            failure_reason,
        )


def estimate_transcription_cost(duration_seconds: float) -> float:
    if (
        type(duration_seconds) not in (int, float)
        or not math.isfinite(float(duration_seconds))
        or float(duration_seconds) <= 0
    ):
        raise ValueError("La duración facturable no es válida.")
    return round(
        float(duration_seconds) / 60 * GPT_TRANSCRIBE_USD_PER_MINUTE,
        12,
    )
