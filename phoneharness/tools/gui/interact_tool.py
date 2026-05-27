"""gui_interact — text_xml GUI sub-loop for flat mode.

When flat_gui_protocol=text_xml, the outer loop (tool_call) delegates GUI
interaction to this tool. Internally it runs a text-completion sub-loop
using the model's native XML action protocol (e.g. Seed 2.0 XML).

This is like run_seed_gui_subtask but:
- Can use a dedicated GUI model/API supplied by the outer console config
- Keeps the GUI XML sub-loop separate from the outer tool-calling loop
- Shares screenshot compression + 0-1000 coord normalization
- Returns structured summary to outer loop
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.request
from typing import Any, Callable

from ...agent.message import ToolResult
from ...model.client import (
    _chat_payload_to_responses_payload,
    _normalize_api_format,
    _responses_payload_to_chat_payload,
)
from ..base import BaseTool
from .proxy_client import GUIProxyClient
from .action_adapter import parse_seed_action, parse_autoglm_action, get_adapter, GUIAction


# Same system prompt + tool definition as seed_gui controller
_GUI_SYSTEM_PROMPT = (
    "You are a GUI agent operating an Android phone. "
    "You observe the screen via screenshots and perform actions using function calls. "
    "Think step by step. If repeating the same action "
    "times results in a static screen with no changes, you should attempt a "
    "modified or alternative action."
)

_GUI_TOOL_PROMPT = """## Function Definition

Available actions (use XML format):
- click: <function=click><parameter=point>x y</parameter></function>
- scroll: <function=scroll><parameter=direction>up/down/left/right</parameter><parameter=point>x y</parameter></function>
- type: <function=type><parameter=content>text</parameter></function>
- press_back: <function=press_back></function>
- press_home: <function=press_home></function>
- open_app: <function=open_app><parameter=app_name>name</parameter></function>
- finished: <function=finished><parameter=content>summary of what was done</parameter></function>

## Important Notes
- Think first in <think>...</think> tags, then output one action.
- Coordinate system: 0-1000 relative. (0,0) is top-left, (1000,1000) is bottom-right.
- One action per turn. Observe the result before next action.
"""

# App package mapping
_APP_MAP = {
    "豆瓣": "com.phoneuse.mdouban", "小红书": "com.phoneuse.mxiaohongshu",
    "美团": "com.phoneuse.mmeituan_waimai", "美团外卖": "com.phoneuse.mmeituan_waimai",
    "B站": "com.phoneuse.mbilibili", "bilibili": "com.phoneuse.mbilibili",
    "微博": "com.sina.weibo",
    "Keep": "com.gotokeep.keep", "keep": "com.gotokeep.keep",
    "喜马拉雅": "com.ximalaya.ting.android",
    "知乎": "com.zhihu.android",
    "美图秀秀": "com.mt.mtxx.mtxx",
    "Chrome": "com.android.chrome", "设置": "com.android.settings",
}

_REAL_APP_MAP = {
    "豆瓣": "com.douban.frodo",
    "mDouban": "com.douban.frodo",
    "mdouban": "com.douban.frodo",
    "小红书": "com.xingin.xhs",
    "mXiaohongshu": "com.xingin.xhs",
    "mxiaohongshu": "com.xingin.xhs",
    "美团": "com.sankuai.meituan",
    "美团外卖": "com.sankuai.meituan.takeoutnew",
    "mMeituan": "com.sankuai.meituan.takeoutnew",
    "mmeituan_waimai": "com.sankuai.meituan.takeoutnew",
    "B站": "tv.danmaku.bili",
    "b站": "tv.danmaku.bili",
    "bilibili": "tv.danmaku.bili",
    "mBilibili": "tv.danmaku.bili",
    "mbilibili": "tv.danmaku.bili",
    "哔哩哔哩": "tv.danmaku.bili",
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
    for app_map in (_REAL_APP_MAP, _APP_MAP):
        pkg = app_map.get(name, app_map.get(name.lower(), ""))
        if pkg:
            candidates.append(pkg)
        for k, v in app_map.items():
            if name.lower() in k.lower():
                candidates.append(v)
                break
    return list(dict.fromkeys(candidates))

# Screenshot compression
_NORM_WIDTH = 1000
_JPEG_QUALITY = 50


def _compress_to_normalized(png_b64: str) -> tuple[str, int, int]:
    """Compress and resize screenshot to 1000px width."""
    try:
        from PIL import Image
        data = base64.b64decode(png_b64)
        img = Image.open(io.BytesIO(data))
        if img.width > _NORM_WIDTH:
            ratio = _NORM_WIDTH / img.width
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=_JPEG_QUALITY)
        return base64.b64encode(buf.getvalue()).decode("ascii"), img.width, img.height
    except Exception:
        return png_b64, 0, 0


def _call_llm_text(api_url: str, api_key: str, model: str,
                   messages: list[dict], max_tokens: int = 2048) -> str:
    """Call LLM in text completion mode (no tools param)."""
    body = {
        "model": model, "messages": messages, "max_tokens": max_tokens,
    }
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
    req = urllib.request.Request(
        chat_url,
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    if api_format == "responses":
        result = _responses_payload_to_chat_payload(result)
    return result["choices"][0]["message"]["content"]


class GUIInteractTool(BaseTool):
    """Run a text_xml GUI sub-loop for multi-step app interaction.

    The outer tool_call loop calls this when GUI interaction is needed.
    Internally uses text completion + XML action parsing.
    """

    name = "gui_interact"
    description = (
        "Interact with an Android app's GUI using multi-step visual navigation. "
        "Describe what you want to achieve, and this tool will handle the "
        "screenshot→observe→act loop using the device screen. "
        "Use this for tasks like: searching in an app, navigating menus, "
        "tapping buttons, filling forms, scrolling through content. "
        "Returns a text summary of what was accomplished."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "What to accomplish in the GUI. Be specific about "
                               "what app, what to search, what to tap, what result to find.",
            },
            "max_steps": {
                "type": "integer",
                "description": "Max GUI steps (default 20).",
                "default": 20,
            },
        },
        "required": ["goal"],
    }

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919",
                 model: str = "", api_url: str = "", api_key: str = "",
                 on_event: Callable[[dict], None] | None = None) -> None:
        self._gui = GUIProxyClient(gui_proxy_url)
        self._model = model
        self._api_url = api_url
        self._api_key = api_key
        self._action_parser = get_adapter(model)  # auto-select parser by model name
        self._on_event = on_event or (lambda _: None)
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        goal = arguments.get("goal", "")
        max_steps = arguments.get("max_steps", 20)
        if not goal:
            return ToolResult(ok=False, output="goal is required", summary="no goal")

        # Build initial messages for text completion sub-loop
        system = f"{_GUI_SYSTEM_PROMPT}\n\n{_GUI_TOOL_PROMPT}\n\nYour task: {goal}"
        messages: list[dict] = [{"role": "system", "content": system}]

        # History for context
        history_n = 3
        action_log: list[str] = []
        final_text = ""
        success = False
        device_w, device_h = 1080, 2400

        for step in range(1, max_steps + 1):
            # 1. Take screenshot
            ss_result = self._gui.get("/screenshot")
            if not ss_result.get("ok"):
                action_log.append(f"step {step}: screenshot failed")
                break

            b64 = ss_result.get("base64", "")
            compressed, img_w, img_h = _compress_to_normalized(b64)

            # 2. Build user message with screenshot
            user_content: list[dict] = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{compressed}"}},
                {"type": "text", "text": f"Screenshot ({img_w}x{img_h}). What action to take next?"},
            ]
            messages.append({"role": "user", "content": user_content})

            # Keep only last N screenshots to manage context
            img_count = sum(1 for m in messages if isinstance(m.get("content"), list) and
                           any(p.get("type") == "image_url" for p in m["content"] if isinstance(p, dict)))
            while img_count > history_n:
                for i, m in enumerate(messages):
                    if isinstance(m.get("content"), list) and any(
                        isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"]
                    ):
                        # Replace image with text note
                        messages[i] = {"role": "user", "content": "[earlier screenshot removed]"}
                        break
                img_count -= 1

            # 3. Call LLM (text completion, no tools)
            try:
                response = _call_llm_text(self._api_url, self._api_key, self._model,
                                          messages, max_tokens=2048)
            except Exception as e:
                action_log.append(f"step {step}: LLM error: {e}")
                break

            messages.append({"role": "assistant", "content": response})

            # Emit event for tracing
            self._on_event({"event": "gui_interact_step", "step": step, "response": response[:200]})

            # 4. Parse action using model-specific adapter
            action = self._action_parser(response)
            action_log.append(f"step {step}: {action.action_type}")

            # 5. Execute action
            if action.action_type == "finished":
                final_text = action.text
                success = True
                break

            if action.action_type == "click":
                px = int(action.x * device_w / 1000)
                py = int(action.y * device_h / 1000)
                self._gui.get("/tap", {"x": px, "y": py})

            elif action.action_type == "scroll":
                cx = int(action.x * device_w / 1000)
                cy = int(action.y * device_h / 1000)
                dist = 600
                dx, dy = {"up": (0, -dist), "down": (0, dist),
                          "left": (-dist, 0), "right": (dist, 0)}.get(action.direction, (0, dist))
                self._gui.get("/swipe", {"x1": cx, "y1": cy, "x2": cx+dx, "y2": cy+dy, "duration": 300})

            elif action.action_type == "type":
                self._gui.get("/type", {"text": action.text})

            elif action.action_type == "press_back":
                self._gui.get("/keyevent", {"key": "KEYCODE_BACK"})

            elif action.action_type == "press_home":
                self._gui.get("/keyevent", {"key": "KEYCODE_HOME"})

            elif action.action_type == "open_app":
                launched_pkg = ""
                for pkg in _app_candidates(action.text):
                    result = self._gui.get("/launch", {"package": pkg})
                    if result.get("ok"):
                        launched_pkg = pkg
                        break
                action_log[-1] = f"step {step}: open_app -> open {action.text} ({launched_pkg or 'unknown pkg'})"

            elif action.action_type == "wait":
                time.sleep(2)

            time.sleep(0.5)  # Brief pause between actions

        output = (
            f"GUI interaction {'completed' if success else 'ended'} ({len(action_log)} steps).\n"
            f"Actions: {', '.join(action_log)}\n"
            f"Result: {final_text[:500] if final_text else '(no explicit finish)'}"
        )
        return ToolResult(
            ok=success,
            output=output,
            summary=f"gui_interact {'ok' if success else 'incomplete'} ({len(action_log)} steps)",
        )
