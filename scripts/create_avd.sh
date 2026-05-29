#!/usr/bin/env bash
set -euo pipefail

SDK_ROOT="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
AVD_NAME="AndroidWorldAvd"
AVD_DEVICE="pixel_6"
AVD_SYSIMG="system-images;android-33;google_apis_playstore;arm64-v8a"
AVD_PLATFORM="platforms;android-33"
DATA_SIZE="32G"
RAM_MB="2048"
SERIAL="emulator-5554"
INSTALL_SDK=0
START_EMULATOR=0

usage() {
  cat <<'EOF'
Usage: scripts/create_avd.sh [options]

Options:
  --name NAME             AVD name (default: AndroidWorldAvd)
  --device DEVICE         avdmanager device profile (default: pixel_6)
  --system-image IMAGE    SDK system image package
  --data-size SIZE        data partition size (default: 32G)
  --ram-mb MB             emulator RAM in MB (default: 2048)
  --serial SERIAL         emulator serial used for startup port (default: emulator-5554)
  --sdk-root PATH         Android SDK root (default: ANDROID_HOME or ~/Library/Android/sdk)
  --install-sdk           install platform and system image before creating AVD
  --start                 start the emulator after creating/updating it
  -h, --help              show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) AVD_NAME="$2"; shift 2 ;;
    --device) AVD_DEVICE="$2"; shift 2 ;;
    --system-image) AVD_SYSIMG="$2"; shift 2 ;;
    --data-size) DATA_SIZE="$2"; shift 2 ;;
    --ram-mb) RAM_MB="$2"; shift 2 ;;
    --serial) SERIAL="$2"; shift 2 ;;
    --sdk-root) SDK_ROOT="$2"; shift 2 ;;
    --install-sdk) INSTALL_SDK=1; shift ;;
    --start) START_EMULATOR=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

SDKMANAGER="$SDK_ROOT/cmdline-tools/latest/bin/sdkmanager"
AVDMANAGER="$SDK_ROOT/cmdline-tools/latest/bin/avdmanager"
EMULATOR="$SDK_ROOT/emulator/emulator"

for bin in "$AVDMANAGER" "$EMULATOR"; do
  if [[ ! -x "$bin" ]]; then
    echo "Missing executable: $bin" >&2
    exit 1
  fi
done

if [[ "$INSTALL_SDK" -eq 1 ]]; then
  if [[ ! -x "$SDKMANAGER" ]]; then
    echo "Missing executable: $SDKMANAGER" >&2
    exit 1
  fi
  yes | "$SDKMANAGER" "$AVD_PLATFORM" "$AVD_SYSIMG"
fi

if "$EMULATOR" -list-avds 2>/dev/null | grep -qx "$AVD_NAME"; then
  echo "AVD exists: $AVD_NAME"
else
  echo "Creating AVD: $AVD_NAME"
  echo "no" | "$AVDMANAGER" create avd \
    -n "$AVD_NAME" \
    -k "$AVD_SYSIMG" \
    -d "$AVD_DEVICE" \
    --force
fi

CONFIG_INI="$HOME/.android/avd/${AVD_NAME}.avd/config.ini"
if [[ ! -f "$CONFIG_INI" ]]; then
  echo "AVD config not found: $CONFIG_INI" >&2
  exit 1
fi

set_config() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "$CONFIG_INI"; then
    sed -i.bak "s#^${key}=.*#${key}=${value}#" "$CONFIG_INI"
  else
    printf '%s=%s\n' "$key" "$value" >> "$CONFIG_INI"
  fi
}

set_config "disk.dataPartition.size" "$DATA_SIZE"
set_config "hw.ramSize" "$RAM_MB"
set_config "hw.lcd.width" "1080"
set_config "hw.lcd.height" "2400"

rm -f "${CONFIG_INI}.bak"
echo "Updated $CONFIG_INI"
echo "  disk.dataPartition.size=$DATA_SIZE"
echo "  hw.ramSize=$RAM_MB"
echo "  hw.lcd.width=1080"
echo "  hw.lcd.height=2400"

if [[ "$START_EMULATOR" -eq 1 ]]; then
  EMU_PORT="${SERIAL##*-}"
  echo "Starting $AVD_NAME on $SERIAL"
  nohup "$EMULATOR" -avd "$AVD_NAME" \
    -no-snapshot \
    -port "$EMU_PORT" \
    -no-audio \
    -no-boot-anim \
    >/tmp/phoneharness-${AVD_NAME}.log 2>&1 &
  echo "Log: /tmp/phoneharness-${AVD_NAME}.log"
fi
