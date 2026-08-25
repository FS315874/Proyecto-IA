import io
import logging
import unittest
from dataclasses import replace

from desktop_agent.input_control import (
    AccessibleControl,
    EmergencyStop,
    InputAction,
    InputConfirmation,
    InputControlError,
    InputController,
    InputKind,
    InputLimits,
    InputMethod,
    InputStatus,
    InputWindowState,
)
from desktop_agent.models import RiskLevel
from desktop_agent.observation import (
    CaptureRegion,
    ObservationLimits,
    ObservationService,
    RasterFrame,
    WindowTarget,
)
from desktop_agent.vision import (
    VisualElement,
    VisualInterpretation,
    VisualRole,
    VisualSource,
    VisualStatus,
)


class MutableClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeCaptureBackend:
    def capture(
        self,
        target: WindowTarget,
        region: CaptureRegion,
    ) -> RasterFrame:
        return RasterFrame(
            region.width,
            region.height,
            region.width * 4,
            bytes(region.width * region.height * 4),
        )


class FakeInputBackend:
    def __init__(self, target: WindowTarget) -> None:
        self.target = target
        self.state = InputWindowState(
            True,
            True,
            False,
            False,
            target.process_id,
            target.client_width,
            target.client_height,
        )
        self.control: AccessibleControl | None = AccessibleControl(
            300,
            VisualRole.BUTTON,
            CaptureRegion(20, 20, 80, 30),
            True,
            True,
            False,
        )
        self.focus_window_result = True
        self.focus_control_result = True
        self.click_result = True
        self.type_result = True
        self.verify_result = True
        self.modal_after_input = False
        self.calls: list[tuple[object, ...]] = []
        self.on_resolve: object = None

    def inspect_window(self, target: WindowTarget) -> InputWindowState:
        self.calls.append(("inspect",))
        return self.state

    def focus_window(self, target: WindowTarget) -> bool:
        self.calls.append(("focus_window",))
        if self.focus_window_result:
            self.state = replace(self.state, foreground=True)
        return self.focus_window_result

    def resolve_accessible(
        self,
        target: WindowTarget,
        element: VisualElement,
    ) -> AccessibleControl | None:
        self.calls.append(("resolve", element.element_id))
        if callable(self.on_resolve):
            self.on_resolve()
        return self.control

    def click_accessible(
        self,
        target: WindowTarget,
        control: AccessibleControl,
    ) -> bool:
        self.calls.append(("click_accessible", control.native_handle))
        if self.modal_after_input:
            self.state = replace(self.state, unknown_modal=True)
        return self.click_result

    def focus_control(
        self,
        target: WindowTarget,
        control: AccessibleControl,
    ) -> bool:
        self.calls.append(("focus_control", control.native_handle))
        if self.focus_control_result:
            self.state = replace(
                self.state,
                focused_control_handle=control.native_handle,
            )
        return self.focus_control_result

    def type_text(
        self,
        target: WindowTarget,
        control: AccessibleControl,
        text: str,
    ) -> bool:
        self.calls.append(("type_text", control.native_handle, text))
        if self.modal_after_input:
            self.state = replace(self.state, unknown_modal=True)
        return self.type_result

    def verify_control_text(
        self,
        target: WindowTarget,
        control: AccessibleControl,
        text: str,
    ) -> bool:
        self.calls.append(("verify_text", control.native_handle, text))
        return self.verify_result

    def click_client_point(
        self,
        target: WindowTarget,
        x: int,
        y: int,
    ) -> bool:
        self.calls.append(("click_point", x, y))
        if self.modal_after_input:
            self.state = replace(self.state, unknown_modal=True)
        return self.click_result


def make_target(window_id: str = "window-test") -> WindowTarget:
    return WindowTarget(window_id, 100, 200, 200, 100)


def visual_element(
    observation_id: str,
    target: WindowTarget,
    role: VisualRole = VisualRole.BUTTON,
) -> VisualElement:
    return VisualElement(
        f"{observation_id}:element-1",
        observation_id,
        target.window_id,
        target.revision,
        role,
        CaptureRegion(20, 20, 80, 30),
        "Etiqueta privada de prueba",
        0.95,
        VisualSource.VISION,
    )


def action(
    observation_id: str,
    target: WindowTarget,
    element: VisualElement,
    *,
    kind: InputKind = InputKind.CLICK_ELEMENT,
    text: str | None = None,
    fallback: bool = False,
    action_id: str = "action-1",
) -> InputAction:
    return InputAction(
        action_id,
        kind,
        target.window_id,
        target.revision,
        observation_id,
        element.element_id,
        text,
        fallback,
    )


class InputControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = make_target()
        self.capture_backend = FakeCaptureBackend()
        self.observations = ObservationService(
            self.capture_backend,
            logging.getLogger(f"{self.id()}.observation"),
            limits=ObservationLimits(
                max_width=200,
                max_height=100,
                max_pixels=20_000,
                min_interval_seconds=0.001,
                max_captures=10,
                retention_seconds=60,
            ),
            id_factory=lambda: "obs-test",
        )
        self.observation = self.observations.capture(self.target)
        self.element = visual_element(
            self.observation.observation_id, self.target
        )
        self.interpretation = VisualInterpretation(
            VisualStatus.READY,
            (self.element,),
            1.0,
            True,
        )
        self.backend = FakeInputBackend(self.target)
        self.log_output = io.StringIO()
        self.logger = logging.Logger(self.id(), logging.INFO)
        self.logger.addHandler(logging.StreamHandler(self.log_output))
        self.clock = MutableClock()
        self.stop = EmergencyStop()
        self.controller = InputController(
            self.observations,
            self.backend,
            self.logger,
            self.stop,
            clock=self.clock,
            challenge_factory=lambda: "challenge-1",
        )
        self.controller.register_context(
            self.target, self.observation, self.interpretation
        )

    def approve(self, prepared: object) -> InputConfirmation:
        return InputConfirmation(
            prepared.action.action_id,
            prepared.challenge,
            True,
        )

    def test_clicks_accessible_control_after_one_time_confirmation(self) -> None:
        requested = action(
            self.observation.observation_id, self.target, self.element
        )
        prepared = self.controller.prepare(requested)

        result = self.controller.execute(prepared, self.approve(prepared))

        self.assertIs(result.status, InputStatus.SUCCEEDED)
        self.assertIs(result.method, InputMethod.ACCESSIBILITY)
        self.assertIn(("click_accessible", 300), self.backend.calls)
        self.assertEqual(result.current_target.revision, 1)
        with self.assertRaises(InputControlError):
            self.controller.prepare(requested)

    def test_confirmation_is_bound_expires_and_cannot_be_reused(self) -> None:
        requested = action(
            self.observation.observation_id, self.target, self.element
        )
        prepared = self.controller.prepare(requested)
        wrong = InputConfirmation(requested.action_id, "wrong", True)

        rejected = self.controller.execute(prepared, wrong)
        reused = self.controller.execute(prepared, self.approve(prepared))

        self.assertIs(rejected.status, InputStatus.REJECTED)
        self.assertIs(reused.status, InputStatus.REJECTED)
        self.assertNotIn(("click_accessible", 300), self.backend.calls)

        second = replace(requested, action_id="action-2")
        prepared_second = self.controller.prepare(second)
        self.clock.advance(31)
        expired = self.controller.execute(
            prepared_second, self.approve(prepared_second)
        )
        self.assertIs(expired.status, InputStatus.REJECTED)

    def test_human_rejection_has_no_focus_or_input_effect(self) -> None:
        requested = action(
            self.observation.observation_id, self.target, self.element
        )
        prepared = self.controller.prepare(requested)
        denied = InputConfirmation(
            requested.action_id, prepared.challenge, False
        )

        result = self.controller.execute(prepared, denied)

        self.assertIs(result.status, InputStatus.REJECTED)
        self.assertNotIn(("focus_window",), self.backend.calls)

    def test_coordinate_fallback_uses_only_registered_element_center(self) -> None:
        self.backend.control = None
        requested = action(
            self.observation.observation_id,
            self.target,
            self.element,
            fallback=True,
        )
        prepared = self.controller.prepare(requested)

        result = self.controller.execute(prepared, self.approve(prepared))

        self.assertIs(result.status, InputStatus.SUCCEEDED)
        self.assertIs(result.method, InputMethod.COORDINATE_FALLBACK)
        self.assertIn(("click_point", 60, 35), self.backend.calls)

    def test_coordinate_fallback_must_be_explicit(self) -> None:
        self.backend.control = None
        requested = action(
            self.observation.observation_id, self.target, self.element
        )
        prepared = self.controller.prepare(requested)

        result = self.controller.execute(prepared, self.approve(prepared))

        self.assertIs(result.status, InputStatus.FAILED)
        self.assertFalse(
            any(call[0] == "click_point" for call in self.backend.calls)
        )

    def test_types_after_exact_focus_without_logging_text(self) -> None:
        private_text = "Cliente ficticio 123"
        text_element = replace(self.element, role=VisualRole.TEXT_FIELD)
        self.backend.control = replace(
            self.backend.control, role=VisualRole.TEXT_FIELD
        )
        interpretation = replace(
            self.interpretation, elements=(text_element,)
        )
        self.controller.register_context(
            self.target, self.observation, interpretation
        )
        requested = action(
            self.observation.observation_id,
            self.target,
            text_element,
            kind=InputKind.TYPE_TEXT,
            text=private_text,
        )
        prepared = self.controller.prepare(requested)

        result = self.controller.execute(prepared, self.approve(prepared))

        self.assertIs(result.status, InputStatus.SUCCEEDED)
        self.assertIn(("focus_control", 300), self.backend.calls)
        self.assertIn(("type_text", 300, private_text), self.backend.calls)
        self.assertIn(("verify_text", 300, private_text), self.backend.calls)
        self.assertNotIn(private_text, self.log_output.getvalue())
        self.assertNotIn(self.element.label, self.log_output.getvalue())

    def test_blocks_password_control_and_control_characters(self) -> None:
        text_element = replace(self.element, role=VisualRole.TEXT_FIELD)
        interpretation = replace(
            self.interpretation, elements=(text_element,)
        )
        self.controller.register_context(
            self.target, self.observation, interpretation
        )
        invalid_text = action(
            self.observation.observation_id,
            self.target,
            text_element,
            kind=InputKind.TYPE_TEXT,
            text="texto\nenter",
        )
        with self.assertRaises(InputControlError):
            self.controller.prepare(invalid_text)

        self.backend.control = AccessibleControl(
            300,
            VisualRole.TEXT_FIELD,
            CaptureRegion(20, 20, 80, 30),
            True,
            True,
            True,
        )
        safe_text = replace(invalid_text, action_id="action-2", text="dato")
        prepared = self.controller.prepare(safe_text)
        result = self.controller.execute(prepared, self.approve(prepared))
        self.assertIs(result.status, InputStatus.FAILED)
        self.assertFalse(any(call[0] == "type_text" for call in self.backend.calls))

    def test_emergency_cancels_at_safe_boundary(self) -> None:
        self.backend.on_resolve = self.stop.trigger
        requested = action(
            self.observation.observation_id, self.target, self.element
        )
        prepared = self.controller.prepare(requested)

        result = self.controller.execute(prepared, self.approve(prepared))

        self.assertIs(result.status, InputStatus.CANCELLED)
        self.assertIsNotNone(result.current_target)
        self.assertFalse(
            any(call[0].startswith("click") for call in self.backend.calls)
        )

    def test_focus_loss_during_resolution_stops_before_delivery(self) -> None:
        self.backend.on_resolve = lambda: setattr(
            self.backend,
            "state",
            replace(self.backend.state, foreground=False),
        )
        requested = action(
            self.observation.observation_id, self.target, self.element
        )
        prepared = self.controller.prepare(requested)

        result = self.controller.execute(prepared, self.approve(prepared))

        self.assertIs(result.status, InputStatus.FAILED)
        self.assertEqual(result.failure_stage, "pre_delivery_validation")
        self.assertFalse(
            any(call[0].startswith("click") for call in self.backend.calls)
        )

    def test_stops_before_input_on_focus_loss_modal_or_blocked_context(self) -> None:
        unsafe_states = [
            replace(self.backend.state, blocked_context=True),
            replace(self.backend.state, unknown_modal=True),
            replace(self.backend.state, process_id=999),
        ]

        for index, state in enumerate(unsafe_states, start=1):
            with self.subTest(state=state):
                target = make_target(f"window-{index}")
                backend = FakeInputBackend(target)
                backend.state = replace(
                    state,
                    process_id=(999 if state.process_id == 999 else target.process_id),
                )
                observations = ObservationService(
                    self.capture_backend,
                    logging.getLogger(f"unsafe-{index}"),
                    limits=ObservationLimits(
                        max_width=200,
                        max_height=100,
                        max_pixels=20_000,
                    ),
                    id_factory=lambda: f"obs-{index}",
                )
                observed = observations.capture(target)
                element = visual_element(observed.observation_id, target)
                interpretation = replace(
                    self.interpretation, elements=(element,)
                )
                controller = InputController(
                    observations,
                    backend,
                    self.logger,
                    EmergencyStop(),
                    challenge_factory=lambda: f"challenge-{index}",
                )
                controller.register_context(target, observed, interpretation)
                requested = action(
                    observed.observation_id,
                    target,
                    element,
                    action_id=f"action-{index}",
                )
                prepared = controller.prepare(requested)
                confirmed = InputConfirmation(
                    requested.action_id, prepared.challenge, True
                )

                result = controller.execute(prepared, confirmed)

                self.assertIs(result.status, InputStatus.FAILED)
                self.assertFalse(
                    any(call[0].startswith("click") for call in backend.calls)
                )

    def test_modal_after_delivery_stops_and_invalidates_previous_state(self) -> None:
        self.backend.modal_after_input = True
        requested = action(
            self.observation.observation_id, self.target, self.element
        )
        prepared = self.controller.prepare(requested)

        result = self.controller.execute(prepared, self.approve(prepared))

        self.assertIs(result.status, InputStatus.STOPPED)
        self.assertIsNotNone(result.current_target)

    def test_input_budget_is_rechecked_after_confirmation(self) -> None:
        identifiers = iter(("obs-a", "obs-b"))
        observations = ObservationService(
            self.capture_backend,
            logging.getLogger("input-budget"),
            limits=ObservationLimits(
                max_width=200,
                max_height=100,
                max_pixels=20_000,
                max_captures=2,
            ),
            id_factory=lambda: next(identifiers),
        )
        first_target = make_target("window-a")
        second_target = make_target("window-b")
        first_observation = observations.capture(first_target)
        second_observation = observations.capture(second_target)
        first_element = visual_element(
            first_observation.observation_id, first_target
        )
        second_element = visual_element(
            second_observation.observation_id, second_target
        )
        backend = FakeInputBackend(first_target)
        controller = InputController(
            observations,
            backend,
            self.logger,
            EmergencyStop(),
            limits=InputLimits(max_actions=1),
            challenge_factory=lambda: "budget-challenge",
        )
        controller.register_context(
            first_target,
            first_observation,
            replace(self.interpretation, elements=(first_element,)),
        )
        controller.register_context(
            second_target,
            second_observation,
            replace(self.interpretation, elements=(second_element,)),
        )
        first_action = action(
            first_observation.observation_id,
            first_target,
            first_element,
            action_id="budget-1",
        )
        second_action = action(
            second_observation.observation_id,
            second_target,
            second_element,
            action_id="budget-2",
        )
        first_prepared = controller.prepare(first_action)
        second_prepared = controller.prepare(second_action)

        first_result = controller.execute(
            first_prepared,
            InputConfirmation("budget-1", "budget-challenge", True),
        )
        second_result = controller.execute(
            second_prepared,
            InputConfirmation("budget-2", "budget-challenge", True),
        )

        self.assertIs(first_result.status, InputStatus.SUCCEEDED)
        self.assertIs(second_result.status, InputStatus.FAILED)
        clicks = [call for call in backend.calls if call[0] == "click_accessible"]
        self.assertEqual(len(clicks), 1)

    def test_rejects_fabricated_or_unsafe_actions(self) -> None:
        with self.assertRaises(ValueError):
            InputAction(
                "private text\n",
                InputKind.CLICK_ELEMENT,
                self.target.window_id,
                0,
                self.observation.observation_id,
                self.element.element_id,
            )
        with self.assertRaises(ValueError):
            replace(
                action(
                    self.observation.observation_id,
                    self.target,
                    self.element,
                ),
                risk_level=RiskLevel.SAFE,
            )
        fabricated = replace(
            action(
                self.observation.observation_id,
                self.target,
                self.element,
            ),
            element_id="obs-test:element-999",
        )
        with self.assertRaises(InputControlError):
            self.controller.prepare(fabricated)

    def test_backend_exception_is_redacted_and_invalidates_after_focus(self) -> None:
        secret = "backend secret detail"

        def fail() -> None:
            raise RuntimeError(secret)

        self.backend.on_resolve = fail
        requested = action(
            self.observation.observation_id, self.target, self.element
        )
        prepared = self.controller.prepare(requested)

        result = self.controller.execute(prepared, self.approve(prepared))

        self.assertIs(result.status, InputStatus.FAILED)
        self.assertIsNotNone(result.current_target)
        self.assertNotIn(secret, result.message)
        self.assertNotIn(secret, self.log_output.getvalue())


if __name__ == "__main__":
    unittest.main()
