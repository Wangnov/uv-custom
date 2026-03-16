import unittest

from uvmirror.installers import render_installers
from uvmirror.metadata import (
    keep_latest_runtime_builds,
    mirror_path_for_python_download_url,
    rewrite_python_download_url,
)
from uvmirror.uv_releases import prune_uv_tags


class MetadataTests(unittest.TestCase):
    def test_keep_latest_runtime_builds_selects_latest_per_runtime_name(self) -> None:
        entries = [
            {
                "name": "cpython",
                "build": "20260303",
                "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20260303/old.tar.gz",
            },
            {
                "name": "cpython",
                "build": "20260310",
                "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20260310/new.tar.gz",
            },
            {
                "name": "pypy",
                "build": "7.3.9",
                "url": "https://downloads.python.org/pypy/pypy3.9-v7.3.9-linux64.tar.bz2",
            },
            {
                "name": "pypy",
                "build": "7.3.20",
                "url": "https://downloads.python.org/pypy/pypy3.11-v7.3.20-linux64.tar.bz2",
            },
            {
                "name": "graalpy",
                "build": "25.0.1",
                "url": "https://github.com/oracle/graalpython/releases/download/graal-25.0.1/graalpy-old.tar.gz",
            },
            {
                "name": "graalpy",
                "build": "25.0.2",
                "url": "https://github.com/oracle/graalpython/releases/download/graal-25.0.2/graalpy-new.tar.gz",
            },
        ]

        selected = keep_latest_runtime_builds(entries)

        self.assertEqual(
            [entry["build"] for entry in selected],
            ["20260310", "7.3.20", "25.0.2"],
        )

    def test_rewrite_python_download_url_supports_all_upstreams(self) -> None:
        public_base_url = "https://uv.example.com"

        self.assertEqual(
            rewrite_python_download_url(
                "https://github.com/astral-sh/python-build-standalone/releases/download/20260310/file.tar.gz",
                public_base_url,
            ),
            "https://uv.example.com/python-build-standalone/releases/download/20260310/file.tar.gz",
        )
        self.assertEqual(
            rewrite_python_download_url(
                "https://downloads.python.org/pypy/pypy3.11-v7.3.20-linux64.tar.bz2",
                public_base_url,
            ),
            "https://uv.example.com/pypy/pypy3.11-v7.3.20-linux64.tar.bz2",
        )
        self.assertEqual(
            rewrite_python_download_url(
                "https://github.com/oracle/graalpython/releases/download/graal-25.0.2/graalpy.tar.gz",
                public_base_url,
            ),
            "https://uv.example.com/graalpython/releases/download/graal-25.0.2/graalpy.tar.gz",
        )

    def test_mirror_path_for_python_download_url_matches_expected_layout(self) -> None:
        self.assertEqual(
            mirror_path_for_python_download_url(
                "https://github.com/astral-sh/python-build-standalone/releases/download/20260310/file.tar.gz"
            ),
            "python-build-standalone/releases/download/20260310/file.tar.gz",
        )
        self.assertEqual(
            mirror_path_for_python_download_url(
                "https://downloads.python.org/pypy/pypy3.11-v7.3.20-linux64.tar.bz2"
            ),
            "pypy/pypy3.11-v7.3.20-linux64.tar.bz2",
        )
        self.assertEqual(
            mirror_path_for_python_download_url(
                "https://github.com/oracle/graalpython/releases/download/graal-25.0.2/graalpy.tar.gz"
            ),
            "graalpython/releases/download/graal-25.0.2/graalpy.tar.gz",
        )


class ReleaseTests(unittest.TestCase):
    def test_prune_uv_tags_keeps_latest_n(self) -> None:
        tags = ["0.10.10", "0.10.9", "0.10.8", "0.10.7"]

        stale = prune_uv_tags(tags, keep=2)

        self.assertEqual(stale, ["0.10.8", "0.10.7"])


class InstallerTests(unittest.TestCase):
    def test_render_installers_injects_public_base_url_and_default_index(self) -> None:
        rendered = render_installers(
            public_base_url="https://uv.example.com",
            default_index_url="https://pypi.tuna.tsinghua.edu.cn/simple",
        )

        self.assertIn("UV_INSTALLER_GITHUB_BASE_URL", rendered.shell)
        self.assertIn("https://uv.example.com/github", rendered.shell)
        self.assertIn("UV_DEFAULT_INDEX", rendered.shell)
        self.assertIn("https://pypi.tuna.tsinghua.edu.cn/simple", rendered.shell)
        self.assertIn(
            'curl -LsSf "$PUBLIC_BASE_URL/github/astral-sh/uv/releases/download/latest/uv-installer.sh" -o "$installer_file"',
            rendered.shell,
        )
        self.assertIn(
            'env UV_INSTALLER_GITHUB_BASE_URL="$PUBLIC_BASE_URL/github" sh "$installer_file"',
            rendered.shell,
        )
        self.assertIn('printf \'%s\\n\' "$line"', rendered.shell)
        self.assertIn(
            'python-install-mirror = "%s/python-build-standalone/releases/download"\\n',
            rendered.shell,
        )
        self.assertNotIn(
            'curl -LsSf "$PUBLIC_BASE_URL/github/astral-sh/uv/releases/download/latest/uv-installer.sh" | env UV_INSTALLER_GITHUB_BASE_URL="$PUBLIC_BASE_URL/github" sh',
            rendered.shell,
        )
        self.assertNotIn("printf '%s\n' \"$line\"", rendered.shell)
        self.assertIn("https://uv.example.com/github", rendered.powershell)
        self.assertIn("https://pypi.tuna.tsinghua.edu.cn/simple", rendered.powershell)
        self.assertIn(
            '$env:UV_INSTALLER_GITHUB_BASE_URL = "$PublicBaseUrl/github"',
            rendered.powershell,
        )
        self.assertLess(
            rendered.powershell.index(
                '$env:UV_INSTALLER_GITHUB_BASE_URL = "$PublicBaseUrl/github"'
            ),
            rendered.powershell.index(
                'irm "$PublicBaseUrl/github/astral-sh/uv/releases/download/latest/uv-installer.ps1" | iex'
            ),
        )


if __name__ == "__main__":
    unittest.main()
