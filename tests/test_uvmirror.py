import hashlib
import json
import pathlib
import tempfile
import unittest
import urllib.error

from uvmirror.downloads import download_python_assets
from uvmirror.installers import render_installers
from uvmirror.metadata import (
    build_state_manifest,
    diff_stale_keys,
    keep_latest_runtime_builds,
    mirror_path_for_python_download_url,
    rewrite_python_download_url,
    state_manifest_file_sizes,
)
from uvmirror.s3_upload import S3MirrorUploader
from uvmirror.uv_releases import prune_uv_tags


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
        self.assertIn("UV_PYTHON_DOWNLOADS_JSON_URL", rendered.shell)
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
            'python-downloads-json-url = "%s/metadata/python-downloads.json"\\n',
            rendered.shell,
        )
        self.assertIn(
            'python-downloads-json-url\\ =*|pypy-install-mirror\\ =*)',
            rendered.shell,
        )
        self.assertNotIn(
            'curl -LsSf "$PUBLIC_BASE_URL/github/astral-sh/uv/releases/download/latest/uv-installer.sh" | env UV_INSTALLER_GITHUB_BASE_URL="$PUBLIC_BASE_URL/github" sh',
            rendered.shell,
        )
        self.assertNotIn("printf '%s\n' \"$line\"", rendered.shell)
        self.assertNotIn("UV_PYPY_INSTALL_MIRROR", rendered.shell)
        self.assertNotIn('pypy-install-mirror = "%s/pypy"\\n', rendered.shell)
        self.assertIn("https://uv.example.com/github", rendered.powershell)
        self.assertIn("https://pypi.tuna.tsinghua.edu.cn/simple", rendered.powershell)
        self.assertIn(
            '$env:UV_INSTALLER_GITHUB_BASE_URL = "$PublicBaseUrl/github"',
            rendered.powershell,
        )
        self.assertIn(
            '$env:UV_PYTHON_DOWNLOADS_JSON_URL = "$PublicBaseUrl/metadata/python-downloads.json"',
            rendered.powershell,
        )
        self.assertNotIn("UV_PYTHON_INSTALL_MIRROR", rendered.shell)
        self.assertNotIn('python-install-mirror = "', rendered.shell)
        self.assertNotIn("UV_PYTHON_INSTALL_MIRROR", rendered.powershell)
        self.assertIn(
            'if ($Line.Trim().StartsWith("pypy-install-mirror =")) { continue }',
            rendered.powershell,
        )
        self.assertNotIn("UV_PYPY_INSTALL_MIRROR", rendered.powershell)
        self.assertNotIn('pypy-install-mirror = "', rendered.powershell)
        self.assertLess(
            rendered.powershell.index(
                '$env:UV_INSTALLER_GITHUB_BASE_URL = "$PublicBaseUrl/github"'
            ),
            rendered.powershell.index(
                'irm "$PublicBaseUrl/github/astral-sh/uv/releases/download/latest/uv-installer.ps1" | iex'
            ),
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
        self.assertEqual(client.calls[0][1]["ContentType"], "application/x-sh")

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
