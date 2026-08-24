import unittest

from desktop_agent.tools.applications import open_application


class OpenApplicationTests(unittest.TestCase):
    def test_opens_application_from_known_path(self) -> None:
        started_executables: list[str] = []
        expected_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

        result = open_application(
            "chrome",
            path_checker=lambda path: path == expected_path,
            finder=lambda _: None,
            starter=started_executables.append,
        )

        self.assertTrue(result.success)
        self.assertEqual(started_executables, [expected_path])

    def test_falls_back_to_path_lookup(self) -> None:
        started_executables: list[str] = []

        result = open_application(
            "calculator",
            path_checker=lambda _: False,
            finder=lambda name: r"C:\Windows\System32\calc.exe"
            if name == "calc.exe"
            else None,
            starter=started_executables.append,
        )

        self.assertTrue(result.success)
        self.assertEqual(
            started_executables,
            [r"C:\Windows\System32\calc.exe"],
        )

    def test_rejects_application_outside_allowlist(self) -> None:
        starter_called = False

        def fake_starter(_: str) -> None:
            nonlocal starter_called
            starter_called = True

        result = open_application("powershell", starter=fake_starter)

        self.assertFalse(result.success)
        self.assertFalse(starter_called)
        self.assertIn("no está permitida", result.message)

    def test_reports_when_application_is_not_installed(self) -> None:
        result = open_application(
            "chrome",
            path_checker=lambda _: False,
            finder=lambda _: None,
        )

        self.assertFalse(result.success)
        self.assertIn("No se encontró", result.message)

    def test_reports_operating_system_error(self) -> None:
        def failing_starter(_: str) -> None:
            raise OSError("fallo simulado")

        result = open_application(
            "vscode",
            path_checker=lambda _: False,
            finder=lambda _: r"C:\fake\Code.exe",
            starter=failing_starter,
        )

        self.assertFalse(result.success)
        self.assertIn("fallo simulado", result.message)


if __name__ == "__main__":
    unittest.main()
