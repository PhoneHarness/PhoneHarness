# PhoneHarness Benchmark Release Subset

This directory contains the public, repository-local benchmark assets used to
exercise the PhoneHarness runtime without depending on private traces or emulator
snapshots.

## What Is Included

| Path | Purpose |
| --- | --- |
| `run_hybrid_bench.py` | Multi-slot runner for verifier-backed hybrid task sheets. |
| `run_benchmark.py` | Small 10-task runner for the unified prompt smoke suite. |
| `grader.py` | Grader for the 10-task smoke suite. |
| `tasks.yaml` / `rubrics.yaml` | 10-task smoke benchmark definitions and rubrics. |
| `tasks/hybrid_bench_base_verifiers_by_sheet/*.yaml` | Public verifier-backed task sheets: `4app_new`, `30app_new`, `68app_new`, and `safety_bench`. |
| `fixtures/` | Small local sample files for OCR/PDF/image smoke tasks. |
| `examples/` | Example trace/meta files for viewer and grader development. |
| `configs/mcp_bench/` | Public CLI/skill tool schemas consumed by `phoneharness.tools.mcp_bench`. |
| `scripts/mcp_proxy.py` | Optional host-side local shell proxy for tools unavailable inside Android/Termux. |

Generated traces, local model outputs, third-party APKs, login state, emulator
snapshots, and private host-service deployments are intentionally not included.

## Quick Checks

```bash
python3 -m py_compile benchmark/run_hybrid_bench.py benchmark/grader.py scripts/mcp_proxy.py
python3 benchmark/run_hybrid_bench.py --sheet 4app_new --list
python3 benchmark/run_hybrid_bench.py --sheet safety_bench --list
```

## Running a Hybrid Task Sheet

Start PhoneHarness servers on one or more emulator slots, then run:

```bash
python3 benchmark/run_hybrid_bench.py --sheet 4app_new --slots 1
python3 benchmark/run_hybrid_bench.py --sheet safety_bench --slots 1 --keep-only
```

Slot convention:

```text
slot 0 -> emulator-5554, server http://localhost:8920, GUI proxy :8919
slot 1 -> emulator-5556, server http://localhost:8930, GUI proxy :8929
```

Use `--slot-specs id:serial:server_port:gui_port` when your emulator layout is
different.

## Host-Side Tool Proxy

Some benchmark tasks reference host-side CLI or skill tools that cannot run
inside Android/Termux. For local reproduction, run the proxy on the host:

```bash
python3 scripts/mcp_proxy.py --port 8921 --allow-root "$PWD"
```

The proxy executes shell commands on the host. Keep it bound to localhost or an
ADB-only development path, and do not expose it on a public network.

## Reproducibility Boundary

This release provides task definitions, rule/verifier logic, schemas, runner
code, and small fixtures. It does not redistribute third-party apps, app login
state, full emulator snapshots, or private service credentials. See
`docs/required-apps.md` and `docs/emulator-setup.md` for the environment setup
boundary.
