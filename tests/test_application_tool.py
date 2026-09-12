import os
import unittest
from unittest.mock import patch

from desktop_agent.catalog import SUPPORTED_APPLICATIONS
from desktop_agent.tools.applications import (
    _is_any_process_running,
    open_application,
)


class OpenApplicationTests(unittest.TestCase):
    def test_opens_application_from_known_path(self) -> None:
        started_executables: list[str] = []
        expected_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

        result = open_application(
            "chrome",
            path_checker=lambda path: path == expected_path,
            finder=lambda _: None,
            starter=started_executables.append,
            process_checker=lambda names: names == ("chrome.exe",),
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
            process_checker=lambda names: "CalculatorApp.exe" in names,
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
        self.assertIn("No se pudo iniciar", result.message)
        self.assertNotIn("fallo simulado", result.message)

    def test_opens_spotify_from_per_user_installation(self) -> None:
        started: list[str] = []
        expected = r"C:\Users\qa\AppData\Roaming\Spotify\Spotify.exe"

        result = open_application(
            "spotify",
            path_checker=lambda path: path.endswith(r"Spotify\Spotify.exe"),
            finder=lambda _: None,
            starter=started.append,
            process_checker=lambda names: names == ("Spotify.exe",),
        )

        self.assertTrue(result.success)
        self.assertTrue(started[0].endswith(r"Spotify\Spotify.exe"))

    def test_verifies_process_after_a_bounded_retry(self) -> None:
        checks = iter((False, False, True, True))
        waits: list[float] = []

        result = open_application(
            "steam",
            path_checker=lambda path: path.endswith(r"Steam\steam.exe"),
            finder=lambda _: None,
            starter=lambda _: None,
            process_checker=lambda names: next(checks)
            and names == ("steam.exe",),
            waiter=waits.append,
        )

        self.assertTrue(result.success)
        self.assertEqual(waits, [0.25, 0.25, 0.25])
        self.assertIn("verificada", result.message)

    def test_transient_launcher_is_not_reported_as_running_application(self) -> None:
        checks = iter((True, False))
        result = open_application(
            "calculator", path_checker=lambda _: True, starter=lambda _: None,
            process_checker=lambda _: next(checks, False), waiter=lambda _: None,
        )
        self.assertFalse(result.success)

    def test_league_uses_riot_launcher_with_fixed_product_arguments(self) -> None:
        from unittest.mock import Mock
        starter = Mock()
        result = open_application(
            "league_of_legends", path_checker=lambda _: True, starter=starter,
            process_checker=lambda names: "LeagueClient.exe" in names, waiter=lambda _: None,
        )
        self.assertTrue(result.success)
        starter.assert_called_once_with(
            r"C:\Riot Games\Riot Client\RiotClientServices.exe",
            ("--launch-product=league_of_legends", "--launch-patchline=live"),
        )

    def test_access_denied_error_is_actionable_and_redacted(self) -> None:
        error = OSError("private-path-and-secret")
        error.winerror = 5
        with patch("desktop_agent.tools.applications.subprocess.Popen", side_effect=error):
            result = open_application("calculator", path_checker=lambda _: True)
        self.assertFalse(result.success)
        self.assertIn("denegó el acceso", result.message)
        self.assertNotIn("private-path", result.message)

    def test_riot_only_is_not_reported_as_league_ready(self) -> None:
        result = open_application(
            "league_of_legends", path_checker=lambda _: True, starter=lambda *args: None,
            process_checker=lambda names: names == ("RiotClientServices.exe",), waiter=lambda _: None,
        )
        self.assertFalse(result.success)
        self.assertIn("Riot Client quedó activo", result.message)

    @patch("desktop_agent.tools.applications.subprocess.Popen")
    def test_starter_uses_known_executable_folder_without_shell(self, popen) -> None:
        from desktop_agent.tools.applications import _start_process
        executable = os.path.abspath(os.path.join("fake-game", "game.exe"))
        _start_process(executable)
        popen.assert_called_once_with([executable], cwd=os.path.dirname(executable), close_fds=True)

    def test_fails_safely_when_started_process_cannot_be_verified(self) -> None:
        result = open_application(
            "voicemeeter",
            path_checker=lambda _: True,
            finder=lambda _: None,
            starter=lambda _: None,
            process_checker=lambda _: False,
            waiter=lambda _: None,
        )

        self.assertFalse(result.success)
        self.assertIn("no se pudo comprobar", result.message)

    def test_new_applications_use_only_fixed_catalog_entries(self) -> None:
        expected = {
            "steam": ("Steam", "steam.exe"),
            "voicemeeter": ("VoiceMeeter Banana", "voicemeeterpro.exe"),
            "league_of_legends": ("League of Legends", "LeagueClient.exe"),
            "god_of_war_ragnarok": ("God of War Ragnarök", "GoWR.exe"),
        }

        for key, (display_name, process_name) in expected.items():
            with self.subTest(key=key):
                application = SUPPORTED_APPLICATIONS[key]
                self.assertEqual(application.name, display_name)
                self.assertIn(process_name, application.process_names)
                self.assertTrue(application.windows_paths)
                self.assertTrue(application.executable_names)

    @patch(
        "desktop_agent.tools.applications._snapshot_process_names",
        return_value=frozenset({"steam.exe"}),
    )
    def test_process_probe_uses_exact_catalog_name(self, snapshot) -> None:
        self.assertTrue(_is_any_process_running(("steam.exe",)))
        self.assertFalse(_is_any_process_running(("team.exe",)))
        self.assertEqual(snapshot.call_count, 2)

    @unittest.skipUnless(os.name == "nt", "requiere enumeración de procesos de Windows")
    def test_native_process_probe_detects_current_python(self) -> None:
        self.assertTrue(_is_any_process_running(("python.exe",)))


if __name__ == "__main__":
    unittest.main()
