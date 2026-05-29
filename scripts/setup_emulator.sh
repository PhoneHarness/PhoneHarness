#!/usr/bin/env bash
set -euo pipefail

SERIAL="emulator-5554"
MANIFEST=""
START_GUI_PROXY=1

usage() {
  cat <<'EOF'
Usage: scripts/setup_emulator.sh [options]

Options:
  --serial SERIAL     adb serial (default: emulator-5554)
  --manifest PATH     optional APK manifest to install after wiring adb
  --no-gui-proxy      do not start host gui_proxy
  -h, --help          show this help

This script wires a running emulator for PhoneHarness:
  host:<8920+slot*10> -> device:8920
  device:8919 -> host:<8919+slot*10>
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial) SERIAL="$2"; shift 2 ;;
    --manifest) MANIFEST="$2"; shift 2 ;;
    --no-gui-proxy) START_GUI_PROXY=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

ADB=(adb -s "$SERIAL")
if ! "${ADB[@]}" get-state >/dev/null 2>&1; then
  echo "Device not available: $SERIAL" >&2
  exit 1
fi

EMU_PORT="${SERIAL##*-}"
SLOT=$(( (EMU_PORT - 5554) / 2 ))
HOST_GUI_PORT=$((8919 + SLOT * 10))
HOST_SERVER_PORT=$((8920 + SLOT * 10))

echo "Configuring $SERIAL (slot=$SLOT)"
"${ADB[@]}" forward "tcp:$HOST_SERVER_PORT" tcp:8920
"${ADB[@]}" reverse tcp:8919 "tcp:$HOST_GUI_PORT"
echo "  forward: host:$HOST_SERVER_PORT -> device:8920"
echo "  reverse: device:8919 -> host:$HOST_GUI_PORT"

echo "Disabling Android animations"
"${ADB[@]}" shell settings put global window_animation_scale 0
"${ADB[@]}" shell settings put global transition_animation_scale 0
"${ADB[@]}" shell settings put global animator_duration_scale 0

if [[ -n "$MANIFEST" ]]; then
  scripts/install_apps.sh --serial "$SERIAL" --manifest "$MANIFEST"
fi

if [[ "$START_GUI_PROXY" -eq 1 ]]; then
  if curl -s --connect-timeout 2 "http://127.0.0.1:$HOST_GUI_PORT/health" >/dev/null 2>&1; then
    echo "gui_proxy already running on :$HOST_GUI_PORT"
  else
    LOG="/tmp/phoneharness-gui-proxy-${HOST_GUI_PORT}.log"
    nohup python3 scripts/gui_proxy.py \
      --port "$HOST_GUI_PORT" \
      --serial "$SERIAL" \
      >"$LOG" 2>&1 &
    sleep 1
    if curl -s --connect-timeout 2 "http://127.0.0.1:$HOST_GUI_PORT/health" >/dev/null 2>&1; then
      echo "gui_proxy started on :$HOST_GUI_PORT (log: $LOG)"
    else
      echo "gui_proxy did not pass health check; inspect $LOG" >&2
      exit 1
    fi
  fi
fi

cat <<EOF

Next step: start PhoneHarness server inside Termux on device port 8920.
Example:
  python3 -m phoneharness server --port 8920 --model <model> --gui-model <gui-model> ...

Host health check after the device server starts:
  curl http://127.0.0.1:$HOST_SERVER_PORT/health
EOF
