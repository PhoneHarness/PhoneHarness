#!/usr/bin/env bash
set -euo pipefail

SERIAL="emulator-5554"
OUT=""

usage() {
  cat <<'EOF'
Usage: scripts/collect_emulator_info.sh [options]

Options:
  --serial SERIAL   adb serial (default: emulator-5554)
  --out PATH        write Markdown report to PATH instead of stdout
  -h, --help        show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial) SERIAL="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

ADB=(adb -s "$SERIAL")
if ! "${ADB[@]}" get-state >/dev/null 2>&1; then
  echo "Device not available: $SERIAL" >&2
  exit 1
fi

run_adb() {
  "${ADB[@]}" "$@" 2>/dev/null || true
}

shell_cmd() {
  "${ADB[@]}" shell "$1" 2>/dev/null | tr -d '\r' || true
}

emit_report() {
  cat <<EOF
# PhoneHarness Emulator Info

- Captured at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
- Serial: $SERIAL
- ADB state: $(run_adb get-state)

## Build

| Property | Value |
| --- | --- |
| ro.product.model | $(shell_cmd 'getprop ro.product.model') |
| ro.product.manufacturer | $(shell_cmd 'getprop ro.product.manufacturer') |
| ro.build.version.release | $(shell_cmd 'getprop ro.build.version.release') |
| ro.build.version.sdk | $(shell_cmd 'getprop ro.build.version.sdk') |
| ro.build.fingerprint | $(shell_cmd 'getprop ro.build.fingerprint') |

## Display

\`\`\`text
$(shell_cmd 'wm size; wm density')
\`\`\`

## Storage

\`\`\`text
$(shell_cmd 'df -h /data /storage/emulated 2>/dev/null || df -h /data')
\`\`\`

## Input Method

\`\`\`text
$(shell_cmd 'settings get secure default_input_method')
\`\`\`

## Animation Settings

\`\`\`text
$(shell_cmd 'settings get global window_animation_scale; settings get global transition_animation_scale; settings get global animator_duration_scale')
\`\`\`

## Third-Party Packages

\`\`\`text
$(shell_cmd 'pm list packages -3 | sort')
\`\`\`

## All Packages

\`\`\`text
$(shell_cmd 'pm list packages | sort')
\`\`\`
EOF
}

if [[ -n "$OUT" ]]; then
  mkdir -p "$(dirname "$OUT")"
  emit_report > "$OUT"
  echo "Wrote $OUT"
else
  emit_report
fi
