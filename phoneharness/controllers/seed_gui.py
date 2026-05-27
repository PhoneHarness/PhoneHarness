"""Seed 2.0 GUI controller for PhoneHarness server.

Implements a Seed-style GUI agent loop inside the PhoneHarness server.

Key protocol choices:
  - Seed XML action format, NOT OpenAI function calling
  - role:"tool" screenshots with PNG base64
  - 0-1000 normalized coordinates
  - Rebuild context each step, keep last 3 images
  - Direct API call to the configured GUI model endpoint

GUI execution goes through host-side gui_proxy (http://10.0.2.2:8919).
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import Any, Callable
from urllib import error, parse, request

from ..model.client import (
    _chat_payload_to_responses_payload,
    _normalize_api_format,
    _responses_payload_to_chat_payload,
)

_TRANSIENT_HTTP_CODES = {404, 429, 500, 502, 503, 504}

# ── Seed Adapter ─────────────────────────────────────────────────────

SEED_SYSTEM_PROMPT = (
    "You are provided with a task description, a history of previous actions, "
    "and corresponding screenshots. Your goal is to perform the next action to "
    "complete the task. Please note that if performing the same action multiple "
    "times results in a static screen with no changes, you should attempt a "
    "modified or alternative action. You start on the Android home screen. "
    "Use the open_app function to launch the app you need."
)

SEED_TOOLS = [
    {"type": "function", "name": "call_user", "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": []}, "description": "Interact with user."},
    {"type": "function", "name": "open_app", "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]}, "description": "Open an app by name."},
    {"type": "function", "name": "click", "parameters": {"type": "object", "properties": {"point": {"type": "string"}}, "required": ["point"]}, "description": "Click at coordinates. Format: <point>x y</point>"},
    {"type": "function", "name": "drag", "parameters": {"type": "object", "properties": {"start_point": {"type": "string"}, "end_point": {"type": "string"}}, "required": ["start_point", "end_point"]}, "description": "Drag action."},
    {"type": "function", "name": "finished", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "Provide the final answer or response to complete the task."}}, "required": []}, "description": "This function is used to indicate the completion of a task by providing the final answer or response."},
    {"type": "function", "name": "press_home", "parameters": {}, "description": "Press home button."},
    {"type": "function", "name": "press_back", "parameters": {}, "description": "Press back button."},
    {"type": "function", "name": "scroll", "parameters": {"type": "object", "properties": {"point": {"type": "string"}, "direction": {"type": "string", "enum": ["up", "down", "left", "right"]}}, "required": ["direction", "point"]}, "description": "Scroll action."},
    {"type": "function", "name": "type", "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}, "description": "Type content."},
    {"type": "function", "name": "wait", "parameters": {"type": "object", "properties": {"time": {"type": "integer"}}, "required": []}, "description": "Wait."},
]

SEED_TOOL_PROMPT = """## Function Definition

- You have access to the following functions:
""" + "\n".join(json.dumps(t, ensure_ascii=False) for t in SEED_TOOLS) + """

- To call a function, use the following structure:

<think> reasoning process </think>
<tool_call><function=example_function_name><parameter=example_parameter_1>value_1</parameter></function></tool_call>

## Important Notes
- Function calls must begin with <function= and end with </function>.
- All required parameters must be explicitly provided.
- Coordinate system: 0-1000 relative. (0,0) is top-left, (1000,1000) is bottom-right.
- When you have completed the task or found the required information, you MUST call the finished function with the result. Do not continue performing actions after the goal is achieved.
- If you cannot find what you are looking for after scrolling through the full page or trying 3 different approaches, call finished to report what you found or that the target was not found.

## App-specific hints for mDouban
- After typing in search box, tap the "搜索" button (top-right). Enter does NOT trigger search.
- Search results have tabs (电影/图书/音乐). Tap correct tab.
- Detail page layout top→bottom: poster → rating → plot → cast → reviews → **写短评 button** (scroll DOWN).
- Rating popup: select stars + type review + tap 提交. ALL required for DB write.
"""


def parse_seed_action(response: str) -> dict:
    """Parse Seed 2.0 XML actions."""
    content = response.split("</think>")[-1] if "</think>" in response else response

    m = re.search(r'<function=finished>(.*?)</function>', content, re.DOTALL)
    if m:
        cm = re.search(r'<parameter=content>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        return {"action_type": "finished", "text": cm.group(1).strip() if cm else ""}

    m = re.search(r'<function=click>(.*?)</function>', content, re.DOTALL)
    if m:
        pm = re.search(r'<parameter=point>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        if pm:
            point = re.sub(r'</?point>', '', pm.group(1)).strip()
            parts = re.split(r'[\s,]+', point)
            try:
                if len(parts) >= 2:
                    return {"action_type": "click", "x": int(float(parts[0])), "y": int(float(parts[1]))}
            except (ValueError, IndexError):
                pass  # fall through to unknown

    m = re.search(r'<function=scroll>(.*?)</function>', content, re.DOTALL)
    if m:
        dm = re.search(r'<parameter=direction>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        pm = re.search(r'<parameter=point>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        direction = dm.group(1).strip() if dm else "down"
        point = "500 500"
        if pm:
            point = re.sub(r'</?point>', '', pm.group(1)).strip()
        parts = re.split(r'[\s,]+', point)
        try:
            x = int(float(parts[0]))
            y = int(float(parts[1])) if len(parts) >= 2 else 500
        except (ValueError, IndexError):
            x, y = 500, 500
        return {"action_type": "scroll", "direction": direction, "x": x, "y": y}

    m = re.search(r'<function=type>(.*?)</function>', content, re.DOTALL)
    if m:
        cm = re.search(r'<parameter=content>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        return {"action_type": "type", "text": cm.group(1).strip() if cm else ""}

    m = re.search(r'<function=drag>(.*?)</function>', content, re.DOTALL)
    if m:
        points = re.findall(r'<point>\s*([\d.]+)\s+([\d.]+)\s*</point>', m.group(1))
        if len(points) >= 2:
            try:
                return {"action_type": "drag",
                        "start_x": int(float(points[0][0])), "start_y": int(float(points[0][1])),
                        "end_x": int(float(points[1][0])), "end_y": int(float(points[1][1]))}
            except (ValueError, IndexError):
                return {"action_type": "drag",
                        "start_x": 500, "start_y": 300, "end_x": 500, "end_y": 700}

    if '<function=press_home>' in content:
        return {"action_type": "press_home"}
    if '<function=press_back>' in content:
        return {"action_type": "press_back"}
    if '<function=wait>' in content:
        return {"action_type": "wait"}

    m = re.search(r'<function=open_app>(.*?)</function>', content, re.DOTALL)
    if m:
        nm = re.search(r'<parameter=app_name>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        return {"action_type": "open_app", "app_name": nm.group(1).strip() if nm else ""}

    m = re.search(r'<function=call_user>(.*?)</function>', content, re.DOTALL)
    if m:
        cm = re.search(r'<parameter=content>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        return {"action_type": "call_user", "text": cm.group(1).strip() if cm else ""}

    return {"action_type": "unknown", "raw": content[:500]}


# ── GUI proxy client (stdlib only, runs inside Termux) ───────────────

APP_MAP = {
    "豆瓣": "com.phoneuse.mdouban", "mdouban": "com.phoneuse.mdouban",
    "小红书": "com.phoneuse.mxiaohongshu", "mxiaohongshu": "com.phoneuse.mxiaohongshu",
    "B站": "com.phoneuse.mbilibili", "b站": "com.phoneuse.mbilibili",
    "bilibili": "com.phoneuse.mbilibili", "mbilibili": "com.phoneuse.mbilibili",
    "哔哩哔哩": "com.phoneuse.mbilibili",
    "美团外卖": "com.phoneuse.mmeituan_waimai", "美团": "com.phoneuse.mmeituan_waimai",
    "mmeituan_waimai": "com.phoneuse.mmeituan_waimai",
    "微博": "com.sina.weibo",
    "Keep": "com.gotokeep.keep", "keep": "com.gotokeep.keep",
    "喜马拉雅": "com.ximalaya.ting.android",
    "知乎": "com.zhihu.android",
    "美图秀秀": "com.mt.mtxx.mtxx",
}


REAL_APP_MAP = {
    "豆瓣": "com.douban.frodo",
    "mDouban": "com.douban.frodo",
    "mdouban": "com.douban.frodo",
    "小红书": "com.xingin.xhs",
    "mXiaohongshu": "com.xingin.xhs",
    "mxiaohongshu": "com.xingin.xhs",
    "B站": "tv.danmaku.bili",
    "b站": "tv.danmaku.bili",
    "bilibili": "tv.danmaku.bili",
    "mBilibili": "tv.danmaku.bili",
    "mbilibili": "tv.danmaku.bili",
    "哔哩哔哩": "tv.danmaku.bili",
    "美团": "com.sankuai.meituan",
    "美团外卖": "com.sankuai.meituan.takeoutnew",
    "mMeituan": "com.sankuai.meituan.takeoutnew",
    "mmeituan_waimai": "com.sankuai.meituan.takeoutnew",
    "酷我音乐": "cn.kuwo.player",
    "什么值得买": "com.smzdm.client.android",
    "百度地图": "com.baidu.BaiduMap",
    "爱奇艺": "com.qiyi.video",
    "WPS": "cn.wps.moffice_eng",
    "扫描全能王": "com.intsig.camscanner",
    "安居客": "com.anjuke.android.app",
    "自如": "com.ziroom.ziroomcustomer",
    "贝壳找房": "com.lianjia.beike",
    "贝壳": "com.lianjia.beike",
    "得物": "com.shizhuang.duapp",
    "芒果TV": "com.hunantv.imgo.activity",
    "58同城": "com.wuba",
    "高德地图": "com.autonavi.minimap",
    "高德": "com.autonavi.minimap",
    "滴滴出行": "com.sdu.didi.psnger",
    "滴滴": "com.sdu.didi.psnger",
    "海底捞": "com.haidilao",
}


def _app_candidates(name: str) -> list[str]:
    candidates: list[str] = []
    for app_map in (REAL_APP_MAP, APP_MAP):
        pkg = app_map.get(name, app_map.get(name.lower(), ""))
        if pkg:
            candidates.append(pkg)
        for k, v in app_map.items():
            if name.lower() in k.lower():
                candidates.append(v)
                break
    return list(dict.fromkeys(candidates))


def _gui_get(gui_proxy_url: str, path: str, params: dict | None = None, timeout: float = 15.0) -> dict:
    url = f"{gui_proxy_url.rstrip('/')}{path}"
    if params:
        url = f"{url}?{parse.urlencode({k: v for k, v in params.items() if v is not None})}"
    req = request.Request(url, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _call_llm(api_url: str, api_key: str, model: str, messages: list[dict],
              temperature: float = 0.7, top_p: float = 0.9) -> dict:
    """Direct API call to the configured GUI model endpoint."""
    # Strip provider-specific fields
    clean = [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]
    body: dict[str, Any] = {
        "model": model, "messages": clean, "max_tokens": 4096,
        "stream": False, "temperature": temperature, "top_p": top_p,
    }
    if "autoglm" in model.lower():
        # AutoGLM: lower temperature + skip_special_tokens + larger max_tokens
        # Reference script uses max_tokens=16384; real app screenshots need long reasoning
        body["max_tokens"] = 16384
        body["temperature"] = 0.3
        body["extra_body"] = {"skip_special_tokens": False}
    else:
        body["reasoning_effort"] = "high"
    base_url = api_url.rstrip("/")
    api_format = _normalize_api_format(
        os.environ.get("PHONEHARNESS_OPENAI_API_FORMAT")
        or os.environ.get("OPENAI_API_FORMAT")
        or ("responses" if base_url.endswith("/responses") else "chat_completions")
    )
    if api_format == "responses":
        request_body = _chat_payload_to_responses_payload(body)
        if base_url.endswith("/responses"):
            chat_url = base_url
        elif base_url.endswith("/v1"):
            chat_url = f"{base_url}/responses"
        else:
            chat_url = f"{base_url}/v1/responses"
    elif base_url.endswith("/chat/completions"):
        request_body = body
        chat_url = base_url
    elif base_url.endswith("/v1"):
        request_body = body
        chat_url = f"{base_url}/chat/completions"
    else:
        request_body = body
        chat_url = f"{base_url}/v1/chat/completions"
    payload = json.dumps(request_body, ensure_ascii=False).encode("utf-8")

    raw = ""
    data: dict[str, Any] = {}
    max_retries = 5
    for attempt in range(max_retries):
        req = request.Request(
            chat_url,
            data=payload, method="POST",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=180) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            if api_format == "responses":
                data = _responses_payload_to_chat_payload(data)
            break
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            if exc.code not in _TRANSIENT_HTTP_CODES or attempt == max_retries - 1:
                raise ValueError(f"LLM call failed: HTTP {exc.code}: {raw}") from exc
        except (error.URLError, TimeoutError) as exc:
            if attempt == max_retries - 1:
                raise ValueError(f"LLM call failed after {max_retries} attempts: {exc}") from exc
        except json.JSONDecodeError as exc:
            if attempt == max_retries - 1:
                raise ValueError(f"LLM call returned invalid JSON after {max_retries} attempts: {raw}") from exc
        time.sleep(2 ** attempt)

    choice = data["choices"][0]["message"]
    reasoning = choice.get("reasoning_content") or ""
    content = choice.get("content") or ""  # AutoGLM may return None
    if not reasoning and "<think>" in content:
        if "</think>" in content:
            reasoning = content.split("</think>")[0].replace("<think>", "").strip()
            content = content.split("</think>")[-1].strip()
    usage = data.get("usage", {})
    return {
        "prediction": f"<think>{reasoning}</think>{content}" if reasoning else content,
        "reasoning": reasoning, "content": content,
        "raw_content": choice.get("content", ""),
        "tokens": {"prompt": usage.get("prompt_tokens", 0), "completion": usage.get("completion_tokens", 0)},
    }


# ── Main controller ──────────────────────────────────────────────────

# Direct controller fallback for standalone runs. Main phoneharness surfaces pass
# these explicitly and should not rely on implicit defaults.
DEFAULT_LLM_URL = os.environ.get("SEED_LLM_URL")
DEFAULT_LLM_KEY = os.environ.get("SEED_LLM_KEY")
DEFAULT_LLM_MODEL = os.environ.get("SEED_LLM_MODEL")


def run_seed_gui_turn(
    user_input: str,
    gui_proxy_url: str = "http://10.0.2.2:8919",
    max_steps: int = 30,
    *,
    gui_model: str | None = None,
    gui_api_url: str | None = None,
    gui_api_key: str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> bool:
    """Run one Seed-style GUI agent turn. Supports multiple model adapters."""
    from ..tools.gui.action_adapter import get_adapter
    emit = on_event or (lambda _: None)
    emit({"event": "start", "user_input": user_input})

    HISTORY_N = 3
    history_images: list[str] = []
    history_responses: list[str] = []

    api_url = gui_api_url or DEFAULT_LLM_URL
    api_key = gui_api_key or DEFAULT_LLM_KEY
    model = gui_model or DEFAULT_LLM_MODEL
    if not api_url or not api_key or not model:
        raise ValueError(
            "GUI backend is not fully configured. Pass gui_model/gui_api_url/gui_api_key "
            "explicitly, or set SEED_LLM_MODEL/SEED_LLM_URL/SEED_LLM_KEY for standalone runs."
        )

    # Select action parser based on model name
    _action_parser = get_adapter(model)

    # Model-specific prompt: Seed uses XML tool_call format, AutoGLM uses native do(action=...) format.
    _model_lower = model.lower()
    if "autoglm" in _model_lower:
        # AutoGLM is trained to emit Open-AutoGLM do(action=...) actions. The
        # adapter still accepts hy_sft XML as fallback, but this is the stable path.
        _sys_prompt = """你是一个智能体分析专家，可以根据操作历史和当前状态图执行一系列操作来完成任务。
你必须严格按照要求输出以下格式：
<think>{think}</think>
<answer>{action}</answer>

其中：
- {think} 是对你为什么选择这个操作的简短推理说明。
- {action} 是本次执行的具体操作指令，必须严格遵循下方定义的指令格式。

操作指令及其作用如下，坐标系统从左上角 (0,0) 到右下角 (999,999)：
- do(action="Launch", app="xxx")
- do(action="Tap", element=[x,y])
- do(action="Type", text="xxx")
- do(action="Type_Name", text="xxx")
- do(action="Swipe", start=[x1,y1], end=[x2,y2])
- do(action="Back")
- do(action="Home")
- do(action="Wait", duration="2 seconds")
- finish(message="xxx")

必须遵循的规则：
1. 每次响应只输出一个动作。
2. 在执行任何操作前，先检查当前 app 是否是目标 app；如果不是，先执行 Launch。
3. 如果页面正在加载或操作后还没有生效，执行 Wait；最多连续 Wait 三次。
4. 如果进入无关页面，执行 Back。
5. 在结束任务前必须检查任务是否完整完成；未完成时不要 finish。
6. 如果同一个 Tap/Swipe 连续两次后屏幕没有明显变化，禁止第三次重复同一动作；必须换点击位置、滑动、返回重试，或说明失败。
7. 不要输出工具列表外的动作，不要只输出自然语言。"""
        _tool_prompt = ""
    else:
        _sys_prompt = SEED_SYSTEM_PROMPT
        _tool_prompt = SEED_TOOL_PROMPT

    # Prepare screenshot save directory (best-effort, non-fatal)
    _screenshots_dir = None
    try:
        from pathlib import Path as _Path
        _termux_home = _Path("/data/data/com.termux/files/home")
        if not _termux_home.exists():
            _termux_home = _Path.home()
        _traces_root = _Path(os.environ.get("PHONEHARNESS_TRACES_DIR")
                             or _termux_home / "artifacts" / "seed_gui_traces")
        _ts = time.strftime("%Y%m%d_%H%M%S")
        _safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in user_input[:30])
        _screenshots_dir = _traces_root / f"{_ts}_{_safe}_screenshots"
        _screenshots_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    last_action_key: tuple[Any, ...] | None = None
    repeated_action_count = 0

    for step in range(1, max_steps + 1):
        step_t0 = time.time()

        # ── Phase 1: Screenshot ──
        ss_t0 = time.time()
        ss = _gui_get(gui_proxy_url, "/screenshot")
        ss_ms = int((time.time() - ss_t0) * 1000)
        if not ss.get("ok"):
            emit({"event": "error", "message": f"screenshot failed ({ss_ms}ms): {ss.get('error')}"})
            break
        img_b64 = ss["base64"]
        img_w = ss.get("width", 1080)
        img_h = ss.get("height", 2400)
        if img_w == 1080 and "width" not in ss:
            try:
                import struct
                png_data = base64.b64decode(img_b64)
                if png_data[1:4] == b'PNG':
                    img_w = struct.unpack('>I', png_data[16:20])[0]
                    img_h = struct.unpack('>I', png_data[20:24])[0]
            except Exception:
                pass

        # Save screenshot to disk (best-effort)
        if _screenshots_dir:
            try:
                (_screenshots_dir / f"step_{step:03d}.png").write_bytes(base64.b64decode(img_b64))
            except Exception:
                pass

        history_images.append(img_b64)

        # ── Phase 2: Build messages ──
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _sys_prompt},
        ]
        if _tool_prompt:
            messages.append({"role": "system", "content": _tool_prompt})
        if "autoglm" in _model_lower:
            # AutoGLM hy_sft_toolcall: single user turn with current screenshot + history text
            # Matches reference script build_messages() format exactly
            history_text = ""
            for i, resp in enumerate(history_responses):
                history_text += f"Step {i+1}: {resp}\n"
            user_text = f"### 指令\n{user_input}\n\n### 历史操作信息\n{history_text}"
            user_content: list[dict[str, Any]] = [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{history_images[-1]}"}},
                {"type": "text", "text": user_text},
            ]
            messages.append({"role": "user", "content": user_content})
        else:
            # Seed: separate user text + tool-role images (original format)
            messages.append({"role": "user", "content": user_input})
            for i in range(len(history_images)):
                messages.append({
                    "role": "tool",
                    "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{history_images[i]}"}}],
                    "tool_call_id": "1",
                })
                if i < len(history_responses):
                    resp = history_responses[i]
                    if "</think>" in resp:
                        c = resp.split("</think>")[-1]
                        r = resp.split("</think>")[0].replace("<think>", "")
                    else:
                        c, r = resp, ""
                    messages.append({"role": "assistant", "content": c, "reasoning_content": r})

        def _has_img(msg):
            c = msg.get("content")
            return isinstance(c, list) and any(item.get("type") == "image_url" for item in c)
        limited = []
        ic = 0
        for msg in reversed(messages):
            if _has_img(msg):
                ic += 1
                if ic > HISTORY_N:
                    continue
            limited.append(msg)
        messages = list(reversed(limited))

        # ── Phase 3: LLM call ──
        llm_t0 = time.time()
        try:
            result = _call_llm(api_url, api_key, model, messages)
        except Exception as exc:
            llm_ms = int((time.time() - llm_t0) * 1000)
            emit({"event": "error", "message": f"LLM failed ({llm_ms}ms): {exc}"})
            break
        llm_ms = int((time.time() - llm_t0) * 1000)
        history_responses.append(result["prediction"])

        # Parse action using model-specific adapter
        raw_content = result.get("raw_content", result["content"])
        parsed = _action_parser(raw_content)
        # Convert GUIAction dataclass to dict for backward compat
        action = {
            "action_type": parsed.action_type,
            "x": parsed.x, "y": parsed.y,
            "end_x": parsed.end_x, "end_y": parsed.end_y,
            "direction": parsed.direction,
            "text": parsed.text,
            "app_name": parsed.text if parsed.action_type == "open_app" else "",
            "raw": parsed.raw,
            "start_x": parsed.x, "start_y": parsed.y,
        }
        action_key = (
            action["action_type"], action["x"], action["y"], action["end_x"],
            action["end_y"], action["direction"], action["text"], action["app_name"],
        )
        if action_key == last_action_key:
            repeated_action_count += 1
        else:
            last_action_key = action_key
            repeated_action_count = 1

        # Emit step event WITH timing + model output + screenshot
        step_event = {"event": "step", "step": step,
              "timing_ms": {"screenshot": ss_ms, "llm": llm_ms},
              "tokens": result.get("tokens", {}),
              "reasoning": result.get("reasoning", "")[:2000],
              "raw_action": result.get("raw_content", result.get("content", ""))[:1000]}
        # Include screenshot base64 so host can decode without pulling from device
        if os.environ.get("SEED_GUI_EMBED_SCREENSHOTS", "1") == "1":
            step_event["screenshot_b64"] = img_b64
        emit(step_event)

        if (
            "autoglm" in _model_lower
            and action["action_type"] in {"click", "drag", "scroll", "wait"}
            and repeated_action_count >= 6
        ):
            emit({"event": "error", "message": (
                f"AutoGLM repeated identical {action['action_type']} action "
                f"{repeated_action_count} times; aborting GUI subtask"
            )})
            break

        emit({"event": "tool_call", "step": step, "name": f"seed_{action['action_type']}",
              "arguments": {k: v for k, v in action.items() if k != "raw"}})

        # ── Phase 4: Execute action via gui_proxy ──
        act_t0 = time.time()
        try:
            at = action["action_type"]
            if at == "finished":
                emit({"event": "assistant_text", "text": action.get("text", "")})
                emit({"event": "done", "success": True, "text": action.get("text", ""), "duration": 0})
                return True
            if at == "unknown":
                emit({"event": "error", "message": f"unknown action: {action.get('raw', '')[:100]}"})
                break

            act_summary = at
            if at == "click":
                px, py = int(action["x"] * img_w / 1000), int(action["y"] * img_h / 1000)
                _gui_get(gui_proxy_url, "/tap", {"x": px, "y": py})
                act_summary = f"tap({px},{py})"
            elif at == "scroll":
                cx, cy = int(action["x"] * img_w / 1000), int(action["y"] * img_h / 1000)
                offsets = {"down": (0, -400), "up": (0, 400), "left": (-400, 0), "right": (400, 0)}
                dx, dy = offsets.get(action["direction"], (0, -400))
                _gui_get(gui_proxy_url, "/swipe", {"x1": cx, "y1": cy, "x2": cx+dx, "y2": cy+dy, "duration": 300})
                act_summary = f"scroll {action['direction']}"
            elif at == "type":
                _gui_get(gui_proxy_url, "/type", {"text": action["text"]})
                act_summary = f"type '{action['text'][:30]}'"
            elif at == "drag":
                x1, y1 = int(action["start_x"]*img_w/1000), int(action["start_y"]*img_h/1000)
                x2, y2 = int(action["end_x"]*img_w/1000), int(action["end_y"]*img_h/1000)
                _gui_get(gui_proxy_url, "/swipe", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": 500})
                act_summary = f"drag ({x1},{y1})->({x2},{y2})"
            elif at == "press_home":
                _gui_get(gui_proxy_url, "/keyevent", {"key": "KEYCODE_HOME"})
                act_summary = "home"
            elif at == "press_back":
                _gui_get(gui_proxy_url, "/keyevent", {"key": "KEYCODE_BACK"})
                act_summary = "back"
            elif at == "open_app":
                name = action.get("app_name", "")
                launched_pkg = ""
                if name:
                    _gui_get(gui_proxy_url, "/keyevent", {"key": "KEYCODE_HOME"})
                    time.sleep(0.5)
                    for pkg in _app_candidates(name):
                        result = _gui_get(gui_proxy_url, "/launch", {"package": pkg})
                        if result.get("ok"):
                            launched_pkg = pkg
                            break
                act_summary = f"open {name} ({launched_pkg or 'unknown pkg'})"
                time.sleep(2)
            elif at == "wait":
                time.sleep(2)
                act_summary = "wait 2s"
            elif at == "call_user":
                act_summary = action.get("text", "")[:50]
                act_ms = int((time.time() - act_t0) * 1000)
                total_ms = int((time.time() - step_t0) * 1000)
                emit({"event": "tool_result", "step": step,
                      "name": "seed_call_user", "ok": True, "summary": act_summary,
                      "timing_ms": {"screenshot": ss_ms, "llm": llm_ms, "action": act_ms, "total": total_ms}})
                emit({"event": "assistant_text", "text": action.get("text", "")})
                emit({"event": "done", "success": False, "needs_user": True,
                      "text": action.get("text", ""), "duration": 0})
                return False

            act_ms = int((time.time() - act_t0) * 1000)
            total_ms = int((time.time() - step_t0) * 1000)
            emit({"event": "tool_result", "step": step,
                  "name": f"seed_{at}", "ok": True, "summary": act_summary,
                  "timing_ms": {"screenshot": ss_ms, "llm": llm_ms, "action": act_ms, "total": total_ms}})

        except Exception as exc:
            act_ms = int((time.time() - act_t0) * 1000)
            emit({"event": "error", "message": f"action error ({act_ms}ms): {exc}"})
            continue

        time.sleep(1)

    emit({"event": "done", "success": False, "text": "", "duration": 0})
    return False
