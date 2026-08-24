import unittest

from desktop_agent.models import Intent, RiskLevel
from desktop_agent.parser import parse_command


class ParseCommandTests(unittest.TestCase):
    def test_parses_each_supported_site(self) -> None:
        expected_urls = {
            "abrir youtube": "https://www.youtube.com/",
            "abrir google": "https://www.google.com/",
            "abrir github": "https://github.com/",
        }

        for command, expected_url in expected_urls.items():
            with self.subTest(command=command):
                action = parse_command(command)
                self.assertIsNotNone(action)
                assert action is not None
                self.assertEqual(action.intent, Intent.OPEN_URL)
                self.assertEqual(action.tool_name, "open_url")
                self.assertEqual(action.arguments["url"], expected_url)
                self.assertEqual(action.risk_level, RiskLevel.SAFE)
                self.assertFalse(action.requires_confirmation)

    def test_normalizes_accents_case_and_spaces(self) -> None:
        action = parse_command("  ABRÍ    YouTube  ")

        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.arguments["url"], "https://www.youtube.com/")

    def test_rejects_unknown_command(self) -> None:
        self.assertIsNone(parse_command("preparame un café"))

    def test_rejects_unknown_site(self) -> None:
        self.assertIsNone(parse_command("abrir sitio-inventado"))


if __name__ == "__main__":
    unittest.main()

