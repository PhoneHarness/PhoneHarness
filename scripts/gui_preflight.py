#!/usr/bin/env python3
"""Strict GUI slot preflight for PhoneHarness benchmark runs."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "benchmark" / "run_hybrid_bench.py"


def _load_runner():
    if not RUNNER.exists():
        raise RuntimeError(
            f"{RUNNER} is not part of the public runtime repository. "
            "Install or download the benchmark release package before running "
            "gui_preflight, or use scripts/run_gui_direct_smoke.py for a "
            "repository-local GUI connectivity check."
        )
    spec = importlib.util.spec_from_file_location("run_hybrid_bench", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_slots(runner, args):
    if args.slot_specs:
        slots = []
        for spec in args.slot_specs.split(","):
            parts = [p.strip() for p in spec.split(":")]
            if len(parts) != 4:
                raise ValueError(f"Invalid --slot-specs entry: {spec}")
            sid, serial, server_port, gui_port = parts
            slots.append({
                "id": int(sid),
                "serial": serial,
                "server": f"http://localhost:{int(server_port)}",
                "gui_port": int(gui_port),
            })
        return slots
    if args.slot_ids:
        return [runner.make_slot(int(s.strip())) for s in args.slot_ids.split(",") if s.strip()]
    return [runner.make_slot(i) for i in range(args.start_slot, args.start_slot + args.slots)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check phoneharness + gui_proxy + screenshot for slots")
    parser.add_argument("--slots", type=int, default=1)
    parser.add_argument("--start-slot", type=int, default=0)
    parser.add_argument("--slot-ids", help="Comma-separated slot ids, e.g. 2,3,4")
    parser.add_argument("--slot-specs", help="Comma-separated id:serial:server_port:gui_port specs")
    args = parser.parse_args()

    runner = _load_runner()
    ok_count = 0
    for slot in _build_slots(runner, args):
        ok = runner.check_slot_health(slot)
        status = "OK" if ok else "FAIL"
        model = slot.get("server_model", "?")
        gui_model = slot.get("server_gui_model", "?")
        reason = slot.get("health_error", "")
        print(
            f"{status} slot={slot['id']} serial={slot['serial']} "
            f"server={slot['server']} gui_port={slot.get('gui_port')} "
            f"model={model} gui_model={gui_model}"
            + (f" reason={reason}" if reason else "")
        )
        ok_count += int(ok)

    return 0 if ok_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
