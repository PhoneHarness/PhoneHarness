#!/bin/bash
# Virtual Display manager for background agent operations.
#
# Usage:
#   ./scripts/vdisplay.sh start [serial]       # Install VDHelper, create background display
#   ./scripts/vdisplay.sh screenshot [serial]   # Capture background display to local file
#   ./scripts/vdisplay.sh launch <pkg/act> [serial]  # Launch app on background display
#   ./scripts/vdisplay.sh tap <x> <y> [serial]       # Tap on background display
#   ./scripts/vdisplay.sh swipe <x1> <y1> <x2> <y2> [serial]
#   ./scripts/vdisplay.sh type <text> [serial]        # Type text (CJK via ADBKeyboard)
#   ./scripts/vdisplay.sh uidump [serial]             # Dump UI tree of background display
#   ./scripts/vdisplay.sh status [serial]      # Show current display status
#   ./scripts/vdisplay.sh stop [serial]        # Stop VDHelper, release display

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VDHELPER_DIR="$PROJECT_DIR/vdisplay-helper"
APK="$VDHELPER_DIR/build/vdhelper.apk"
PKG="com.phoneharness.vdhelper"
ID_FILE="/sdcard/vdhelper_display_id.txt"
SCREENSHOT_DEVICE="/sdcard/vdhelper_screenshot.png"
SCREENSHOT_LOCAL="/tmp/vdisplay_screenshot.png"

# ── Helpers ──────────────────────────────────────────

resolve_serial() {
    local serial="${1:-}"
    if [ -n "$serial" ]; then
        echo "$serial"
        return
    fi
    # Auto-detect single device
    local devices
    devices=$(adb devices | grep -E "device$" | awk '{print $1}')
    local count
    count=$(echo "$devices" | grep -c . || true)
    if [ "$count" -eq 0 ]; then
        echo "ERROR: no device found" >&2; exit 1
    elif [ "$count" -gt 1 ]; then
        echo "ERROR: multiple devices found, specify serial: $devices" >&2; exit 1
    fi
    echo "$devices"
}

adb_s() {
    adb -s "$SERIAL" "$@"
}

get_display_id() {
    local did
    did=$(adb_s shell cat "$ID_FILE" 2>/dev/null | tr -d '\r\n')
    if [ -z "$did" ] || [ "$did" = "" ]; then
        echo ""
    else
        echo "$did"
    fi
}

require_display() {
    local did
    did=$(get_display_id)
    if [ -z "$did" ]; then
        echo "ERROR: no background display running. Run '$0 start' first." >&2
        exit 1
    fi
    echo "$did"
}

# ── Commands ─────────────────────────────────────────

cmd_start() {
    echo "=== VirtualDisplay: start ==="

    # Build APK if needed
    if [ ! -f "$APK" ]; then
        echo "Building VDHelper APK..."
        (cd "$VDHELPER_DIR" && bash build.sh)
    fi

    # Install
    echo "Installing VDHelper..."
    adb_s install -r "$APK" 2>&1 | tail -1

    # Permissions
    adb_s shell pm grant "$PKG" android.permission.WRITE_EXTERNAL_STORAGE 2>/dev/null || true
    adb_s shell pm grant "$PKG" android.permission.READ_EXTERNAL_STORAGE 2>/dev/null || true

    # Enable multi-display
    adb_s shell settings put global force_resizable_activities 1

    # Clean old state
    adb_s shell am force-stop "$PKG" 2>/dev/null || true
    adb_s shell rm -f "$ID_FILE" 2>/dev/null || true
    sleep 1

    # Launch
    adb_s shell am start -n "$PKG/.PermActivity" 2>&1 | head -1
    sleep 3

    # Verify
    local did
    did=$(get_display_id)
    if [ -z "$did" ]; then
        echo "ERROR: VirtualDisplay not created. Check: adb logcat -s VDHelper" >&2
        adb_s logcat -d -s VDHelper | tail -5 >&2
        exit 1
    fi

    echo ""
    echo "Background display created: Display $did"
    echo "Main screen (Display 0) is unaffected."
    echo ""
    echo "Usage:"
    echo "  $0 launch com.phoneuse.mdouban/.app.MainActivity"
    echo "  $0 screenshot"
    echo "  $0 tap 540 960"
    echo "  $0 type '霸王别姬'"
    echo "  $0 stop"
}

cmd_stop() {
    echo "=== VirtualDisplay: stop ==="
    adb_s shell am force-stop "$PKG"
    adb_s shell rm -f "$ID_FILE" "$SCREENSHOT_DEVICE" 2>/dev/null || true
    echo "Background display released."
}

cmd_status() {
    local did
    did=$(get_display_id)
    if [ -z "$did" ]; then
        echo "No background display running."
    else
        echo "Background display: Display $did"
        echo ""
        echo "Activities on Display $did:"
        adb_s shell dumpsys activity activities | grep -A10 "Display #$did" | head -12
    fi
}

cmd_screenshot() {
    local did
    did=$(require_display)
    local out="${1:-$SCREENSHOT_LOCAL}"

    adb_s shell am broadcast -a "$PKG.SCREENSHOT" > /dev/null 2>&1
    sleep 1
    adb_s pull "$SCREENSHOT_DEVICE" "$out" 2>&1 | tail -1

    local size
    size=$(wc -c < "$out" | tr -d ' ')
    if [ "$size" -lt 100 ]; then
        echo "WARNING: screenshot is $size bytes (possibly empty)" >&2
    else
        echo "Screenshot saved: $out ($size bytes)"
    fi
}

cmd_launch() {
    local component="$1"
    local did
    did=$(require_display)

    echo "Launching $component on Display $did..."
    # Extract package name for force-stop
    local pkg_name="${component%%/*}"
    adb_s shell am force-stop "$pkg_name" 2>/dev/null || true
    sleep 1
    adb_s shell am start -f 0x10000000 --display "$did" -n "$component" 2>&1 | head -1
    sleep 2
    echo "Done. Use '$0 screenshot' to see the result."
}

cmd_tap() {
    local x="$1" y="$2"
    local did
    did=$(require_display)
    adb_s shell input -d "$did" tap "$x" "$y"
}

cmd_swipe() {
    local x1="$1" y1="$2" x2="$3" y2="$4"
    local did
    did=$(require_display)
    adb_s shell input -d "$did" swipe "$x1" "$y1" "$x2" "$y2"
}

cmd_type() {
    local text="$1"
    # ADBKeyboard broadcast is global (not display-specific)
    adb_s shell am broadcast -a ADB_INPUT_TEXT --es msg "$text" > /dev/null 2>&1
}

cmd_uidump() {
    local did
    did=$(require_display)
    local out="${1:-/tmp/vdisplay_ui.xml}"

    adb_s shell uiautomator dump --display "$did" /sdcard/vdisplay_ui.xml > /dev/null 2>&1
    adb_s pull /sdcard/vdisplay_ui.xml "$out" 2>&1 | tail -1
    echo "UI dump saved: $out"
}

# ── Main ─────────────────────────────────────────────

CMD="${1:-help}"
shift || true

case "$CMD" in
    start)
        SERIAL=$(resolve_serial "${1:-}")
        cmd_start
        ;;
    stop)
        SERIAL=$(resolve_serial "${1:-}")
        cmd_stop
        ;;
    status)
        SERIAL=$(resolve_serial "${1:-}")
        cmd_status
        ;;
    screenshot)
        SERIAL=$(resolve_serial "${2:-}")
        cmd_screenshot "${1:-$SCREENSHOT_LOCAL}"
        ;;
    launch)
        [ -z "${1:-}" ] && echo "Usage: $0 launch <pkg/activity> [serial]" && exit 1
        SERIAL=$(resolve_serial "${2:-}")
        cmd_launch "$1"
        ;;
    tap)
        [ -z "${1:-}" ] || [ -z "${2:-}" ] && echo "Usage: $0 tap <x> <y> [serial]" && exit 1
        SERIAL=$(resolve_serial "${3:-}")
        cmd_tap "$1" "$2"
        ;;
    swipe)
        [ -z "${1:-}" ] || [ -z "${4:-}" ] && echo "Usage: $0 swipe <x1> <y1> <x2> <y2> [serial]" && exit 1
        SERIAL=$(resolve_serial "${5:-}")
        cmd_swipe "$1" "$2" "$3" "$4"
        ;;
    type)
        [ -z "${1:-}" ] && echo "Usage: $0 type <text> [serial]" && exit 1
        SERIAL=$(resolve_serial "${2:-}")
        cmd_type "$1"
        ;;
    uidump)
        SERIAL=$(resolve_serial "${2:-}")
        cmd_uidump "${1:-/tmp/vdisplay_ui.xml}"
        ;;
    help|--help|-h)
        echo "Virtual Display manager for background agent operations."
        echo ""
        echo "Usage:"
        echo "  $0 start [serial]                          Create background display"
        echo "  $0 screenshot [output_path] [serial]       Capture background display"
        echo "  $0 launch <pkg/activity> [serial]          Launch app on background display"
        echo "  $0 tap <x> <y> [serial]                    Tap on background display"
        echo "  $0 swipe <x1> <y1> <x2> <y2> [serial]     Swipe on background display"
        echo "  $0 type <text> [serial]                    Type text (CJK supported)"
        echo "  $0 uidump [output_path] [serial]           Dump UI tree"
        echo "  $0 status [serial]                         Show display status"
        echo "  $0 stop [serial]                           Stop and release display"
        ;;
    *)
        echo "Unknown command: $CMD. Run '$0 help' for usage." >&2
        exit 1
        ;;
esac
