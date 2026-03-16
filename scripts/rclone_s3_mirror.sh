#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash scripts/rclone_s3_mirror.sh copy-dir <local_dir> <remote_prefix>
  bash scripts/rclone_s3_mirror.sh sync-dir <local_dir> <remote_prefix>
  bash scripts/rclone_s3_mirror.sh copy-file <local_file> <remote_key>
  bash scripts/rclone_s3_mirror.sh purge-dir <remote_prefix>
EOF
}

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "$name is required" >&2
    exit 1
  fi
}

remote_path() {
  local relative="${1#/}"
  printf 'mirror:%s/%s' "$S3_BUCKET" "$relative"
}

rclone_with_flags() {
  local extra_flags=()
  if [ -n "${RCLONE_CACHE_CONTROL:-}" ]; then
    extra_flags+=(--metadata --metadata-set "cache-control=${RCLONE_CACHE_CONTROL}")
  fi
  rclone "$@" \
    --transfers "${RCLONE_TRANSFERS:-1}" \
    --checkers "${RCLONE_CHECKERS:-1}" \
    --s3-upload-concurrency "${RCLONE_S3_UPLOAD_CONCURRENCY:-1}" \
    --s3-chunk-size "${RCLONE_S3_CHUNK_SIZE:-128Mi}" \
    --s3-disable-checksum \
    --s3-no-check-bucket \
    --ignore-checksum \
    --retries "${RCLONE_RETRIES:-20}" \
    --low-level-retries "${RCLONE_LOW_LEVEL_RETRIES:-20}" \
    --retries-sleep "${RCLONE_RETRIES_SLEEP:-10s}" \
    --tpslimit "${RCLONE_TPSLIMIT:-0.5}" \
    --tpslimit-burst "${RCLONE_TPSLIMIT_BURST:-1}" \
    --stats "${RCLONE_STATS:-20s}" \
    "${extra_flags[@]}"
}

require_env S3_BUCKET
require_env RCLONE_CONFIG_MIRROR_TYPE
require_env RCLONE_CONFIG_MIRROR_PROVIDER
require_env RCLONE_CONFIG_MIRROR_ACCESS_KEY_ID
require_env RCLONE_CONFIG_MIRROR_SECRET_ACCESS_KEY
require_env RCLONE_CONFIG_MIRROR_ENDPOINT
require_env RCLONE_CONFIG_MIRROR_REGION

command="${1:-}"

case "$command" in
  copy-dir)
    [ "$#" -eq 3 ] || {
      usage
      exit 1
    }
    rclone_with_flags copy "$2" "$(remote_path "$3")"
    ;;
  sync-dir)
    [ "$#" -eq 3 ] || {
      usage
      exit 1
    }
    rclone_with_flags sync "$2" "$(remote_path "$3")"
    ;;
  copy-file)
    [ "$#" -eq 3 ] || {
      usage
      exit 1
    }
    rclone_with_flags copyto "$2" "$(remote_path "$3")"
    ;;
  purge-dir)
    [ "$#" -eq 2 ] || {
      usage
      exit 1
    }
    if ! rclone_with_flags lsf "$(remote_path "$2")" >/dev/null 2>&1; then
      echo "skip purge for missing prefix: $2" >&2
      exit 0
    fi
    rclone_with_flags purge "$(remote_path "$2")"
    ;;
  *)
    usage
    exit 1
    ;;
esac
