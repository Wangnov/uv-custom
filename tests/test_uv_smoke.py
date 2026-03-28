import unittest

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
            "https://uv.agentsmirror.com/metadata/python-downloads.json",
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
