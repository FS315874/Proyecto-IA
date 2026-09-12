import base64
import hashlib
import json
import unittest
from pathlib import Path

from desktop_agent.browser_bridge import EXTENSION_ID, NATIVE_HOST_NAME


ROOT = Path(__file__).resolve().parents[1]


def chrome_extension_id(public_key: str) -> str:
    digest = hashlib.sha256(base64.b64decode(public_key, validate=True)).digest()
    alphabet = "abcdefghijklmnop"
    return "".join(
        alphabet[nibble]
        for byte in digest[:16]
        for nibble in (byte >> 4, byte & 0x0F)
    )


class BrowserExtensionManifestTests(unittest.TestCase):
    def test_manifest_has_stable_id_and_minimum_permissions(self) -> None:
        manifest = json.loads(
            (ROOT / "browser_extension" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(chrome_extension_id(manifest["key"]), EXTENSION_ID)
        self.assertEqual(
            set(manifest["permissions"]),
            {"nativeMessaging", "scripting", "storage"},
        )
        self.assertEqual(
            manifest["host_permissions"],
            ["https://www.youtube.com/*"],
        )

    def test_worker_uses_fixed_operations_without_remote_code_execution(self) -> None:
        worker = (ROOT / "browser_extension" / "service_worker.js").read_text(
            encoding="utf-8"
        )

        self.assertIn(f'const HOST_NAME = "{NATIVE_HOST_NAME}"', worker)
        self.assertIn('operation === "youtube_stop"', worker)
        for forbidden in ("eval(", "new Function", "<all_urls>", "downloads"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, worker)


if __name__ == "__main__":
    unittest.main()
