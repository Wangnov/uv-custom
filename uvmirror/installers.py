from __future__ import annotations

from dataclasses import dataclass


SHELL_TEMPLATE = """#!/bin/sh
set -eu

PUBLIC_BASE_URL="__PUBLIC_BASE_URL__"

append_managed_block() {
  target_file="$1"
  managed_block=$(cat <<'EOF'
# >>> uv mirror managed block >>>
export UV_INSTALLER_GITHUB_BASE_URL="__PUBLIC_BASE_URL__/github"
export UV_PYTHON_DOWNLOADS_JSON_URL="__PUBLIC_BASE_URL__/metadata/python-downloads.json"
export UV_DEFAULT_INDEX="__DEFAULT_INDEX_URL__"
# <<< uv mirror managed block <<<
EOF
)

  mkdir -p "$(dirname "$target_file")"
  touch "$target_file"

  if grep -qF "# >>> uv mirror managed block >>>" "$target_file"; then
    awk '
      BEGIN {skip=0}
      /^# >>> uv mirror managed block >>>/ {skip=1; next}
      /^# <<< uv mirror managed block <<</ {skip=0; next}
      !skip {print}
    ' "$target_file" > "$target_file.tmp"
    mv "$target_file.tmp" "$target_file"
  fi

  printf '\\n%s\\n' "$managed_block" >> "$target_file"
}

write_uv_config() {
  config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/uv"
  config_file="$config_dir/uv.toml"
  timestamp=$(date +%Y%m%d%H%M%S)
  tmp_file=$(mktemp)
  mkdir -p "$config_dir"

  if [ -f "$config_file" ]; then
    cp "$config_file" "$config_file.$timestamp.bak"
    while IFS= read -r line || [ -n "$line" ]; do
      trimmed=$(printf '%s' "$line" | sed 's/^[[:space:]]*//')
      case "$trimmed" in
        python-downloads-json-url\\ =*|pypy-install-mirror\\ =*)
          continue
          ;;
      esac
      printf '%s\\n' "$line" >> "$tmp_file"
    done < "$config_file"
  fi

  if [ -s "$tmp_file" ]; then
    printf '\\n' >> "$tmp_file"
  fi

  printf 'python-downloads-json-url = "%s/metadata/python-downloads.json"\\n' "$PUBLIC_BASE_URL" >> "$tmp_file"
  mv "$tmp_file" "$config_file"
}

install_uv() {
  installer_file=$(mktemp)
  trap 'rm -f "$installer_file"' EXIT HUP INT TERM
  curl -LsSf "$PUBLIC_BASE_URL/github/astral-sh/uv/releases/download/latest/uv-installer.sh" -o "$installer_file"
  env UV_INSTALLER_GITHUB_BASE_URL="$PUBLIC_BASE_URL/github" sh "$installer_file"
  rm -f "$installer_file"
  trap - EXIT HUP INT TERM
}

install_uv
write_uv_config

if [ -n "${SHELL:-}" ]; then
  case "${SHELL##*/}" in
    zsh) append_managed_block "$HOME/.zshrc" ;;
    bash) append_managed_block "$HOME/.bashrc" ;;
  esac
fi

append_managed_block "$HOME/.profile"
"""


POWERSHELL_TEMPLATE = r"""$ErrorActionPreference = "Stop"

$PublicBaseUrl = "__PUBLIC_BASE_URL__"

function Set-ManagedBlock {
    param([string]$Path)

    $ManagedBlock = @'
# >>> uv mirror managed block >>>
$env:UV_INSTALLER_GITHUB_BASE_URL = "__PUBLIC_BASE_URL__/github"
$env:UV_PYTHON_DOWNLOADS_JSON_URL = "__PUBLIC_BASE_URL__/metadata/python-downloads.json"
$env:UV_DEFAULT_INDEX = "__DEFAULT_INDEX_URL__"
# <<< uv mirror managed block <<<
'@

    $Directory = Split-Path -Parent $Path
    if ($Directory -and -not (Test-Path $Directory)) {
        New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    }

    if (-not (Test-Path $Path)) {
        New-Item -ItemType File -Path $Path -Force | Out-Null
    }

    $RawContent = Get-Content -Path $Path -Raw
    $Content = if ($null -eq $RawContent) { "" } else { $RawContent }
    if ($Content -match '(?s)# >>> uv mirror managed block >>>.*?# <<< uv mirror managed block <<<') {
        $Content = [regex]::Replace($Content, '(?s)# >>> uv mirror managed block >>>.*?# <<< uv mirror managed block <<<\r?\n?', '')
    }

    $Updated = $Content.TrimEnd()
    if ($Updated.Length -gt 0) {
        $Updated += "`r`n`r`n"
    }
    $Updated += $ManagedBlock.TrimEnd() + "`r`n"
    [System.IO.File]::WriteAllText($Path, $Updated, [System.Text.UTF8Encoding]::new($false))
}

function Set-UvConfig {
    $ConfigDir = Join-Path $env:APPDATA "uv"
    $ConfigFile = Join-Path $ConfigDir "uv.toml"
    if (-not (Test-Path $ConfigDir)) {
        New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
    }

    if (Test-Path $ConfigFile) {
        $Timestamp = Get-Date -Format "yyyyMMddHHmmss"
        Copy-Item $ConfigFile "$ConfigFile.$Timestamp.bak"
    }

    $Existing = ""
    if (Test-Path $ConfigFile) {
        $Existing = Get-Content -Path $ConfigFile -Raw
    }

    $Lines = @()
    foreach ($Line in $Existing -split "\r?\n") {
        if ($Line.Trim().StartsWith("python-downloads-json-url =")) { continue }
        if ($Line.Trim().StartsWith("pypy-install-mirror =")) { continue }
        $Lines += $Line
    }

    while ($Lines.Count -gt 0 -and [string]::IsNullOrWhiteSpace($Lines[-1])) {
        if ($Lines.Count -eq 1) {
            $Lines = @()
            break
        }
        $Lines = $Lines[0..($Lines.Count - 2)]
    }

    if ($Lines.Count -gt 0) {
        $Lines += ""
    }

    $Lines += 'python-downloads-json-url = "' + $PublicBaseUrl + '/metadata/python-downloads.json"'
    [System.IO.File]::WriteAllText($ConfigFile, ($Lines -join "`r`n") + "`r`n", [System.Text.UTF8Encoding]::new($false))
}

$env:UV_INSTALLER_GITHUB_BASE_URL = "$PublicBaseUrl/github"
$env:UV_PYTHON_DOWNLOADS_JSON_URL = "$PublicBaseUrl/metadata/python-downloads.json"
$env:UV_DEFAULT_INDEX = "__DEFAULT_INDEX_URL__"
irm "$PublicBaseUrl/github/astral-sh/uv/releases/download/latest/uv-installer.ps1" | iex
Set-UvConfig
Set-ManagedBlock -Path $PROFILE
"""


@dataclass(frozen=True)
class RenderedInstallers:
    shell: str
    powershell: str


def render_installers(public_base_url: str, default_index_url: str) -> RenderedInstallers:
    base = public_base_url.rstrip("/")
    shell = SHELL_TEMPLATE.replace("__PUBLIC_BASE_URL__", base).replace(
        "__DEFAULT_INDEX_URL__", default_index_url
    )
    powershell = POWERSHELL_TEMPLATE.replace("__PUBLIC_BASE_URL__", base).replace(
        "__DEFAULT_INDEX_URL__", default_index_url
    )
    return RenderedInstallers(shell=shell, powershell=powershell)
