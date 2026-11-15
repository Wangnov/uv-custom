import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
import urllib.error

from uvmirror.downloads import download_python_assets
from uvmirror.installers import render_installers
from uvmirror.metadata import (
    build_rewritten_python_metadata,
    build_state_manifest,
    diff_stale_keys,
    keep_latest_runtime_builds,
    mirror_path_for_python_download_url,
    rewrite_python_download_url,
    state_manifest_file_sizes,
)
from uvmirror.s3_upload import S3MirrorUploader
from uvmirror.uv_releases import (
    build_uv_releases_api_bundle,
    prune_uv_tags,
    rewrite_uv_release_payload,
)


class MetadataTests(unittest.TestCase):
    def test_build_state_manifest_sorts_and_wraps_keys(self) -> None:
        manifest = build_state_manifest(
            [
                "python-build-standalone/releases/download/20260310/b.tar.zst",
                "python-build-standalone/releases/download/20260310/a.tar.zst",
            ]
        )

        self.assertEqual(
            manifest,
            {
                "keys": [
                    "python-build-standalone/releases/download/20260310/a.tar.zst",
                    "python-build-standalone/releases/download/20260310/b.tar.zst",
                ]
            },
        )

    def test_diff_stale_keys_returns_removed_keys_only(self) -> None:
        stale = diff_stale_keys(
            previous_manifest={
                "keys": [
                    "python-build-standalone/releases/download/20260303/old.tar.zst",
                    "python-build-standalone/releases/download/20260310/current.tar.zst",
                ]
            },
            current_keys=[
                "python-build-standalone/releases/download/20260310/current.tar.zst",
                "metadata/python-downloads.json",
            ],
        )

        self.assertEqual(
            stale,
            ["python-build-standalone/releases/download/20260303/old.tar.zst"],
        )

    def test_build_state_manifest_can_record_file_sizes(self) -> None:
        manifest = build_state_manifest(
            [
                {
                    "key": "python-build-standalone/releases/download/20260310/a.tar.zst",
                    "size": 123,
                },
                {
                    "key": "python-build-standalone/releases/download/20260310/b.tar.zst",
                    "size": 456,
                },
            ]
        )

        self.assertEqual(
            manifest,
            {
                "files": [
                    {
                        "key": "python-build-standalone/releases/download/20260310/a.tar.zst",
                        "size": 123,
                    },
                    {
                        "key": "python-build-standalone/releases/download/20260310/b.tar.zst",
                        "size": 456,
                    },
                ],
                "keys": [
                    "python-build-standalone/releases/download/20260310/a.tar.zst",
                    "python-build-standalone/releases/download/20260310/b.tar.zst",
                ],
            },
        )

    def test_state_manifest_file_sizes_falls_back_to_legacy_keys(self) -> None:
        self.assertEqual(
            state_manifest_file_sizes(
                {
                    "keys": [
                        "python-build-standalone/releases/download/20260310/a.tar.zst",
                    ]
                }
            ),
            {"python-build-standalone/releases/download/20260310/a.tar.zst": None},
        )

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
                "https://github.com/astral-sh/python-build-standalone/releases/download/20260310/cpython-3.12.13%2B20260310-aarch64-apple-darwin-install_only_stripped.tar.gz",
                public_base_url,
            ),
            "https://uv.example.com/python-build-standalone/releases/download/20260310/cpython-3.12.13-plus-20260310-aarch64-apple-darwin-install_only_stripped.tar.gz",
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
                "https://github.com/astral-sh/python-build-standalone/releases/download/20260310/cpython-3.12.13%2B20260310-aarch64-apple-darwin-install_only_stripped.tar.gz"
            ),
            "python-build-standalone/releases/download/20260310/cpython-3.12.13-plus-20260310-aarch64-apple-darwin-install_only_stripped.tar.gz",
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

    def test_build_rewritten_python_metadata_can_keep_upstream_urls(self) -> None:
        upstream_url = (
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
            "20260718/cpython-3.12.13%2B20260718-aarch64-apple-darwin-install_only_stripped.tar.gz"
        )
        raw = {"cpython-3.12.13-darwin-aarch64-none": {"url": upstream_url, "name": "cpython"}}

        rewritten = build_rewritten_python_metadata(raw, "https://uv.example.com")
        self.assertEqual(
            rewritten["cpython-3.12.13-darwin-aarch64-none"]["url"],
            "https://uv.example.com/python-build-standalone/releases/download/20260718/"
            "cpython-3.12.13-plus-20260718-aarch64-apple-darwin-install_only_stripped.tar.gz",
        )

        # uv only accepts UV_PYTHON_INSTALL_MIRROR when the entry still carries the
        # canonical upstream prefix, so this variant must be left untouched.
        kept = build_rewritten_python_metadata(
            raw, "https://uv.example.com", keep_upstream_urls=True
        )
        self.assertEqual(
            kept["cpython-3.12.13-darwin-aarch64-none"]["url"], upstream_url
        )
        self.assertEqual(kept["cpython-3.12.13-darwin-aarch64-none"]["name"], "cpython")


class ReleaseTests(unittest.TestCase):
    def test_prune_uv_tags_keeps_latest_n(self) -> None:
        tags = ["0.10.10", "0.10.9", "0.10.8", "0.10.7"]

        stale = prune_uv_tags(tags, keep=2)

        self.assertEqual(stale, ["0.10.8", "0.10.7"])

    def test_rewrite_uv_release_payload_rewrites_assets_and_api_urls(self) -> None:
        raw = {
            "url": "https://api.github.com/repos/astral-sh/uv/releases/123",
            "assets_url": "https://api.github.com/repos/astral-sh/uv/releases/123/assets",
            "html_url": "https://github.com/astral-sh/uv/releases/tag/0.13.8",
            "tag_name": "0.13.8",
            "name": "0.13.8",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-11-12T17:03:07Z",
            "author": {"login": "bot"},
            "reactions": {"total_count": 5},
            "body": "Release notes",
            "assets": [
                {
                    "name": "uv-installer.sh",
                    "size": 1000,
                    "content_type": "application/x-sh",
                    "url": "https://api.github.com/repos/astral-sh/uv/releases/assets/99",
                    "browser_download_url": "https://github.com/astral-sh/uv/releases/download/0.13.8/uv-installer.sh",
                    "uploader": {"login": "bot"},
                }
            ],
        }

        rewritten = rewrite_uv_release_payload(raw, "https://uv.example.com/")
        self.assertEqual(
            rewritten["url"],
            "https://uv.example.com/api/v3/repos/astral-sh/uv/releases/123",
        )
        self.assertEqual(
            rewritten["html_url"],
            "https://github.com/astral-sh/uv/releases/tag/0.13.8",
        )
        self.assertNotIn("author", rewritten)
        self.assertNotIn("reactions", rewritten)
        self.assertEqual(rewritten["body"], "Release notes")
        asset = rewritten["assets"][0]
        self.assertNotIn("uploader", asset)
        self.assertEqual(
            asset["browser_download_url"],
            "https://uv.example.com/github/astral-sh/uv/releases/download/0.13.8/uv-installer.sh",
        )
        self.assertEqual(
            asset["url"],
            "https://uv.example.com/api/v3/repos/astral-sh/uv/releases/assets/99",
        )

    def test_build_uv_releases_api_bundle_outputs_list_latest_and_tag(self) -> None:
        release = {
            "url": "https://api.github.com/repos/astral-sh/uv/releases/123",
            "tag_name": "0.13.8",
            "name": "0.13.8",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-11-12T17:03:07Z",
            "body": "Notes",
            "assets": [
                {
                    "name": "uv-installer.sh",
                    "size": 1000,
                    "content_type": "application/x-sh",
                    "url": "https://api.github.com/repos/astral-sh/uv/releases/assets/99",
                    "browser_download_url": "https://github.com/astral-sh/uv/releases/download/0.13.8/uv-installer.sh",
                }
            ],
        }

        older = {
            **release,
            "tag_name": "0.13.7",
            "name": "0.13.7",
        }
        bundle = build_uv_releases_api_bundle(
            [release, older], release, "https://uv.example.com"
        )
        self.assertEqual(
            set(bundle.keys()),
            {
                "github/api/v3/repos/astral-sh/uv/releases.json",
                "github/api/v3/repos/astral-sh/uv/releases/latest.json",
                "github/api/v3/repos/astral-sh/uv/releases/tags/0.13.8.json",
                "github/api/v3/repos/astral-sh/uv/releases/tags/0.13.7.json",
            },
        )
        self.assertNotIn(
            "body",
            bundle["github/api/v3/repos/astral-sh/uv/releases.json"][0],
        )
        self.assertEqual(
            bundle["github/api/v3/repos/astral-sh/uv/releases/latest.json"]["body"],
            "Notes",
        )

    def test_build_uv_releases_api_bundle_skips_unsafe_tag_key(self) -> None:
        release = {
            "url": "https://api.github.com/repos/astral-sh/uv/releases/123",
            "tag_name": "../escape",
            "assets": [],
        }
        bundle = build_uv_releases_api_bundle(
            [release], release, "https://uv.example.com"
        )
        self.assertEqual(
            set(bundle.keys()),
            {
                "github/api/v3/repos/astral-sh/uv/releases.json",
                "github/api/v3/repos/astral-sh/uv/releases/latest.json",
            },
        )


class InstallerTests(unittest.TestCase):
    def test_render_installers_injects_public_base_url_and_default_index(self) -> None:
        rendered = render_installers(
            public_base_url="https://uv.example.com",
            default_index_url="https://pypi.tuna.tsinghua.edu.cn/simple",
        )

        self.assertIn(
            'export UV_INSTALLER_GHE_BASE_URL="${UV_INSTALLER_GHE_BASE_URL:-https://uv.example.com/github}"',
            rendered.shell,
        )
        self.assertNotIn("export UV_INSTALLER_GITHUB_BASE_URL=", rendered.shell)
        self.assertIn("https://uv.example.com/github", rendered.shell)
        self.assertIn("UV_PYTHON_DOWNLOADS_JSON_URL", rendered.shell)
        self.assertIn("UV_DEFAULT_INDEX", rendered.shell)
        self.assertIn("https://pypi.tuna.tsinghua.edu.cn/simple", rendered.shell)
        self.assertIn(
            'curl -LsSf "$PUBLIC_BASE_URL/github/astral-sh/uv/releases/download/latest/uv-installer.sh" -o "$installer_file"',
            rendered.shell,
        )
        self.assertIn(
            'env -u UV_INSTALLER_GITHUB_BASE_URL UV_INSTALLER_GHE_BASE_URL="$PUBLIC_BASE_URL/github" sh "$installer_file"',
            rendered.shell,
        )
        self.assertIn('printf \'%s\\n\' "$line"', rendered.shell)
        self.assertIn(
            'python-downloads-json-url = "%s/metadata/python-downloads-upstream.json"\\n',
            rendered.shell,
        )
        self.assertIn(
            'python-downloads-json-url\\ =*|python-install-mirror\\ =*|pypy-install-mirror\\ =*)',
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
            'Remove-Item Env:UV_INSTALLER_GITHUB_BASE_URL -ErrorAction SilentlyContinue',
            rendered.powershell,
        )
        self.assertIn(
            '$env:UV_INSTALLER_GHE_BASE_URL = "$PublicBaseUrl/github"',
            rendered.powershell,
        )
        self.assertIn(
            '$env:UV_INSTALLER_GHE_BASE_URL = "https://uv.example.com/github"',
            rendered.powershell,
        )
        self.assertNotIn(
            '$env:UV_INSTALLER_GITHUB_BASE_URL = "$PublicBaseUrl/github"',
            rendered.powershell,
        )
        self.assertIn(
            '$env:UV_PYTHON_DOWNLOADS_JSON_URL = "$PublicBaseUrl/metadata/python-downloads-upstream.json"',
            rendered.powershell,
        )
        self.assertIn(
            'if ($Line.Trim().StartsWith("python-install-mirror =")) { continue }',
            rendered.powershell,
        )
        self.assertIn(
            'if ($Line.Trim().StartsWith("pypy-install-mirror =")) { continue }',
            rendered.powershell,
        )
        self.assertLess(
            rendered.powershell.index(
                'Remove-Item Env:UV_INSTALLER_GITHUB_BASE_URL -ErrorAction SilentlyContinue'
            ),
            rendered.powershell.index(
                '$env:UV_INSTALLER_GHE_BASE_URL = "$PublicBaseUrl/github"'
            ),
        )
        self.assertLess(
            rendered.powershell.index(
                '$env:UV_INSTALLER_GHE_BASE_URL = "$PublicBaseUrl/github"'
            ),
            rendered.powershell.index(
                'irm "$PublicBaseUrl/github/astral-sh/uv/releases/download/latest/uv-installer.ps1" | iex'
            ),
        )

    def test_shell_managed_block_migrates_legacy_github_base_url(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX shell test")

        rendered = render_installers(
            public_base_url="https://uv.example.com",
            default_index_url="https://uv.example.com/pypi/simple",
        )
        start = rendered.shell.index("# >>> uv mirror managed block >>>")
        end = rendered.shell.index("# <<< uv mirror managed block <<<") + len(
            "# <<< uv mirror managed block <<<"
        )
        managed_block = rendered.shell[start:end]

        def eval_block(extra_env: dict[str, str]) -> dict[str, str]:
            env = {"PATH": os.environ["PATH"], **extra_env}
            proc = subprocess.run(
                [
                    "sh",
                    "-eu",
                    "-c",
                    f'{managed_block}\nprintf "GH=%s\\nGHE=%s\\n" "${{UV_INSTALLER_GITHUB_BASE_URL:-}}" "${{UV_INSTALLER_GHE_BASE_URL:-}}"',
                ],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            return dict(line.split("=", 1) for line in proc.stdout.strip().splitlines())

        self.assertEqual(
            eval_block({"UV_INSTALLER_GITHUB_BASE_URL": "https://uv.example.com/github"}),
            {"GH": "", "GHE": "https://uv.example.com/github"},
        )
        self.assertEqual(
            eval_block({"UV_INSTALLER_GITHUB_BASE_URL": "https://custom.example.com/github"}),
            {"GH": "https://custom.example.com/github", "GHE": ""},
        )
        self.assertEqual(
            eval_block({"UV_INSTALLER_GHE_BASE_URL": "https://custom-ghe.example.com/github"}),
            {"GH": "", "GHE": "https://custom-ghe.example.com/github"},
        )

    def test_render_installers_ships_overridable_python_install_mirrors(self) -> None:
        """The upstream-URL metadata endpoint makes UV_PYTHON_INSTALL_MIRROR usable again.

        It was dropped in 9a4ebd8 because rewritten URLs made uv fail with
        ``Error::Mirror``; serving upstream URLs removes that conflict.
        """
        rendered = render_installers(
            public_base_url="https://uv.example.com",
            default_index_url="https://pypi.tuna.tsinghua.edu.cn/simple",
        )

        # Defaults are only applied when the user has not set their own value.
        self.assertIn(
            'export UV_PYTHON_INSTALL_MIRROR="${UV_PYTHON_INSTALL_MIRROR:-'
            'https://uv.example.com/python-build-standalone/releases/download}"',
            rendered.shell,
        )
        self.assertIn(
            'export UV_PYPY_INSTALL_MIRROR="${UV_PYPY_INSTALL_MIRROR:-'
            'https://uv.example.com/pypy}"',
            rendered.shell,
        )
        self.assertIn(
            'export UV_PYTHON_DOWNLOADS_JSON_URL="${UV_PYTHON_DOWNLOADS_JSON_URL:-'
            'https://uv.example.com/metadata/python-downloads-upstream.json}"',
            rendered.shell,
        )
        self.assertIn(
            'python-install-mirror = "%s/python-build-standalone/releases/download"\\n',
            rendered.shell,
        )
        self.assertIn('pypy-install-mirror = "%s/pypy"\\n', rendered.shell)

        self.assertIn(
            'if (-not $env:UV_PYTHON_INSTALL_MIRROR) { $env:UV_PYTHON_INSTALL_MIRROR = '
            '"https://uv.example.com/python-build-standalone/releases/download" }',
            rendered.powershell,
        )
        self.assertIn(
            'if (-not $env:UV_PYPY_INSTALL_MIRROR) { $env:UV_PYPY_INSTALL_MIRROR = '
            '"https://uv.example.com/pypy" }',
            rendered.powershell,
        )

    def test_render_installers_powershell_guards_against_empty_profile_content(self) -> None:
        rendered = render_installers(
            public_base_url="https://uv.example.com",
            default_index_url="https://pypi.tuna.tsinghua.edu.cn/simple",
        )

        self.assertIn('$RawContent = Get-Content -Path $Path -Raw', rendered.powershell)
        self.assertIn(
            '$Content = if ($null -eq $RawContent) { "" } else { $RawContent }',
            rendered.powershell,
        )

    def test_render_installers_powershell_managed_block_uses_literal_here_string(self) -> None:
        rendered = render_installers(
            public_base_url="https://uv.example.com",
            default_index_url="https://pypi.tuna.tsinghua.edu.cn/simple",
        )

        self.assertIn("$ManagedBlock = @'", rendered.powershell)
        self.assertNotIn('$ManagedBlock = @"', rendered.powershell)


class DownloadTests(unittest.TestCase):
    def test_download_python_assets_retries_http_502_then_succeeds(self) -> None:
        payload = b"payload"
        attempts = 0
        sleeps: list[float] = []
        source_url = "https://example.com/releases/download/20260310/file.tar.gz"

        def flaky_downloader(url: str, destination: pathlib.Path) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise urllib.error.HTTPError(url, 502, "Bad Gateway", hdrs=None, fp=None)
            destination.write_bytes(payload)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            manifest_path = root / "python-assets.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "source_url": source_url,
                            "mirror_path": "python-build-standalone/releases/download/20260310/file.tar.gz",
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    ]
                ),
                encoding="utf-8",
            )

            download_python_assets(
                manifest_path=manifest_path,
                stage_dir=root / "stage",
                max_attempts=3,
                backoff_seconds=5,
                request_interval=0,
                sleep=sleeps.append,
                downloader=flaky_downloader,
            )

            self.assertEqual(
                (root / "stage" / "python-build-standalone/releases/download/20260310/file.tar.gz").read_bytes(),
                payload,
            )

        self.assertEqual(attempts, 3)
        self.assertEqual(sleeps, [5.0, 10.0])

    def test_download_python_assets_reports_url_after_retries_exhausted(self) -> None:
        sleeps: list[float] = []
        source_url = "https://example.com/releases/download/20260310/file.tar.gz"

        def failing_downloader(url: str, destination: pathlib.Path) -> None:
            raise urllib.error.HTTPError(url, 502, "Bad Gateway", hdrs=None, fp=None)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            manifest_path = root / "python-assets.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "source_url": source_url,
                            "mirror_path": "python-build-standalone/releases/download/20260310/file.tar.gz",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit) as context:
                download_python_assets(
                    manifest_path=manifest_path,
                    stage_dir=root / "stage",
                    max_attempts=2,
                    backoff_seconds=3,
                    request_interval=0,
                    sleep=sleeps.append,
                    downloader=failing_downloader,
                )

        self.assertIn(source_url, str(context.exception))
        self.assertIn("after 2 attempts", str(context.exception))
        self.assertEqual(sleeps, [3.0])


class FakeBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class FakeRetryableError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status_code}}


class ReadTimeoutError(Exception):
    pass


class FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_put_attempts = 0
        self.fail_create_multipart_attempts = 0
        self.fail_put_errors: list[Exception] = []
        self.fail_upload_part_errors: list[Exception] = []
        self.get_payload: bytes | None = None

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))
        if self.fail_put_errors:
            raise self.fail_put_errors.pop(0)
        if self.fail_put_attempts > 0:
            self.fail_put_attempts -= 1
            raise FakeRetryableError(403)
        return {}

    def create_multipart_upload(self, **kwargs):
        self.calls.append(("create_multipart_upload", kwargs))
        if self.fail_create_multipart_attempts > 0:
            self.fail_create_multipart_attempts -= 1
            raise FakeRetryableError(403)
        return {"UploadId": "upload-1"}

    def upload_part(self, **kwargs):
        self.calls.append(("upload_part", kwargs))
        if self.fail_upload_part_errors:
            raise self.fail_upload_part_errors.pop(0)
        return {"ETag": f"etag-{kwargs['PartNumber']}"}

    def complete_multipart_upload(self, **kwargs):
        self.calls.append(("complete_multipart_upload", kwargs))
        return {}

    def abort_multipart_upload(self, **kwargs):
        self.calls.append(("abort_multipart_upload", kwargs))
        return {}

    def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        if self.get_payload is None:
            raise FileNotFoundError("missing")
        return {"Body": FakeBody(self.get_payload)}

    def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))
        return {}


class UploadTests(unittest.TestCase):
    def test_sync_directory_with_state_deletes_only_stale_keys(self) -> None:
        client = FakeS3Client()
        client.get_payload = (
            b'{\n  "keys": [\n'
            b'    "python-build-standalone/releases/download/20260303/old.tar.zst",\n'
            b'    "python-build-standalone/releases/download/20260310/current.tar.zst"\n'
            b"  ]\n}\n"
        )
        uploader = S3MirrorUploader(
            client=client,
            bucket="bucket",
            multipart_threshold=64,
            part_size=4,
            max_attempts=1,
            sleep=lambda _: None,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            current = root / "current.tar.zst"
            current.write_bytes(b"data")

            uploader.sync_directory_with_state(
                local_dir=root,
                remote_prefix="python-build-standalone/releases/download/20260310",
                cache_control="public, max-age=31536000, immutable",
                state_key="state/python-build-standalone.json",
            )

        self.assertEqual(
            [name for name, _ in client.calls],
            ["get_object", "put_object", "delete_object"],
        )
        self.assertEqual(
            client.calls[-1][1]["Key"],
            "python-build-standalone/releases/download/20260303/old.tar.zst",
        )

    def test_sync_directory_with_state_reuploads_when_recorded_size_changes(self) -> None:
        client = FakeS3Client()
        client.get_payload = (
            b'{\n'
            b'  "files": [\n'
            b'    {\n'
            b'      "key": "python-build-standalone/releases/download/20260310/current.tar.zst",\n'
            b'      "size": 1\n'
            b"    }\n"
            b"  ],\n"
            b'  "keys": [\n'
            b'    "python-build-standalone/releases/download/20260310/current.tar.zst"\n'
            b"  ]\n"
            b"}\n"
        )
        uploader = S3MirrorUploader(
            client=client,
            bucket="bucket",
            multipart_threshold=64,
            part_size=4,
            max_attempts=1,
            sleep=lambda _: None,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            current = root / "current.tar.zst"
            current.write_bytes(b"data")

            uploader.sync_directory_with_state(
                local_dir=root,
                remote_prefix="python-build-standalone/releases/download/20260310",
                cache_control="public, max-age=31536000, immutable",
                state_key="state/python-build-standalone.json",
            )

        self.assertEqual(
            [name for name, _ in client.calls],
            ["get_object", "put_object", "put_object"],
        )

    def test_small_file_uses_put_object_only(self) -> None:
        client = FakeS3Client()
        uploader = S3MirrorUploader(
            client=client,
            bucket="bucket",
            multipart_threshold=64,
            part_size=4,
            max_attempts=1,
            sleep=lambda _: None,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "installer.sh"
            path.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

            uploader.upload_file(
                local_path=path,
                key="github/astral-sh/uv/releases/download/latest/uv-installer.sh",
                cache_control="public, max-age=300",
            )

        self.assertEqual([name for name, _ in client.calls], ["put_object"])
        self.assertEqual(client.calls[0][1]["CacheControl"], "public, max-age=300")
        self.assertIn(client.calls[0][1]["ContentType"], {"application/x-sh", "text/x-sh"})

    def test_large_file_uses_serial_multipart_upload(self) -> None:
        client = FakeS3Client()
        uploader = S3MirrorUploader(
            client=client,
            bucket="bucket",
            multipart_threshold=4,
            part_size=3,
            max_attempts=1,
            sleep=lambda _: None,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "asset.bin"
            path.write_bytes(b"abcdefgh")

            uploader.upload_file(
                local_path=path,
                key="python-build-standalone/releases/download/20260310/asset.bin",
                cache_control="public, max-age=31536000, immutable",
            )

        self.assertEqual(
            [name for name, _ in client.calls],
            [
                "create_multipart_upload",
                "upload_part",
                "upload_part",
                "upload_part",
                "complete_multipart_upload",
            ],
        )
        self.assertEqual(
            [call[1]["PartNumber"] for call in client.calls if call[0] == "upload_part"],
            [1, 2, 3],
        )

    def test_multipart_access_denied_falls_back_to_streaming_put_object(self) -> None:
        client = FakeS3Client()
        client.fail_create_multipart_attempts = 1
        uploader = S3MirrorUploader(
            client=client,
            bucket="bucket",
            multipart_threshold=4,
            part_size=3,
            max_attempts=3,
            sleep=lambda _: None,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "asset.bin"
            path.write_bytes(b"abcdefgh")

            uploader.upload_file(
                local_path=path,
                key="python-build-standalone/releases/download/20260310/asset.bin",
                cache_control="public, max-age=31536000, immutable",
            )

        self.assertEqual(
            [name for name, _ in client.calls],
            ["create_multipart_upload", "put_object"],
        )

    def test_put_object_retries_before_succeeding(self) -> None:
        client = FakeS3Client()
        client.fail_put_attempts = 2
        sleeps: list[float] = []
        uploader = S3MirrorUploader(
            client=client,
            bucket="bucket",
            multipart_threshold=8,
            part_size=4,
            max_attempts=3,
            sleep=sleeps.append,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "metadata.json"
            path.write_text("{}\n", encoding="utf-8")

            uploader.upload_file(
                local_path=path,
                key="metadata/uv-latest.json",
                cache_control="public, max-age=300",
            )

        self.assertEqual([name for name, _ in client.calls], ["put_object", "put_object", "put_object"])
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_put_object_retries_read_timeouts_before_succeeding(self) -> None:
        client = FakeS3Client()
        client.fail_put_errors = [ReadTimeoutError("timed out"), ReadTimeoutError("timed out")]
        sleeps: list[float] = []
        uploader = S3MirrorUploader(
            client=client,
            bucket="bucket",
            multipart_threshold=8,
            part_size=4,
            max_attempts=3,
            sleep=sleeps.append,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "metadata.json"
            path.write_text("{}\n", encoding="utf-8")

            uploader.upload_file(
                local_path=path,
                key="metadata/uv-latest.json",
                cache_control="public, max-age=300",
            )

        self.assertEqual([name for name, _ in client.calls], ["put_object", "put_object", "put_object"])
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_multipart_upload_retries_read_timeouts_before_succeeding(self) -> None:
        client = FakeS3Client()
        client.fail_upload_part_errors = [ReadTimeoutError("timed out")]
        sleeps: list[float] = []
        uploader = S3MirrorUploader(
            client=client,
            bucket="bucket",
            multipart_threshold=4,
            part_size=3,
            max_attempts=2,
            sleep=sleeps.append,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "asset.bin"
            path.write_bytes(b"abcdefgh")

            uploader.upload_file(
                local_path=path,
                key="python-build-standalone/releases/download/20260310/asset.bin",
                cache_control="public, max-age=31536000, immutable",
            )

        self.assertEqual(
            [name for name, _ in client.calls],
            [
                "create_multipart_upload",
                "upload_part",
                "upload_part",
                "upload_part",
                "upload_part",
                "complete_multipart_upload",
            ],
        )
        self.assertEqual(sleeps, [1.0])

    def test_retry_backoff_is_capped(self) -> None:
        client = FakeS3Client()
        client.fail_put_attempts = 3
        sleeps: list[float] = []
        uploader = S3MirrorUploader(
            client=client,
            bucket="bucket",
            multipart_threshold=8,
            part_size=4,
            max_attempts=4,
            backoff_seconds=10,
            max_backoff_seconds=12,
            sleep=sleeps.append,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "metadata.json"
            path.write_text("{}\n", encoding="utf-8")

            uploader.upload_file(
                local_path=path,
                key="metadata/uv-latest.json",
                cache_control="public, max-age=300",
            )

        self.assertEqual(sleeps, [10.0, 12, 12])


if __name__ == "__main__":
    unittest.main()
