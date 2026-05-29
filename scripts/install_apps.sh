#!/usr/bin/env bash
set -euo pipefail

SERIAL="emulator-5554"
MANIFEST="config/apk-manifest.example.tsv"
DOWNLOAD_DIR=".apk-cache"

usage() {
  cat <<'EOF'
Usage: scripts/install_apps.sh [options]

Options:
  --serial SERIAL       adb serial (default: emulator-5554)
  --manifest PATH       tab-separated APK manifest
  --download-dir PATH   cache directory for downloaded APKs (default: .apk-cache)
  -h, --help            show this help

Manifest format:
  name<TAB>package<TAB>source<TAB>sha256

source may be a local path, file:// URL, or https:// URL. Empty source means
the package is documented but not installed by this script.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial) SERIAL="$2"; shift 2 ;;
    --manifest) MANIFEST="$2"; shift 2 ;;
    --download-dir) DOWNLOAD_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! -f "$MANIFEST" ]]; then
  echo "Manifest not found: $MANIFEST" >&2
  exit 1
fi

ADB=(adb -s "$SERIAL")
if ! "${ADB[@]}" get-state >/dev/null 2>&1; then
  echo "Device not available: $SERIAL" >&2
  exit 1
fi

mkdir -p "$DOWNLOAD_DIR"

is_installed() {
  local package="$1"
  [[ -n "$package" ]] && "${ADB[@]}" shell pm path "$package" >/dev/null 2>&1
}

resolve_source() {
  local name="$1"
  local source="$2"
  case "$source" in
    https://*)
      local apk="$DOWNLOAD_DIR/${name}.apk"
      if [[ ! -f "$apk" ]]; then
        curl -L --fail --show-error --output "$apk" "$source"
      fi
      printf '%s\n' "$apk"
      ;;
    file://*)
      printf '%s\n' "${source#file://}"
      ;;
    *)
      printf '%s\n' "$source"
      ;;
  esac
}

check_sha256() {
  local apk="$1"
  local expected="$2"
  if [[ -z "$expected" || "$expected" == "-" ]]; then
    return 0
  fi
  local actual
  actual="$(shasum -a 256 "$apk" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    echo "SHA256 mismatch for $apk" >&2
    echo "  expected: $expected" >&2
    echo "  actual:   $actual" >&2
    exit 1
  fi
}

while IFS=$'\t' read -r name package source sha256 _; do
  [[ -z "${name:-}" || "${name:0:1}" == "#" ]] && continue
  source="${source:-}"
  sha256="${sha256:-}"

  if is_installed "$package"; then
    echo "installed: $name ($package)"
    continue
  fi

  if [[ -z "$source" || "$source" == "-" ]]; then
    echo "missing source: $name ($package)"
    continue
  fi

  apk="$(resolve_source "$name" "$source")"
  if [[ ! -f "$apk" ]]; then
    echo "APK not found for $name: $apk" >&2
    continue
  fi

  check_sha256 "$apk" "$sha256"
  echo "installing: $name from $apk"
  "${ADB[@]}" install -r "$apk"
done < "$MANIFEST"
