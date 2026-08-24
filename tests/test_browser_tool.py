import unittest

from desktop_agent.tools.browser import open_url


class OpenUrlTests(unittest.TestCase):
    def test_opens_valid_url_through_injected_opener(self) -> None:
        opened_urls: list[str] = []

        def fake_opener(url: str) -> bool:
            opened_urls.append(url)
            return True

        result = open_url("https://www.youtube.com/", opener=fake_opener)

        self.assertTrue(result.success)
        self.assertEqual(opened_urls, ["https://www.youtube.com/"])

    def test_rejects_non_http_scheme_without_calling_opener(self) -> None:
        opener_called = False

        def fake_opener(url: str) -> bool:
            nonlocal opener_called
            opener_called = True
            return True

        result = open_url("file:///private.txt", opener=fake_opener)

        self.assertFalse(result.success)
        self.assertFalse(opener_called)

    def test_reports_when_system_cannot_open_browser(self) -> None:
        result = open_url("https://github.com/", opener=lambda _: False)

        self.assertFalse(result.success)
        self.assertIn("no confirmó", result.message)


if __name__ == "__main__":
    unittest.main()

