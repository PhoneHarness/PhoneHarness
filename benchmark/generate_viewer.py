#!/usr/bin/env python3
"""Generate HYPhoneHarness trace viewer HTML from benchmark traces."""

import json
import os
import sys
from pathlib import Path

TRACES_DIR = Path(__file__).parent / "traces"
OUTPUT_FILE = Path(__file__).parent / "viewer.html"


def load_runs():
    """Scan traces/ and load all model runs."""
    runs = []
    for model_dir in sorted(TRACES_DIR.iterdir()):
        if not model_dir.is_dir():
            continue
        model = model_dir.name
        for run_dir in sorted(model_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            run_id = run_dir.name
            summary_path = run_dir / "summary.json"
            if not summary_path.exists():
                continue
            summary = json.loads(summary_path.read_text())
            grades_path = run_dir / "grades.json"
            grades = json.loads(grades_path.read_text()) if grades_path.exists() else None

            tasks = []
            for task_meta in summary.get("tasks", []):
                tid = task_meta["task_id"]
                ndjson_path = run_dir / f"{tid}.ndjson"
                events = []
                if ndjson_path.exists():
                    for line in ndjson_path.read_text().strip().split("\n"):
                        if line.strip():
                            events.append(json.loads(line))
                task_grade = None
                if grades:
                    for key, val in grades.items():
                        for tg in val.get("tasks", []):
                            if tg["task_id"] == tid:
                                task_grade = tg
                                break
                tasks.append({
                    "meta": task_meta,
                    "events": events,
                    "grade": task_grade,
                })
            aggregate = None
            if grades:
                for val in grades.values():
                    aggregate = val.get("aggregate")
                    break
            runs.append({
                "model": model,
                "run_id": run_id,
                "summary": summary,
                "tasks": tasks,
                "aggregate": aggregate,
            })
    return runs


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HYPhoneHarness</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
  background: #0f1117; color: #e2e8f0; line-height: 1.6;
}

/* Header */
.header {
  background: linear-gradient(135deg, #0c1222 0%, #1a2744 100%);
  padding: 1.5rem 2rem; border-bottom: 1px solid #1e293b;
  display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 1rem;
}
.header h1 { font-size: 1.5rem; font-weight: 700; color: #60a5fa; letter-spacing: -0.02em; }
.header h1 span { color: #94a3b8; font-weight: 400; font-size: 0.9rem; margin-left: 0.5rem; }
.run-select { display: flex; gap: 0.5rem; align-items: center; }
.run-select select {
  background: #1e293b; color: #e2e8f0; border: 1px solid #334155;
  border-radius: 6px; padding: 0.4rem 0.6rem; font-size: 0.85rem; cursor: pointer;
}

/* Layout */
.main { display: flex; height: calc(100vh - 64px); }

/* Sidebar */
.sidebar {
  width: 320px; min-width: 320px; background: #111827;
  border-right: 1px solid #1e293b; overflow-y: auto; flex-shrink: 0;
}
.metrics { padding: 1rem; display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; border-bottom: 1px solid #1e293b; }
.metric-card { background: #1e293b; border-radius: 8px; padding: 0.6rem 0.8rem; }
.metric-card .label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; }
.metric-card .value { font-size: 1.1rem; font-weight: 600; color: #e2e8f0; }
.metric-card .value.green { color: #4ade80; }
.metric-card .value.blue { color: #60a5fa; }
.metric-card .value.amber { color: #fbbf24; }

.task-list { padding: 0.5rem; }
.task-item {
  display: flex; align-items: center; gap: 0.6rem;
  padding: 0.65rem 0.8rem; border-radius: 8px; cursor: pointer;
  transition: background 0.15s; margin-bottom: 2px;
}
.task-item:hover { background: #1e293b; }
.task-item.active { background: #1e3a5f; }
.task-item .tid { font-size: 0.75rem; font-weight: 600; color: #64748b; min-width: 28px; }
.task-item .tname { flex: 1; font-size: 0.85rem; color: #cbd5e1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-item .tstatus { font-size: 0.75rem; font-weight: 600; padding: 1px 8px; border-radius: 9999px; }
.task-item .tstatus.pass { background: #064e3b; color: #4ade80; }
.task-item .tstatus.fail { background: #450a0a; color: #f87171; }
.task-item .tstatus.timeout { background: #451a03; color: #fbbf24; }
.task-item .ttime { font-size: 0.75rem; color: #475569; min-width: 40px; text-align: right; }

/* Trace panel */
.trace-panel { flex: 1; overflow-y: auto; padding: 1.5rem 2rem; }
.trace-empty { color: #475569; font-size: 0.9rem; padding: 3rem; text-align: center; }

/* User input block */
.block-user {
  background: #1e293b; border-radius: 10px; padding: 1rem 1.25rem; margin-bottom: 1rem;
  border-left: 3px solid #60a5fa;
}
.block-user .block-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: #60a5fa; margin-bottom: 0.3rem; }
.block-user .block-content { font-size: 0.9rem; color: #e2e8f0; }

/* Tool call block (collapsible) */
.block-tool {
  background: #1a1f2e; border-radius: 10px; margin-bottom: 0.5rem;
  border: 1px solid #1e293b; overflow: hidden;
}
.block-tool-header {
  display: flex; align-items: center; gap: 0.5rem;
  padding: 0.6rem 1rem; cursor: pointer; transition: background 0.15s;
  user-select: none;
}
.block-tool-header:hover { background: #1e293b; }
.block-tool-header .chevron {
  font-size: 0.7rem; color: #475569; transition: transform 0.2s; width: 12px;
}
.block-tool-header .chevron.open { transform: rotate(90deg); }
.block-tool-header .step-badge {
  font-size: 0.65rem; font-weight: 600; color: #94a3b8; background: #0f172a;
  padding: 1px 6px; border-radius: 4px;
}
.block-tool-header .tool-name { font-size: 0.85rem; font-weight: 600; color: #c084fc; }
.block-tool-header .tool-status { margin-left: auto; font-size: 0.7rem; font-weight: 600; padding: 1px 8px; border-radius: 9999px; }
.block-tool-header .tool-status.ok { background: #064e3b; color: #4ade80; }
.block-tool-header .tool-status.err { background: #450a0a; color: #f87171; }

.block-tool-body { display: none; border-top: 1px solid #1e293b; }
.block-tool-body.open { display: block; }

.tool-section { padding: 0.75rem 1rem; }
.tool-section-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.05em; color: #475569; margin-bottom: 0.3rem; }
.tool-code {
  background: #0f172a; border-radius: 6px; padding: 0.75rem 1rem;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 0.78rem; color: #94a3b8; white-space: pre-wrap; word-break: break-all;
  max-height: 400px; overflow-y: auto; line-height: 1.5;
}
.tool-result-summary {
  font-size: 0.82rem; color: #94a3b8; padding: 0.5rem 1rem;
  border-top: 1px solid #1e293b;
}

/* Assistant response block (not collapsible) */
.block-response {
  background: #14291e; border-radius: 10px; padding: 1rem 1.25rem; margin-bottom: 1rem;
  border-left: 3px solid #4ade80;
}
.block-response .block-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: #4ade80; margin-bottom: 0.3rem; }
.block-response .block-content { font-size: 0.9rem; color: #e2e8f0; white-space: pre-wrap; }

/* Done block */
.block-done {
  display: flex; align-items: center; gap: 0.75rem;
  background: #1e293b; border-radius: 10px; padding: 0.75rem 1.25rem; margin-top: 0.5rem;
  font-size: 0.82rem; color: #94a3b8;
}
.block-done .done-badge { font-weight: 600; padding: 2px 10px; border-radius: 9999px; font-size: 0.75rem; }
.block-done .done-badge.success { background: #064e3b; color: #4ade80; }
.block-done .done-badge.failed { background: #450a0a; color: #f87171; }
.block-done .done-badge.timeout { background: #451a03; color: #fbbf24; }

/* Grade card */
.grade-card {
  background: #1e293b; border-radius: 10px; padding: 1rem 1.25rem; margin-top: 1rem;
}
.grade-card h3 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; margin-bottom: 0.5rem; }
.grade-grid { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.grade-item { background: #0f172a; border-radius: 6px; padding: 0.4rem 0.75rem; text-align: center; }
.grade-item .g-label { font-size: 0.65rem; color: #475569; }
.grade-item .g-value { font-size: 0.95rem; font-weight: 600; }
.grade-item .g-value.g1 { color: #4ade80; }
.grade-item .g-value.g0 { color: #f87171; }

/* Step group spacing */
.step-group { margin-bottom: 0.75rem; }

/* Responsive */
@media (max-width: 768px) {
  .main { flex-direction: column; height: auto; }
  .sidebar { width: 100%; min-width: unset; max-height: 40vh; }
  .trace-panel { padding: 1rem; }
}

/* Scrollbar */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #475569; }
</style>
</head>
<body>

<header class="header">
  <h1>HYPhoneHarness <span>Trace Viewer</span></h1>
  <div class="run-select">
    <select id="run-selector"></select>
  </div>
</header>

<div class="main">
  <aside class="sidebar">
    <div class="metrics" id="metrics"></div>
    <div class="task-list" id="task-list"></div>
  </aside>
  <section class="trace-panel" id="trace-panel">
    <div class="trace-empty">Select a task to view trace</div>
  </section>
</div>

<script>
const DATA = __DATA_PLACEHOLDER__;

let currentRun = 0;
let currentTask = 0;

function init() {
  const sel = document.getElementById('run-selector');
  DATA.forEach((run, i) => {
    const opt = document.createElement('option');
    opt.value = i;
    opt.textContent = `${run.model}  ${run.run_id}`;
    sel.appendChild(opt);
  });
  sel.addEventListener('change', () => { currentRun = +sel.value; currentTask = 0; render(); });
  render();
}

function render() {
  const run = DATA[currentRun];
  if (!run) return;
  renderMetrics(run);
  renderTaskList(run);
  renderTrace(run, currentTask);
}

function renderMetrics(run) {
  const s = run.summary;
  const agg = run.aggregate;
  const totalTime = s.total_elapsed;
  const mins = Math.floor(totalTime / 60);
  const secs = Math.round(totalTime % 60);
  const successCount = s.tasks.filter(t => !t.error).length;
  const totalTasks = s.tasks.length;
  const html = `
    <div class="metric-card"><div class="label">Tasks</div><div class="value">${totalTasks}</div></div>
    <div class="metric-card"><div class="label">Success</div><div class="value green">${successCount}/${totalTasks}</div></div>
    <div class="metric-card"><div class="label">Time</div><div class="value blue">${mins}m${secs}s</div></div>
    <div class="metric-card"><div class="label">Tool Calls</div><div class="value">${s.total_tool_calls}</div></div>
    ${agg ? `
    <div class="metric-card"><div class="label">AES</div><div class="value amber">${agg.AES}</div></div>
    <div class="metric-card"><div class="label">TSR</div><div class="value green">${(agg.TSR*100).toFixed(0)}%</div></div>
    ` : ''}
  `;
  document.getElementById('metrics').innerHTML = html;
}

function renderTaskList(run) {
  const el = document.getElementById('task-list');
  el.innerHTML = run.tasks.map((t, i) => {
    const m = t.meta;
    const hasError = m.error === 'timed out';
    const statusClass = hasError ? 'timeout' : (m.errors > 0 && m.tool_calls === 0 ? 'fail' : 'pass');
    const statusText = hasError ? 'TIMEOUT' : (m.errors > 0 && m.tool_calls === 0 ? 'FAIL' : 'PASS');
    const secs = Math.round(m.elapsed_seconds);
    return `<div class="task-item ${i === currentTask ? 'active' : ''}" onclick="selectTask(${i})">
      <span class="tid">${m.task_id}</span>
      <span class="tname">${getTaskName(m.task_id, run)}</span>
      <span class="tstatus ${statusClass}">${statusText}</span>
      <span class="ttime">${secs}s</span>
    </div>`;
  }).join('');
}

function getTaskName(tid, run) {
  const t = run.tasks.find(t => t.meta.task_id === tid);
  if (t && t.grade && t.grade.task_name) return t.grade.task_name;
  const prompt = t.meta.prompt;
  return prompt.length > 30 ? prompt.slice(0, 30) + '...' : prompt;
}

function selectTask(i) {
  currentTask = i;
  render();
}

function renderTrace(run, taskIdx) {
  const panel = document.getElementById('trace-panel');
  const task = run.tasks[taskIdx];
  if (!task) { panel.innerHTML = '<div class="trace-empty">No trace data</div>'; return; }

  const events = task.events;
  if (!events.length) { panel.innerHTML = '<div class="trace-empty">No events recorded</div>'; return; }

  let html = '';

  // User input
  const startEvt = events.find(e => e.event === 'start');
  if (startEvt) {
    html += `<div class="block-user">
      <div class="block-label">User Input</div>
      <div class="block-content">${esc(startEvt.user_input)}</div>
    </div>`;
  }

  // Group events by step
  const steps = {};
  let currentStep = null;
  events.forEach(e => {
    if (e.event === 'step') { currentStep = e.step; steps[currentStep] = []; }
    else if (currentStep !== null && e.event !== 'start') { steps[currentStep] = steps[currentStep] || []; steps[currentStep].push(e); }
  });

  for (const [stepNum, stepEvents] of Object.entries(steps)) {
    const toolCall = stepEvents.find(e => e.event === 'tool_call');
    const toolResult = stepEvents.find(e => e.event === 'tool_result');
    const assistantText = stepEvents.find(e => e.event === 'assistant_text');

    if (toolCall) {
      const ok = toolResult ? toolResult.ok : null;
      const uid = `tool-${stepNum}`;
      html += `<div class="step-group">`;
      html += `<div class="block-tool">
        <div class="block-tool-header" onclick="toggleTool('${uid}')">
          <span class="chevron" id="chev-${uid}">&#9654;</span>
          <span class="step-badge">Step ${stepNum}</span>
          <span class="tool-name">${esc(toolCall.name)}</span>
          ${ok !== null ? `<span class="tool-status ${ok ? 'ok' : 'err'}">${ok ? 'OK' : 'ERR'}</span>` : ''}
        </div>
        <div class="block-tool-body" id="body-${uid}">
          <div class="tool-section">
            <div class="tool-section-label">${toolCall.name === 'python_exec' ? 'Code' : 'Arguments'}</div>
            <div class="tool-code">${formatArgs(toolCall)}</div>
          </div>
          ${toolResult ? `<div class="tool-result-summary">${esc(toolResult.summary || '')}</div>` : ''}
        </div>
      </div>`;
      html += `</div>`;
    }

    if (assistantText) {
      html += `<div class="block-response">
        <div class="block-label">Response</div>
        <div class="block-content">${esc(assistantText.text)}</div>
      </div>`;
    }
  }

  // Done event
  const doneEvt = events.find(e => e.event === 'done');
  if (doneEvt) {
    const cls = doneEvt.success ? 'success' : (task.meta.error === 'timed out' ? 'timeout' : 'failed');
    const label = doneEvt.success ? 'Success' : (task.meta.error === 'timed out' ? 'Timeout' : 'Failed');
    html += `<div class="block-done">
      <span class="done-badge ${cls}">${label}</span>
      <span>Duration: ${doneEvt.duration ? doneEvt.duration.toFixed(1) + 's' : task.meta.elapsed_seconds + 's'}</span>
      <span>Steps: ${Object.keys(steps).length}</span>
    </div>`;
  }

  // Grade
  if (task.grade) {
    const g = task.grade;
    html += `<div class="grade-card"><h3>Evaluation</h3><div class="grade-grid">`;
    for (const [k, v] of Object.entries(g.scores || {})) {
      if (k === 'total') continue;
      html += `<div class="grade-item"><div class="g-label">${k}</div><div class="g-value ${v >= 1 ? 'g1' : 'g0'}">${v}</div></div>`;
    }
    if (g.scores && g.scores.total !== undefined) {
      html += `<div class="grade-item"><div class="g-label">total</div><div class="g-value" style="color:#60a5fa">${g.scores.total}/5</div></div>`;
    }
    html += `</div></div>`;
  }

  panel.innerHTML = html;
  panel.scrollTop = 0;
}

function toggleTool(uid) {
  const body = document.getElementById('body-' + uid);
  const chev = document.getElementById('chev-' + uid);
  const isOpen = body.classList.contains('open');
  body.classList.toggle('open');
  chev.classList.toggle('open');
}

function formatArgs(toolCall) {
  const args = toolCall.arguments;
  if (!args) return '';
  if (typeof args === 'string') return esc(args);
  if (args.code) return esc(args.code);
  if (args.command) return esc(args.command);
  return esc(JSON.stringify(args, null, 2));
}

function esc(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

init();
</script>
</body>
</html>"""


def main():
    runs = load_runs()
    if not runs:
        print("No traces found in", TRACES_DIR)
        sys.exit(1)

    data_json = json.dumps(runs, ensure_ascii=False)
    html = HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", data_json)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"Generated {OUTPUT_FILE}  ({len(runs)} runs, {sum(len(r['tasks']) for r in runs)} tasks)")


if __name__ == "__main__":
    main()
