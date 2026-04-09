#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

from uvmirror.downloads import download_python_assets
from uvmirror.installers import render_installers
from uvmirror.metadata import (
    build_python_asset_manifest,
    build_rewritten_python_metadata,
    keep_latest_runtime_builds,
)
from uvmirror.s3_upload import S3MirrorUploader
from uvmirror.uv_releases import prune_uv_tags


def build_python_downloads(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    public_base_url: str,
    manifest_output: pathlib.Path | None,
) -> None:
    raw_metadata = json.loads(input_path.read_text(encoding="utf-8"))
    selected_entries = keep_latest_runtime_builds(raw_metadata.values())
    selected_keys = {
        key for key, value in raw_metadata.items() if value in selected_entries
    }
    trimmed = {
        key: value for key, value in raw_metadata.items() if key in selected_keys
    }
    rewritten = build_rewritten_python_metadata(trimmed, public_base_url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(rewritten, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if manifest_output is not None:
        manifest_output.parent.mkdir(parents=True, exist_ok=True)
        manifest_output.write_text(
            json.dumps(build_python_asset_manifest(selected_entries), indent=2) + "\n",
            encoding="utf-8",
        )


def render(public_base_url: str, default_index_url: str, output_dir: pathlib.Path) -> None:
    rendered = render_installers(public_base_url, default_index_url)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "install-cn.sh").write_text(rendered.shell, encoding="utf-8")
    (output_dir / "install-cn.ps1").write_text(rendered.powershell, encoding="utf-8")


def write_uv_latest(output_path: pathlib.Path, public_base_url: str, tag: str) -> None:
    base = public_base_url.rstrip("/")
    payload = {
        "tag": tag,
        "installer_sh": f"{base}/github/astral-sh/uv/releases/download/latest/uv-installer.sh",
        "installer_ps1": f"{base}/github/astral-sh/uv/releases/download/latest/uv-installer.ps1",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def build_uploader_from_env() -> S3MirrorUploader:
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - exercised in workflow
        raise SystemExit("boto3 and botocore are required for upload commands") from exc

    access_key_id = _required_env("AWS_ACCESS_KEY_ID")
    secret_access_key = _required_env("AWS_SECRET_ACCESS_KEY")
    endpoint_url = _required_env("AWS_ENDPOINT_URL")
    region = os.environ.get("AWS_REGION") or _required_env("AWS_DEFAULT_REGION")
    bucket = _required_env("S3_BUCKET")

    session = boto3.session.Session(
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
        region_name=region,
    )
    client = session.client(
        "s3",
        endpoint_url=endpoint_url,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=float(os.environ.get("MIRROR_CONNECT_TIMEOUT_SECONDS", "10")),
            read_timeout=float(os.environ.get("MIRROR_READ_TIMEOUT_SECONDS", "300")),
            retries={"max_attempts": 0},
            tcp_keepalive=True,
        ),
    )
    return S3MirrorUploader(
        client=client,
        bucket=bucket,
        multipart_threshold=int(
            os.environ.get("MIRROR_MULTIPART_THRESHOLD_BYTES", str(256 * 1024 * 1024))
        ),
        part_size=int(os.environ.get("MIRROR_PART_SIZE_BYTES", str(128 * 1024 * 1024))),
        enable_multipart=_env_bool("MIRROR_ENABLE_MULTIPART", True),
        max_attempts=int(os.environ.get("MIRROR_MAX_ATTEMPTS", "24")),
        backoff_seconds=float(os.environ.get("MIRROR_BACKOFF_SECONDS", "5")),
        max_backoff_seconds=float(os.environ.get("MIRROR_MAX_BACKOFF_SECONDS", "60")),
        request_interval=float(os.environ.get("MIRROR_REQUEST_INTERVAL_SECONDS", "2")),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    python_downloads = subparsers.add_parser("build-python-downloads")
    python_downloads.add_argument("--input", type=pathlib.Path, required=True)
    python_downloads.add_argument("--output", type=pathlib.Path, required=True)
    python_downloads.add_argument("--public-base-url", required=True)
    python_downloads.add_argument("--manifest-output", type=pathlib.Path)

    download_python_assets_parser = subparsers.add_parser("download-python-assets")
    download_python_assets_parser.add_argument("--manifest", type=pathlib.Path, required=True)
    download_python_assets_parser.add_argument("--stage-dir", type=pathlib.Path, required=True)

    render_installers_parser = subparsers.add_parser("render-installers")
    render_installers_parser.add_argument("--public-base-url", required=True)
    render_installers_parser.add_argument("--default-index-url", required=True)
    render_installers_parser.add_argument("--output-dir", type=pathlib.Path, required=True)

    write_uv_latest_parser = subparsers.add_parser("write-uv-latest")
    write_uv_latest_parser.add_argument("--output", type=pathlib.Path, required=True)
    write_uv_latest_parser.add_argument("--public-base-url", required=True)
    write_uv_latest_parser.add_argument("--tag", required=True)

    plan_uv_prune_parser = subparsers.add_parser("plan-uv-prune")
    plan_uv_prune_parser.add_argument("--keep", type=int, required=True)
    plan_uv_prune_parser.add_argument("tags", nargs="+")

    upload_file_parser = subparsers.add_parser("upload-file")
    upload_file_parser.add_argument("--local-path", type=pathlib.Path, required=True)
    upload_file_parser.add_argument("--key", required=True)
    upload_file_parser.add_argument("--cache-control", required=True)

    upload_dir_parser = subparsers.add_parser("upload-dir")
    upload_dir_parser.add_argument("--local-dir", type=pathlib.Path, required=True)
    upload_dir_parser.add_argument("--prefix", required=True)
    upload_dir_parser.add_argument("--cache-control", required=True)

    sync_dir_parser = subparsers.add_parser("sync-dir-with-state")
    sync_dir_parser.add_argument("--local-dir", type=pathlib.Path, required=True)
    sync_dir_parser.add_argument("--prefix", required=True)
    sync_dir_parser.add_argument("--cache-control", required=True)
    sync_dir_parser.add_argument("--state-key", required=True)

    args = parser.parse_args()

    if args.command == "build-python-downloads":
        build_python_downloads(
            args.input,
            args.output,
            args.public_base_url,
            args.manifest_output,
        )
        return

    if args.command == "download-python-assets":
        download_python_assets(
            manifest_path=args.manifest,
            stage_dir=args.stage_dir,
            max_attempts=int(
                os.environ.get(
                    "MIRROR_DOWNLOAD_MAX_ATTEMPTS",
                    os.environ.get("MIRROR_MAX_ATTEMPTS", "24"),
                )
            ),
            backoff_seconds=float(
                os.environ.get(
                    "MIRROR_DOWNLOAD_BACKOFF_SECONDS",
                    os.environ.get("MIRROR_BACKOFF_SECONDS", "5"),
                )
            ),
            request_interval=float(
                os.environ.get(
                    "MIRROR_DOWNLOAD_REQUEST_INTERVAL_SECONDS",
                    os.environ.get("MIRROR_REQUEST_INTERVAL_SECONDS", "2"),
                )
            ),
            max_backoff_seconds=float(
                os.environ.get("MIRROR_DOWNLOAD_MAX_BACKOFF_SECONDS", "60")
            ),
            logger=lambda message: print(message, file=sys.stderr),
        )
        return

    if args.command == "render-installers":
        render(args.public_base_url, args.default_index_url, args.output_dir)
        return

    if args.command == "write-uv-latest":
        write_uv_latest(args.output, args.public_base_url, args.tag)
        return

    if args.command == "plan-uv-prune":
        for tag in prune_uv_tags(args.tags, args.keep):
            print(tag)
        return

    if args.command == "upload-file":
        build_uploader_from_env().upload_file(
            local_path=args.local_path,
            key=args.key,
            cache_control=args.cache_control,
        )
        return

    if args.command == "upload-dir":
        build_uploader_from_env().upload_directory(
            local_dir=args.local_dir,
            remote_prefix=args.prefix,
            cache_control=args.cache_control,
        )
        return

    if args.command == "sync-dir-with-state":
        build_uploader_from_env().sync_directory_with_state(
            local_dir=args.local_dir,
            remote_prefix=args.prefix,
            cache_control=args.cache_control,
            state_key=args.state_key,
        )
        return


if __name__ == "__main__":
    main()
