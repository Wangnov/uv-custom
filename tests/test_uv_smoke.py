import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from scripts import uv_smoke


class SmokeScriptTests(unittest.TestCase):
    def test_default_urls_point_to_public_mirror(self) -> None:
        self.assertEqual(
            uv_smoke._default_index_url("https://uv.agentsmirror.com"),
            "https://uv.agentsmirror.com/pypi/simple",
        )
        self.assertEqual(
            uv_smoke._default_index_url("https://uv.agentsmirror.com/"),
            "https://uv.agentsmirror.com/pypi/simple",
        )
        self.assertEqual(
            uv_smoke._python_downloads_json_url("https://uv.agentsmirror.com"),
            "https://uv.agentsmirror.com/metadata/python-downloads-upstream.json",
        )
        self.assertEqual(
            uv_smoke._python_install_mirror("https://uv.agentsmirror.com"),
            "https://uv.agentsmirror.com/python-build-standalone/releases/download",
        )
        self.assertEqual(
            uv_smoke._pypy_install_mirror("https://uv.agentsmirror.com"),
            "https://uv.agentsmirror.com/pypy",
        )
        self.assertEqual(
            uv_smoke._ghe_base_url("https://uv.agentsmirror.com"),
            "https://uv.agentsmirror.com/github",
        )
        self.assertEqual(
            uv_smoke._ghe_base_url("https://uv.agentsmirror.com/"),
            "https://uv.agentsmirror.com/github",
        )

    def test_uv_receipt_path_prefers_xdg_config_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xdg = pathlib.Path(tmp) / "xdg"
            self.assertEqual(
                uv_smoke._uv_receipt_path({"XDG_CONFIG_HOME": str(xdg)}),
                xdg / "uv" / "uv-receipt.json",
            )

    def test_receipt_matches_uv_bin_checks_install_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            uv_bin = bin_dir / "uv"
            uv_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            uv_bin.chmod(0o755)
            receipt_path = root / "uv-receipt.json"
            receipt_path.write_text(
                json.dumps({"install_prefix": str(bin_dir)}),
                encoding="utf-8",
            )

            with mock.patch("scripts.uv_smoke.shutil.which", return_value=str(uv_bin)):
                self.assertTrue(uv_smoke._receipt_matches_uv_bin(receipt_path, "uv"))

            with mock.patch(
                "scripts.uv_smoke.shutil.which",
                return_value=str(root / "other" / "uv"),
            ):
                self.assertFalse(uv_smoke._receipt_matches_uv_bin(receipt_path, "uv"))

            self.assertFalse(
                uv_smoke._receipt_matches_uv_bin(root / "missing.json", "uv")
            )

    def test_default_add_steps_cover_heavy_smoke_chain(self) -> None:
        self.assertEqual(
            uv_smoke.DEFAULT_ADD_STEPS,
            (
                ("pillow==12.1.1", "orjson==3.11.7"),
                ("torch==2.11.0",),
                ("numpy==2.4.3",),
            ),
        )

    def test_expected_versions_match_pinned_dependencies(self) -> None:
        self.assertEqual(
            uv_smoke.EXPECTED_VERSIONS,
            {
                "orjson": "3.11.7",
                "pillow": "12.1.1",
                "numpy": "2.4.3",
                "torch": "2.11.0",
            },
        )


if __name__ == "__main__":
    unittest.main()
