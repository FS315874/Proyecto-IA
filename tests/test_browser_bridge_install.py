import json
import tempfile
import unittest
from pathlib import Path

from desktop_agent.browser_bridge import EXTENSION_ID, NATIVE_HOST_NAME
from desktop_agent.browser_bridge_install import (
    REGISTRY_SUBKEY,
    BrowserBridgeInstallError,
    install_native_host,
    uninstall_native_host,
)


class FakeRegistry:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set_default(self, subkey: str, value: str) -> None:
        self.values[subkey] = value

    def read_default(self, subkey: str) -> str | None:
        return self.values.get(subkey)

    def delete(self, subkey: str) -> None:
        self.values.pop(subkey, None)


class BrowserBridgeInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.host = self.base / "desktop-agent-browser-host.exe"
        self.host.write_bytes(b"test")
        self.manifest = self.base / "native_host.json"
        self.registry = FakeRegistry()

    def test_install_writes_exact_host_and_pinned_extension_origin(self) -> None:
        status = install_native_host(
            host_executable=self.host,
            manifest_path=self.manifest,
            registry=self.registry,
        )

        value = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertTrue(status.ready)
        self.assertEqual(value["name"], NATIVE_HOST_NAME)
        self.assertEqual(value["path"], str(self.host))
        self.assertEqual(
            value["allowed_origins"],
            [f"chrome-extension://{EXTENSION_ID}/"],
        )
        self.assertEqual(self.registry.values[REGISTRY_SUBKEY], str(self.manifest))

    def test_uninstall_removes_only_owned_manifest_and_registry_key(self) -> None:
        install_native_host(
            host_executable=self.host,
            manifest_path=self.manifest,
            registry=self.registry,
        )

        uninstall_native_host(self.manifest, self.registry)

        self.assertFalse(self.manifest.exists())
        self.assertNotIn(REGISTRY_SUBKEY, self.registry.values)
        self.assertTrue(self.host.exists())

    def test_rejects_non_executable_host(self) -> None:
        invalid = self.base / "host.py"
        invalid.write_text("pass", encoding="utf-8")
        with self.assertRaises(BrowserBridgeInstallError):
            install_native_host(
                host_executable=invalid,
                manifest_path=self.manifest,
                registry=self.registry,
            )


if __name__ == "__main__":
    unittest.main()
