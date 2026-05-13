#!/usr/bin/env python3
"""
trace2html_all — 将多题 trace 合并到一个 HTML 汇总页面

用法:
  python scripts/trace2html_all.py benchmark/traces/hybrid_bench/4app_new -o dashboard.html --open
  python scripts/trace2html_all.py benchmark/traces/hybrid_bench/4app_new  # 默认输出到该目录下 all-traces.html
"""

import json, os, sys, argparse, subprocess, platform, re
from pathlib import Path

# 复用 trace2html 的解析逻辑
sys.path.insert(0, os.path.dirname(__file__))
from trace2html import parse_trace_dir, find_trace_dirs


def collect_all_traces(root):
    """收集 root 下所有 trace，按 task_id 去重，始终取最新 run。
    如果最新 run 没截图但旧 run 有，则把截图路径指向旧 run。"""
    dirs = find_trace_dirs(root)
    
    # task_id -> (run_ts, dir, task_id, task_obj, steps)  取最新
    best = {}
    # task_id -> {ss_suffix: (run_dir, ss_dir_name)}  记录所有有截图的 run
    ss_registry = {}
    
    for d in dirs:
        traces = parse_trace_dir(d)
        files_in_dir = os.listdir(d)
        for task_id, task_obj, steps in traces:
            if not steps:
                continue
            run_ts = os.path.basename(d)
            # 记录最新 run
            if task_id not in best or run_ts > best[task_id][0]:
                best[task_id] = (run_ts, d, task_id, task_obj, steps)
            # 记录有截图的 run（取最新的有截图 run）
            for fn in files_in_dir:
                if fn.startswith(f"{task_id}_screenshots") and os.path.isdir(os.path.join(d, fn)):
                    ss_registry.setdefault(task_id, {})[fn] = d

    # 回填截图路径：如果最新 run 没截图但旧 run 有
    for task_id, (run_ts, d, tid, tobj, steps) in best.items():
        files_in_dir = os.listdir(d)
        has_ss = any(
            fn.startswith(f"{task_id}_screenshots") and os.path.isdir(os.path.join(d, fn))
            for fn in files_in_dir
        )
        if not has_ss and task_id in ss_registry:
            # 找旧 run 的截图，替换 steps 里的 img 路径
            for s in steps:
                old_img = s.get("img", "")
                parts = old_img.split("/")
                if len(parts) == 2:
                    ss_dir_name = parts[0]  # e.g. SC11_002_screenshots_1
                    img_file = parts[1]     # e.g. step_001.png
                    # 尝试精确匹配
                    if ss_dir_name in ss_registry[task_id]:
                        old_run_dir = ss_registry[task_id][ss_dir_name]
                        s["img"] = os.path.join(os.path.relpath(old_run_dir, d), ss_dir_name, img_file)
                    else:
                        # 数字后缀的 _1 → 尝试匹配不带后缀的 _screenshots
                        base_ss = f"{task_id}_screenshots"
                        if base_ss in ss_registry[task_id]:
                            old_run_dir = ss_registry[task_id][base_ss]
                            s["img"] = os.path.join(os.path.relpath(old_run_dir, d), base_ss, img_file)

    items = sorted(best.values(), key=lambda x: x[2])
    return [(run_ts, d, tid, tobj, steps) for run_ts, d, tid, tobj, steps in items]


def relative_img_path(trace_dir, root_dir, img_field):
    """把 img 字段转成相对于 root_dir 的路径"""
    abs_img = os.path.join(trace_dir, img_field)
    try:
        return os.path.relpath(abs_img, root_dir)
    except ValueError:
        return img_field


def generate_all_html(root, output_path, do_open=False):
    traces = collect_all_traces(root)
    if not traces:
        print("No traces found!")
        return

    print(f"Found {len(traces)} tasks\n")

    # 构建 JS 数据
    all_tasks = []
    for run_ts, trace_dir, task_id, task_obj, steps in traces:
        # 修正 img 路径为相对于 output 所在目录
        out_dir = os.path.dirname(os.path.abspath(output_path))
        for s in steps:
            s["img"] = relative_img_path(trace_dir, out_dir, s["img"])

        task_obj["run"] = run_ts
        task_obj["dir"] = os.path.relpath(trace_dir, out_dir)
        all_tasks.append({
            "task": task_obj,
            "steps": steps,
        })

        status = task_obj["result"]
        name = task_obj["shortName"]
        n = len(steps)
        print(f"  {task_id:12s}  {status:4s}  {n:3d} steps  {name}")

    data_json = json.dumps(all_tasks, ensure_ascii=False, indent=None)

    html = DASHBOARD_TEMPLATE.replace("__ALL_DATA__", data_json)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    kb = os.path.getsize(output_path) / 1024
    print(f"\n✓ Generated: {output_path} ({kb:.0f} KB, {len(traces)} tasks)")

    if do_open:
        s = platform.system()
        if s == "Darwin":
            subprocess.run(["open", output_path])
        elif s == "Linux":
            subprocess.run(["xdg-open", output_path])


DASHBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agent Trace Dashboard</title>
<style>
:root{--bg:#0f1117;--surface:#1a1d27;--surface2:#242836;--border:#2e3348;--text:#e4e6f0;--text2:#8b8fa8;--accent:#6c7cff;--red:#ff6b6b;--green:#5cffa0;--cyan:#5ce1ff;--orange:#ffaa5c;--radius:10px}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',sans-serif;background:var(--bg);color:var(--text);height:100vh;overflow:hidden;display:flex;flex-direction:column}

.topbar{background:rgba(15,17,23,0.9);backdrop-filter:blur(20px);border-bottom:1px solid var(--border);padding:12px 24px;display:flex;align-items:center;gap:16px;flex-shrink:0}
.topbar h1{font-size:17px;font-weight:600;background:linear-gradient(135deg,var(--accent),var(--cyan));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.topbar .stats{color:var(--text2);font-size:12px;margin-left:auto;display:flex;gap:16px}
.topbar .stat-val{font-weight:600;color:var(--text)}

.container{display:flex;flex:1;overflow:hidden}

/* Sidebar */
.sidebar{width:320px;flex-shrink:0;border-right:1px solid var(--border);display:flex;flex-direction:column;background:var(--surface)}
.sidebar-header{padding:12px 16px;border-bottom:1px solid var(--border);font-size:12px;color:var(--text2)}
.sidebar-filter{padding:8px 12px;border-bottom:1px solid var(--border);display:flex;gap:6px}
.filter-btn{font-size:11px;padding:4px 10px;border-radius:100px;border:1px solid var(--border);background:transparent;color:var(--text2);cursor:pointer;transition:all .15s}
.filter-btn:hover,.filter-btn.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.filter-btn.active-pass{background:rgba(92,255,160,0.15);color:var(--green);border-color:var(--green)}
.filter-btn.active-fail{background:rgba(255,107,107,0.15);color:var(--red);border-color:var(--red)}
.task-list{flex:1;overflow-y:auto}
.task-item{padding:10px 16px;border-bottom:1px solid rgba(46,51,72,0.3);cursor:pointer;transition:background .15s;display:flex;align-items:center;gap:10px}
.task-item:hover{background:var(--surface2)}
.task-item.active{background:rgba(108,124,255,0.1);border-left:3px solid var(--accent)}
.task-item .tid{font-size:11px;color:var(--text2);width:70px;flex-shrink:0;font-variant-numeric:tabular-nums}
.task-item .tname{font-size:13px;font-weight:500;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.task-item .tbadge{font-size:10px;padding:2px 8px;border-radius:100px;font-weight:600;flex-shrink:0}
.tbadge-pass{background:rgba(92,255,160,0.15);color:var(--green)}
.tbadge-fail{background:rgba(255,107,107,0.15);color:var(--red)}
.task-item .tsteps{font-size:10px;color:var(--text2);flex-shrink:0;width:35px;text-align:right}

/* Main content */
.main{flex:1;overflow-y:auto;padding:24px 28px 64px}

.task-title{font-size:15px;font-weight:600;margin-bottom:16px;padding:12px 16px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);display:flex;align-items:center;gap:10px;flex-wrap:wrap;line-height:1.5}
.badge{font-size:11px;padding:3px 10px;border-radius:100px;font-weight:600;flex-shrink:0}
.badge-fail{background:rgba(255,107,107,0.15);color:var(--red)}.badge-pass{background:rgba(92,255,160,0.15);color:var(--green)}
.task-detail{font-size:12px;color:var(--text2);margin-left:auto}

.summary-bar{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}
.summary-card{flex:1;min-width:120px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:12px 16px}
.summary-card .label{font-size:10px;color:var(--text2);text-transform:uppercase;letter-spacing:.5px}
.summary-card .value{font-size:22px;font-weight:700;margin-top:3px}
.summary-card .sub{font-size:10px;color:var(--text2);margin-top:2px}

.section-title{font-size:13px;font-weight:600;margin-bottom:10px;color:var(--text2)}
.waterfall{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px;margin-bottom:22px;overflow-x:auto}
.wf-row{display:flex;align-items:center;gap:8px;padding:3px 0;border-bottom:1px solid rgba(46,51,72,0.25)}.wf-row:last-of-type{border-bottom:none}
.wf-label{width:140px;flex-shrink:0;font-size:10px;color:var(--text2);text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wf-bars{flex:1;display:flex;height:18px;min-width:200px}
.wf-bar{height:100%;border-radius:3px;min-width:2px;display:flex;align-items:center;justify-content:center;font-size:7px;font-weight:600;color:rgba(255,255,255,0.85)}
.bar-ss{background:linear-gradient(90deg,#5ce1ff,#3db8d8)}.bar-llm{background:linear-gradient(90deg,#6c7cff,#4e5bbd)}.bar-act{background:linear-gradient(90deg,#5cffa0,#3dcc7a)}
.bar-llm.bottleneck{background:linear-gradient(90deg,#ff6b6b,#cc4444);box-shadow:0 0 8px rgba(255,107,107,0.3)}
.wf-time{width:50px;flex-shrink:0;font-size:10px;color:var(--text2);text-align:right;font-variant-numeric:tabular-nums}
.wf-legend{display:flex;gap:12px;margin-top:8px;padding-top:8px;border-top:1px solid var(--border)}
.legend-i{display:flex;align-items:center;gap:4px;font-size:10px;color:var(--text2)}.legend-dot{width:8px;height:8px;border-radius:2px}

.step-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);margin-bottom:10px;overflow:hidden;transition:border-color .15s}
.step-card:hover{border-color:var(--accent)}.step-card.is-bn{border-color:var(--red)}.step-card.is-bn .step-hdr{background:rgba(255,107,107,0.05)}
.step-hdr{display:flex;align-items:center;gap:10px;padding:8px 14px;cursor:pointer;user-select:none}
.step-num{width:26px;height:26px;border-radius:6px;background:var(--surface2);display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:var(--accent);flex-shrink:0}
.is-bn .step-num{background:rgba(255,107,107,0.15);color:var(--red)}
.step-info{flex:1;min-width:0}.step-action{font-size:11px;font-weight:600;display:flex;align-items:center;gap:5px}
.step-detail-text{font-size:10px;color:var(--text2);margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.step-chips{display:flex;gap:5px;flex-shrink:0;flex-wrap:wrap}
.chip{font-size:9px;padding:2px 7px;border-radius:100px;font-weight:500;font-variant-numeric:tabular-nums}
.chip-ss{background:rgba(92,225,255,0.12);color:var(--cyan)}.chip-llm{background:rgba(108,124,255,0.12);color:var(--accent)}.chip-act{background:rgba(92,255,160,0.12);color:var(--green)}
.chip-bn{background:rgba(255,107,107,0.15);color:var(--red)}.chip-call{background:rgba(255,170,92,0.15);color:var(--orange)}
.bn-badge{font-size:8px;padding:2px 6px;border-radius:100px;background:rgba(255,107,107,0.15);color:var(--red);font-weight:600}
.arrow{color:var(--text2);font-size:12px;transition:transform .2s;flex-shrink:0}.step-card.open .arrow{transform:rotate(180deg)}
.step-body{max-height:0;overflow:hidden;transition:max-height .3s ease}.step-card.open .step-body{max-height:3000px}
.step-body-inner{padding:0 14px 14px;display:grid;grid-template-columns:1fr 1fr;gap:12px}
.step-img{border-radius:6px;overflow:hidden;border:1px solid var(--border);background:#000;display:flex;align-items:center;justify-content:center;max-height:440px}
.step-img img{width:100%;height:100%;object-fit:contain}.step-img .no-img{color:var(--text2);font-size:11px;padding:30px;text-align:center}
.detail-tbl{font-size:11px}.d-row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid rgba(46,51,72,0.3)}.d-row:last-child{border-bottom:none}
.d-label{color:var(--text2)}.d-val{font-weight:500;font-variant-numeric:tabular-nums}
.tok-bar-bg{height:4px;border-radius:2px;background:var(--surface2);overflow:hidden;margin-top:6px}.tok-bar-fill{height:100%;border-radius:2px;background:linear-gradient(90deg,var(--accent),var(--cyan))}
.tok-label{font-size:8px;color:var(--text2);margin-top:2px}
.thought-box{grid-column:1/-1;background:var(--surface2);border-radius:6px;padding:10px 12px;font-size:11px;line-height:1.6;color:var(--text2);max-height:180px;overflow-y:auto;white-space:pre-wrap;word-break:break-word}
.thought-box strong{color:var(--text)}
.app-divider{margin:14px 0 8px;padding:6px 12px;background:var(--surface2);border-radius:6px;font-size:12px;font-weight:600;color:var(--cyan);border-left:3px solid var(--cyan)}
.empty{padding:80px;text-align:center;color:var(--text2);font-size:14px}

@media(max-width:900px){.sidebar{width:240px}.step-body-inner{grid-template-columns:1fr}}
::-webkit-scrollbar{width:5px;height:5px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
</style>
</head>
<body>
<div class="topbar">
  <h1>Agent Trace Dashboard</h1>
  <div class="stats" id="topStats"></div>
</div>
<div class="container">
  <div class="sidebar">
    <div class="sidebar-filter" id="filterBar"></div>
    <div class="task-list" id="taskList"></div>
  </div>
  <div class="main" id="main"><div class="empty">← 选择一个任务查看轨迹</div></div>
</div>

<script>
const ALL = __ALL_DATA__;

const fmtMs=ms=>!ms||ms<=0?'-':ms<1000?Math.round(ms)+'ms':(ms/1000).toFixed(1)+'s';
const fmtS=ms=>(ms/1000).toFixed(1)+'s';
const esc=s=>{const d=document.createElement('div');d.textContent=s;return d.innerHTML;};

// Stats
const passCount=ALL.filter(t=>t.task.result==='PASS').length;
const failCount=ALL.filter(t=>t.task.result!=='PASS').length;
document.getElementById('topStats').innerHTML=`
  <span><span class="stat-val">${ALL.length}</span> 题</span>
  <span style="color:var(--green)"><span class="stat-val">${passCount}</span> PASS</span>
  <span style="color:var(--red)"><span class="stat-val">${failCount}</span> FAIL</span>
`;

// Filter
let currentFilter='all';
const filterBar=document.getElementById('filterBar');
filterBar.innerHTML=`
  <button class="filter-btn active" data-f="all">全部 ${ALL.length}</button>
  <button class="filter-btn" data-f="pass">PASS ${passCount}</button>
  <button class="filter-btn" data-f="fail">FAIL ${failCount}</button>
`;
filterBar.querySelectorAll('.filter-btn').forEach(btn=>{
  btn.addEventListener('click',()=>{
    filterBar.querySelectorAll('.filter-btn').forEach(b=>b.className='filter-btn');
    currentFilter=btn.dataset.f;
    btn.className='filter-btn active'+(currentFilter==='pass'?'-pass':currentFilter==='fail'?'-fail':'');
    renderList();
  });
});

// Task list
const taskList=document.getElementById('taskList');
let activeIdx=-1;

function renderList(){
  taskList.innerHTML='';
  ALL.forEach((t,i)=>{
    if(currentFilter==='pass'&&t.task.result!=='PASS')return;
    if(currentFilter==='fail'&&t.task.result==='PASS')return;
    const el=document.createElement('div');
    el.className='task-item'+(i===activeIdx?' active':'');
    const bclass=t.task.result==='PASS'?'tbadge-pass':'tbadge-fail';
    el.innerHTML=`<span class="tid">${esc(t.task.id)}</span><span class="tname">${esc(t.task.shortName)}</span><span class="tbadge ${bclass}">${t.task.result}</span><span class="tsteps">${t.steps.length}步</span>`;
    el.addEventListener('click',()=>{activeIdx=i;renderList();renderDetail(t);});
    taskList.appendChild(el);
  });
}

function renderDetail(t){
  const T=t.task, S=t.steps, main=document.getElementById('main');
  const totalTime=S.reduce((a,s)=>a+s.totalMs,0);
  const totalLLM=S.reduce((a,s)=>a+s.llmMs,0);
  const totalSS=S.reduce((a,s)=>a+s.ssMs,0);
  const totalTokIn=S.reduce((a,s)=>a+s.tokensIn,0);
  const totalTokOut=S.reduce((a,s)=>a+s.tokensOut,0);
  const maxLLM=Math.max(...S.map(s=>s.llmMs));
  const bnIdx=S.findIndex(s=>s.llmMs===maxLLM);
  const maxTotal=Math.max(...S.map(s=>s.totalMs),1);
  const maxTokOut=Math.max(...S.map(s=>s.tokensOut),1);
  const callUserCount=S.filter(s=>s.actionType==='call_user').length;

  let h='';
  const rb=T.result==='PASS'?'<span class="badge badge-pass">✓ PASS</span>':'<span class="badge badge-fail">✗ '+esc(T.result)+'</span>';
  h+=`<div class="task-title">${rb}<span>${esc(T.name||T.shortName)}</span><span class="task-detail">${esc(T.detail||'')}</span></div>`;

  h+=`<div class="summary-bar">
    <div class="summary-card"><div class="label">总耗时</div><div class="value" style="color:var(--accent)">${fmtS(totalTime)}</div><div class="sub">${S.length} 步 · ${esc(T.difficulty||'')}</div></div>
    <div class="summary-card"><div class="label">LLM</div><div class="value" style="color:var(--accent)">${fmtS(totalLLM)}</div><div class="sub">${totalTime>0?(totalLLM/totalTime*100).toFixed(0):0}%</div></div>
    <div class="summary-card"><div class="label">截图</div><div class="value" style="color:var(--cyan)">${fmtS(totalSS)}</div><div class="sub">${totalTime>0?(totalSS/totalTime*100).toFixed(0):0}%</div></div>
    <div class="summary-card"><div class="label">Tokens</div><div class="value" style="color:var(--green)">${(totalTokIn+totalTokOut).toLocaleString()}</div><div class="sub">in ${totalTokIn.toLocaleString()} / out ${totalTokOut.toLocaleString()}</div></div>
    ${callUserCount>0?`<div class="summary-card"><div class="label">call_user</div><div class="value" style="color:var(--orange)">${callUserCount}</div><div class="sub">求助</div></div>`:''}
    ${T.blocker?`<div class="summary-card"><div class="label">Blocker</div><div class="value" style="color:var(--orange);font-size:12px">${esc(T.blocker)}</div></div>`:''}
  </div>`;

  // Verdict box: PASS/FAIL 判定理由
  const lastStep = S[S.length-1];
  const lastReasoning = lastStep ? lastStep.reasoning : '';
  const verdictColor = T.result==='PASS' ? 'var(--green)' : 'var(--red)';
  const verdictBg = T.result==='PASS' ? 'rgba(92,255,160,0.06)' : 'rgba(255,107,107,0.06)';
  const verdictBorder = T.result==='PASS' ? 'var(--green)' : 'var(--red)';
  const verdictIcon = T.result==='PASS' ? '✅' : '❌';

  let verdictLines = [];
  // 验证结果
  if(T.detail) verdictLines.push('<strong>验证结果：</strong>' + esc(T.detail));
  // Blocker
  if(T.blocker) verdictLines.push('<strong>Blocker：</strong>' + esc(T.blocker));
  // 模型最后一步的思考
  if(lastReasoning) verdictLines.push('<strong>模型最终判断：</strong>' + esc(lastReasoning.length > 300 ? lastReasoning.slice(0, 300) + '…' : lastReasoning));

  if(verdictLines.length > 0) {
    h+=`<div style="margin-bottom:18px;padding:14px 16px;background:${verdictBg};border:1px solid ${verdictBorder};border-radius:var(--radius);border-left:4px solid ${verdictBorder}">
      <div style="font-size:13px;font-weight:600;color:${verdictColor};margin-bottom:8px">${verdictIcon} ${T.result==='PASS'?'成功':'失败'}判定理由</div>
      <div style="font-size:12px;line-height:1.7;color:var(--text2)">${verdictLines.join('<br><br>')}</div>
    </div>`;
  }

  // Waterfall
  h+=`<div class="section-title">⏱ 瀑布图</div><div class="waterfall">`;
  for(let i=0;i<S.length;i++){
    const s=S[i],isBn=i===bnIdx;
    const dn=s.globalNum||s.num;
    const ap=s.app?(/^\d+$/.test(s.app)?' [R'+s.app+']':' ['+esc(s.app)+']'):'';
    const ssPct=s.ssMs/maxTotal*100,llmPct=s.llmMs/maxTotal*100,actPct=s.actionMs/maxTotal*100;
    h+=`<div class="wf-row"><div class="wf-label">${dn}${ap} · ${esc(s.actionType)}</div><div class="wf-bars">
      <div class="wf-bar bar-ss" style="width:${Math.max(ssPct,0.4)}%" title="截图 ${fmtMs(s.ssMs)}">${ssPct>8?fmtMs(s.ssMs):''}</div>
      <div class="wf-bar bar-llm${isBn?' bottleneck':''}" style="width:${Math.max(llmPct,0.4)}%" title="LLM ${fmtMs(s.llmMs)}">${llmPct>8?fmtMs(s.llmMs):''}</div>
      ${s.actionMs>0?`<div class="wf-bar bar-act" style="width:${Math.max(actPct,0.4)}%" title="Action ${fmtMs(s.actionMs)}">${actPct>8?fmtMs(s.actionMs):''}</div>`:''}
    </div><div class="wf-time">${fmtS(s.totalMs)}</div></div>`;
  }
  h+=`<div class="wf-legend"><div class="legend-i"><div class="legend-dot" style="background:var(--cyan)"></div>截图</div><div class="legend-i"><div class="legend-dot" style="background:var(--accent)"></div>LLM</div><div class="legend-i"><div class="legend-dot" style="background:var(--green)"></div>Action</div><div class="legend-i"><div class="legend-dot" style="background:var(--red)"></div>瓶颈</div></div></div>`;

  // Steps
  h+=`<div class="section-title">📋 ${S.length} 步详情</div>`;
  let lastApp='';
  for(let i=0;i<S.length;i++){
    const s=S[i],isBn=i===bnIdx,isCall=s.actionType==='call_user';
    if(s.app&&s.app!==lastApp){lastApp=s.app;const al=/^\d+$/.test(s.app)?'🔄 第'+s.app+'轮 GUI 尝试':'📱 '+esc(s.app);h+=`<div class="app-divider">${al}</div>`;}
    const dn=s.globalNum||s.num;
    const paramStr=Object.entries(s.params||{}).map(([k,v])=>k+': '+JSON.stringify(v)).join(', ')||'—';
    h+=`<div class="step-card${isBn?' is-bn':''}" data-idx="${i}"><div class="step-hdr">
      <div class="step-num">${dn}</div>
      <div class="step-info"><div class="step-action">${esc(s.tool)} → ${esc(s.actionType)}${isBn?' <span class="bn-badge">⚠瓶颈</span>':''}${isCall?' <span class="bn-badge" style="background:rgba(255,170,92,0.15);color:var(--orange)">📞求助</span>':''}</div>
        <div class="step-detail-text">${esc(s.summary)} · ${esc(paramStr)}</div></div>
      <div class="step-chips">
        <span class="chip chip-ss">${fmtMs(s.ssMs)}</span>
        <span class="chip ${isBn?'chip-bn':'chip-llm'}">${fmtMs(s.llmMs)}</span>
        ${s.actionMs>0?`<span class="chip chip-act">${fmtMs(s.actionMs)}</span>`:''}
        ${s.reasoning?'<span class="chip chip-llm">有 CoT</span>':s.modelRationale?'<span class="chip chip-llm">有模型说明</span>':''}
      </div><div class="arrow">▾</div></div>
    <div class="step-body"><div class="step-body-inner">
      <div class="step-img"><img src="${s.img}" alt="Step ${dn}" onerror="this.outerHTML='<div class=no-img>📷 无截图</div>'"></div>
      <div class="detail-tbl">
        <div class="d-row"><span class="d-label">总耗时</span><span class="d-val">${fmtMs(s.totalMs)}</span></div>
        <div class="d-row"><span class="d-label">截图</span><span class="d-val">${fmtMs(s.ssMs)}</span></div>
        <div class="d-row"><span class="d-label">LLM</span><span class="d-val">${fmtMs(s.llmMs)}</span></div>
        <div class="d-row"><span class="d-label">Action</span><span class="d-val">${fmtMs(s.actionMs)}</span></div>
        <div class="d-row"><span class="d-label">Tokens in</span><span class="d-val">${s.tokensIn.toLocaleString()}</span></div>
        <div class="d-row"><span class="d-label">Tokens out</span><span class="d-val">${s.tokensOut.toLocaleString()}</span></div>
        <div class="tok-bar-bg"><div class="tok-bar-fill" style="width:${(s.tokensOut/maxTokOut*100).toFixed(1)}%"></div></div>
      </div>
      ${s.reasoning?`<div class="thought-box"><strong>🧠 CoT：</strong>\n${esc(s.reasoning)}</div>`:''}
      ${!s.reasoning&&s.modelRationale?`<div class="thought-box"><strong>🧠 模型原始说明：</strong>\n${esc(s.modelRationale)}</div>`:''}
      ${s.modelAction?`<div class="thought-box" style="border-left:3px solid var(--orange)"><strong>🎯 模型动作：</strong>\n${esc(s.modelAction)}</div>`:''}
      ${s.output&&s.output!==s.summary?`<div class="thought-box" style="border-left:3px solid var(--cyan)"><strong>📤 完整输出：</strong>\n${esc(s.output)}</div>`
       :s.summary?`<div class="thought-box" style="border-left:3px solid var(--green)"><strong>📋 Action：</strong> ${esc(s.tool)} → ${esc(s.summary)}${Object.keys(s.params||{}).length>0?'\\n参数: '+esc(JSON.stringify(s.params)):''}</div>`:''}
    </div></div></div>`;
  }

  main.innerHTML=h;
  main.scrollTop=0;
  main.querySelectorAll('.step-hdr').forEach(hdr=>{
    hdr.addEventListener('click',()=>hdr.parentElement.classList.toggle('open'));
  });
}

renderList();
// Auto-select first
if(ALL.length>0){activeIdx=0;renderList();renderDetail(ALL[0]);}
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="trace2html_all: 多题 trace → 汇总 HTML dashboard")
    parser.add_argument("path", help="包含 trace run 目录的根路径")
    parser.add_argument("-o", "--output", help="输出 HTML 路径（默认: <path>/all-traces.html）")
    parser.add_argument("--open", action="store_true", help="生成后自动打开")
    args = parser.parse_args()

    root = os.path.abspath(args.path)
    output = args.output or os.path.join(root, "all-traces.html")
    output = os.path.abspath(output)

    generate_all_html(root, output, do_open=args.open)


if __name__ == "__main__":
    main()
