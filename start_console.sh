#!/data/data/com.termux/files/usr/bin/bash
# PhoneHarness Agent Console launcher for Termux
# Usage: ~/phoneharness/start_console.sh [--model doubao-2.0-pro]

cd "$(dirname "$0")"
exec python3 -m phoneharness console "$@"
