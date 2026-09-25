import logging
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from desktop_agent.device_registry import DeviceRegistry, DeviceRegistryError
from desktop_agent.local_service import DesktopAgentService, ServiceError, TaskState
from desktop_agent.permissions import (
    AuthorizationStatus,
    ConfirmationChannel,
    ConfirmationDecision,
)
from desktop_agent.remote_protocol import (
    CancelPayload,
    CommandPayload,
    ConfirmationPayload,
    EncryptedEnvelope,
    RemoteCodec,
    RemoteDirection,
    RemoteMessage,
    RemoteMessageType,
    RemoteProtocolError,
    RemoteConfirmationSummary,
    RemoteResponseStatus,
    RemoteTaskSummary,
    ResponsePayload,
    StatusPayload,
)


MAX_REMOTE_COMMANDS_PER_MINUTE = 10
MAX_REMOTE_PENDING_TASKS = 3


@runtime_checkable
class RemoteService(Protocol):
    @property
    def snapshot(self): ...

    def submit(self, command: str) -> str: ...

    def cancel_task(self, task_id: str) -> bool: ...

    def resolve_confirmation(self, task_id: str, decision: ConfirmationDecision): ...


class RemoteGateway:
    """Adapta mensajes autenticados al mismo servicio usado por la GUI local."""

    def __init__(
        self,
        service: DesktopAgentService,
        devices: DeviceRegistry,
        logger: logging.Logger,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(service, RemoteService):
            raise TypeError("El gateway requiere el servicio local.")
        if not isinstance(devices, DeviceRegistry):
            raise TypeError("El gateway requiere un registro de dispositivos.")
        self._service = service
        self._devices = devices
        self._logger = logger
        self._clock = clock
        self._inbound = RemoteCodec(RemoteDirection.CLIENT_TO_AGENT, clock=clock)
        self._outbound = RemoteCodec(RemoteDirection.AGENT_TO_CLIENT, clock=clock)
        self._command_times: dict[str, deque[float]] = defaultdict(deque)

    def handle(self, envelope: EncryptedEnvelope) -> EncryptedEnvelope | None:
        started = time.monotonic()
        message_type = "unknown"
        status = "rejected"
        try:
            secret = self._devices.active_secret(envelope.device_id)
            message = self._inbound.decrypt(envelope, secret)
            message_type = message.message_type.value
            if message.message_type is RemoteMessageType.RESPONSE:
                raise RemoteProtocolError("Una respuesta no puede entrar al agente.")
            # La autenticidad se valida antes de consumir la secuencia: un atacante
            # sin clave no puede adelantar el contador de un dispositivo legítimo.
            self._devices.claim_inbound(envelope)
            response = self._dispatch(envelope.device_id, message)
            sequence = self._devices.reserve_outbound_sequence(envelope.device_id)
            status = response.status.value
            return self._outbound.encrypt(
                envelope.device_id,
                secret,
                sequence,
                RemoteMessage(
                    RemoteMessageType.RESPONSE,
                    message.request_id,
                    response,
                ),
            )
        except (DeviceRegistryError, RemoteProtocolError):
            return None
        finally:
            duration_ms = (time.monotonic() - started) * 1000
            self._logger.info(
                "Remote gateway: device=%s message=%s type=%s status=%s "
                "duration_ms=%.2f",
                getattr(envelope, "device_id", "invalid"),
                getattr(envelope, "message_id", "invalid"),
                message_type,
                status,
                duration_ms,
            )

    def _dispatch(self, device_id: str, message: RemoteMessage) -> ResponsePayload:
        payload = message.payload
        if isinstance(payload, CommandPayload):
            return self._command(device_id, payload)
        if isinstance(payload, CancelPayload):
            return self._cancel(payload)
        if isinstance(payload, ConfirmationPayload):
            return self._confirm(payload)
        if isinstance(payload, StatusPayload):
            return self._status("status_snapshot")
        return self._response(RemoteResponseStatus.REJECTED, "message_not_allowed")

    def _command(self, device_id: str, payload: CommandPayload) -> ResponsePayload:
        now = self._clock()
        recent = self._command_times[device_id]
        while recent and recent[0] <= now - 60:
            recent.popleft()
        if len(recent) >= MAX_REMOTE_COMMANDS_PER_MINUTE:
            return self._response(RemoteResponseStatus.REJECTED, "rate_limited")
        snapshot = self._service.snapshot
        pending = sum(
            item.state in {
                TaskState.QUEUED,
                TaskState.RUNNING,
                TaskState.AWAITING_CONFIRMATION,
            }
            for item in snapshot.history
        )
        if pending >= MAX_REMOTE_PENDING_TASKS:
            return self._response(RemoteResponseStatus.REJECTED, "queue_limit")
        recent.append(now)
        try:
            task_id = self._service.submit(payload.command)
        except ServiceError:
            return self._response(RemoteResponseStatus.REJECTED, "service_unavailable")
        return self._response(
            RemoteResponseStatus.ACCEPTED,
            "command_accepted",
            task_id,
        )

    def _cancel(self, payload: CancelPayload) -> ResponsePayload:
        try:
            cancelled = self._service.cancel_task(payload.task_id)
        except ServiceError:
            return self._response(RemoteResponseStatus.REJECTED, "cancel_not_safe")
        return self._response(
            (
                RemoteResponseStatus.CANCELLED
                if cancelled
                else RemoteResponseStatus.REJECTED
            ),
            "task_cancelled" if cancelled else "task_not_cancellable",
            payload.task_id,
        )

    def _confirm(self, payload: ConfirmationPayload) -> ResponsePayload:
        pending = self._service.snapshot.pending_confirmation
        if pending is None or pending.task_id != payload.task_id:
            return self._response(RemoteResponseStatus.REJECTED, "confirmation_stale")
        request = pending.request
        exact = (
            request.request_id == payload.confirmation_request_id
            and request.challenge == payload.challenge
            and request.subject_fingerprint == payload.subject_fingerprint
            and ConfirmationChannel.REMOTE in request.allowed_channels
        )
        if not exact:
            return self._response(
                RemoteResponseStatus.REJECTED,
                "confirmation_mismatch",
            )
        decision = ConfirmationDecision(
            request.request_id,
            request.challenge,
            request.subject_fingerprint,
            payload.approved,
            ConfirmationChannel.REMOTE,
        )
        try:
            result = self._service.resolve_confirmation(payload.task_id, decision)
        except ServiceError:
            return self._response(RemoteResponseStatus.REJECTED, "confirmation_stale")
        accepted = result.authorization.status is AuthorizationStatus.AUTHORIZED
        return self._response(
            (
                RemoteResponseStatus.ACCEPTED
                if accepted
                else RemoteResponseStatus.REJECTED
            ),
            "confirmation_applied" if accepted else "confirmation_rejected",
            payload.task_id,
        )

    def _status(self, reason: str) -> ResponsePayload:
        return self._response(RemoteResponseStatus.STATUS, reason)

    def _response(
        self,
        status: RemoteResponseStatus,
        reason: str,
        task_id: str | None = None,
    ) -> ResponsePayload:
        snapshot = self._service.snapshot
        tasks = tuple(
            RemoteTaskSummary(item.task_id, item.state.value, item.active_tool)
            for item in snapshot.history[-20:]
        )
        pending = snapshot.pending_confirmation
        remote_pending = None
        if (
            pending is not None
            and ConfirmationChannel.REMOTE in pending.request.allowed_channels
        ):
            request = pending.request
            remote_pending = RemoteConfirmationSummary(
                pending.task_id,
                request.request_id,
                request.challenge,
                request.subject_fingerprint,
                request.effect.value,
                request.risk_level.value,
                request.destination,
            )
        return ResponsePayload(
            status,
            snapshot.state.value,
            reason,
            task_id,
            tasks,
            remote_pending,
        )
