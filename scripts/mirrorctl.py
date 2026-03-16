#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib

from uvmirror.installers import render_installers
from uvmirror.metadata import (
    build_python_asset_manifest,
    build_rewritten_python_metadata,
    keep_latest_runtime_builds,
)
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


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    python_downloads = subparsers.add_parser("build-python-downloads")
    python_downloads.add_argument("--input", type=pathlib.Path, required=True)
    python_downloads.add_argument("--output", type=pathlib.Path, required=True)
    python_downloads.add_argument("--public-base-url", required=True)
    python_downloads.add_argument("--manifest-output", type=pathlib.Path)

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

    args = parser.parse_args()

    if args.command == "build-python-downloads":
        build_python_downloads(
            args.input,
            args.output,
            args.public_base_url,
            args.manifest_output,
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


if __name__ == "__main__":
    main()
