import io
import logging

from desktop_agent.executor import ActionExecutionError, ActionExecutor
from desktop_agent.models import Action, Intent, RiskLevel, ToolResult
from desktop_agent.permissions import (
    AuthorizationStatus,
    CapabilityRule,
    ConfirmationChannel,
    ConfirmationDecision,
    Effect,
    PermissionBroker,
    PolicyRegistry,
    PolicySubject,
    PreparationStatus,
)


def main() -> int:
    stream = io.StringIO()
    logger = logging.Logger("policy-qa")
    logger.addHandler(logging.StreamHandler(stream))
    registry = PolicyRegistry(
        (
            CapabilityRule(
                "modify-fictitious-client",
                Effect.MODIFY_LOCAL_DATA,
                (Intent.MODIFY_LOCAL_DATA,),
                ("qa_modify",),
                ("target", "content"),
                "target",
            ),
            CapabilityRule(
                "arbitrary-code",
                Effect.EXECUTE_ARBITRARY_CODE,
                (Intent.SYSTEM_CHANGE,),
                ("qa_shell",),
                ("target",),
                "target",
            ),
        )
    )
    broker = PermissionBroker(registry, logger)
    private_value = "Cliente Ficticio Privado QA"
    action = Action(
        Intent.MODIFY_LOCAL_DATA,
        "qa_modify",
        {"target": "record-qa-001", "content": private_value},
        RiskLevel.CAUTION,
        True,
    )
    subject = PolicySubject(
        "action-modify-qa",
        "modify-fictitious-client",
        action,
    )
    preparation = broker.prepare(subject)
    request = preparation.request
    if preparation.status is not PreparationStatus.PENDING or request is None:
        print("POLICY_QA_FAILED: no_confirmation")
        return 1
    decision = ConfirmationDecision(
        request.request_id,
        request.challenge,
        request.subject_fingerprint,
        True,
        ConfirmationChannel.LOCAL,
    )
    authorization = broker.authorize(subject, decision)
    calls: list[dict[str, str]] = []

    def modify(**arguments: str) -> ToolResult:
        calls.append(arguments)
        return ToolResult(True, "ok")

    executor = ActionExecutor({"qa_modify": modify}, logger, broker)
    if (
        authorization.status is not AuthorizationStatus.AUTHORIZED
        or authorization.token is None
    ):
        print("POLICY_QA_FAILED: not_authorized")
        return 1
    executor.execute(action, authorization.token)
    try:
        executor.execute(action, authorization.token)
    except ActionExecutionError:
        reused = False
    else:
        reused = True

    blocked = broker.prepare(
        PolicySubject(
            "action-shell-qa",
            "arbitrary-code",
            Action(
                Intent.SYSTEM_CHANGE,
                "qa_shell",
                {"target": "fictitious-script"},
                RiskLevel.DANGEROUS,
                True,
            ),
        )
    )
    if (
        calls != [{"target": "record-qa-001", "content": private_value}]
        or reused
        or blocked.status is not PreparationStatus.BLOCKED
        or blocked.request is not None
        or private_value in stream.getvalue()
    ):
        print("POLICY_QA_FAILED: invariant")
        return 1

    print(
        "POLICY_QA_OK: confirmacion exacta consumida una vez, repeticion "
        "rechazada y efecto prohibido bloqueado; datos ficticios."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
