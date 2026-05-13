"""gui_action — unified GUI action tool for flat mode.

Accepts model-native action format (Seed XML, JSON, etc.) and executes via gui_proxy.
Handles coordinate normalization (0-1000 → device pixels) internally.
"""
from __future__ import annotations

import time
from typing import Any

from ...agent.message import ToolResult
from ..base import BaseTool
from .proxy_client import GUIProxyClient
from .action_adapter import GUIAction, get_adapter

# App package mapping (same as seed_gui controller)
APP_MAP = {
    "豆瓣": "com.phoneuse.mdouban", "mdouban": "com.phoneuse.mdouban",
    "小红书": "com.phoneuse.mxiaohongshu", "mxiaohongshu": "com.phoneuse.mxiaohongshu",
    "美团": "com.phoneuse.mmeituan_waimai", "mmeituan_waimai": "com.phoneuse.mmeituan_waimai",
    "美团外卖": "com.phoneuse.mmeituan_waimai",
    "B站": "com.phoneuse.mbilibili", "mbilibili": "com.phoneuse.mbilibili",
    "bilibili": "com.phoneuse.mbilibili",
    "微博": "com.sina.weibo",
    "Keep": "com.gotokeep.keep", "keep": "com.gotokeep.keep",
    "喜马拉雅": "com.ximalaya.ting.android",
    "知乎": "com.zhihu.android",
    "美图秀秀": "com.mt.mtxx.mtxx",
    "Chrome": "com.android.chrome", "浏览器": "com.android.chrome",
    "设置": "com.android.settings", "Settings": "com.android.settings",
    "时钟": "com.google.android.deskclock", "Clock": "com.google.android.deskclock",
    "计算器": "com.google.android.calculator", "Calculator": "com.google.android.calculator",
}

REAL_APP_MAP = {
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
    for app_map in (REAL_APP_MAP, APP_MAP):
        pkg = app_map.get(name, app_map.get(name.lower(), ""))
        if pkg:
            candidates.append(pkg)
        for k, v in app_map.items():
            if name.lower() in k.lower():
                candidates.append(v)
                break
    return list(dict.fromkeys(candidates))

# Device resolution for coordinate scaling
DEVICE_W = 1080
DEVICE_H = 2400
NORM_BASE = 1000  # 0-1000 normalized coordinate space


class GUIActionTool(BaseTool):
    """Execute a GUI action using the model's native action format.

    In flat mode, models output GUI actions in their trained format
    (e.g. Seed XML). This tool parses and executes them with proper
    coordinate normalization.
    """

    name = "gui_action"
    description = (
        "Execute a GUI action on the Android screen. "
        "Output your action in your native format:\n"
        "- click: <function=click><parameter=point>x y</parameter></function>\n"
        "- scroll: <function=scroll><parameter=direction>down</parameter><parameter=point>500 500</parameter></function>\n"
        "- type: <function=type><parameter=content>text here</parameter></function>\n"
        "- press_back: <function=press_back></function>\n"
        "- press_home: <function=press_home></function>\n"
        "- open_app: <function=open_app><parameter=app_name>appname</parameter></function>\n"
        "- finished: <function=finished><parameter=content>summary</parameter></function>\n"
        "Coordinates are 0-1000 normalized (same as screenshot image space). "
        "(0,0)=top-left, (1000,~2222)=bottom-right."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "The GUI action in model-native format (XML or JSON).",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919",
                 model_name: str = "") -> None:
        self._client = GUIProxyClient(gui_proxy_url)
        self._adapter = get_adapter(model_name)
        self._model_name = model_name
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        raw_action = arguments.get("action", "")
        if not raw_action:
            return ToolResult(ok=False, output="action is required", summary="no action provided")

        # Parse using model-specific adapter
        action = self._adapter(raw_action)

        if action.action_type == "unknown":
            return ToolResult(
                ok=False,
                output=f"Could not parse action: {action.raw[:200]}",
                summary="gui_action parse failed",
            )

        if action.action_type == "finished":
            return ToolResult(
                ok=True,
                output=f"GUI task finished: {action.text}",
                summary=f"finished: {action.text[:80]}",
            )

        # Scale normalized coords (0-1000) to device pixels
        def to_pixel_x(nx: int) -> int:
            return int(nx * DEVICE_W / NORM_BASE)

        def to_pixel_y(ny: int) -> int:
            return int(ny * DEVICE_H / NORM_BASE)

        # Execute action via gui_proxy
        if action.action_type == "click":
            px, py = to_pixel_x(action.x), to_pixel_y(action.y)
            result = self._client.get("/tap", {"x": px, "y": py})
            ok = result.get("ok", False)
            return ToolResult(
                ok=ok,
                output=f"click({action.x},{action.y})→pixel({px},{py}): {'ok' if ok else 'failed'}",
                summary=f"click at ({action.x},{action.y})",
            )

        if action.action_type == "scroll":
            cx, cy = to_pixel_x(action.x), to_pixel_y(action.y)
            scroll_dist = 600  # pixels
            dx, dy = {
                "up": (0, -scroll_dist),
                "down": (0, scroll_dist),
                "left": (-scroll_dist, 0),
                "right": (scroll_dist, 0),
            }.get(action.direction, (0, scroll_dist))
            result = self._client.get("/swipe", {
                "x1": cx, "y1": cy,
                "x2": cx + dx, "y2": cy + dy,
                "duration": 300,
            })
            ok = result.get("ok", False)
            return ToolResult(
                ok=ok,
                output=f"scroll {action.direction} at ({action.x},{action.y}): {'ok' if ok else 'failed'}",
                summary=f"scroll {action.direction}",
            )

        if action.action_type == "type":
            result = self._client.get("/type", {"text": action.text})
            ok = result.get("ok", False)
            return ToolResult(
                ok=ok,
                output=f"type '{action.text}': {'ok' if ok else 'failed'}",
                summary=f"type {len(action.text)} chars",
            )

        if action.action_type == "drag":
            x1, y1 = to_pixel_x(action.x), to_pixel_y(action.y)
            x2, y2 = to_pixel_x(action.end_x), to_pixel_y(action.end_y)
            result = self._client.get("/swipe", {
                "x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": 500,
            })
            ok = result.get("ok", False)
            return ToolResult(
                ok=ok,
                output=f"drag ({action.x},{action.y})→({action.end_x},{action.end_y}): {'ok' if ok else 'failed'}",
                summary=f"drag",
            )

        if action.action_type == "press_home":
            result = self._client.get("/keyevent", {"key": "KEYCODE_HOME"})
            return ToolResult(ok=result.get("ok", False), output="press_home", summary="press_home")

        if action.action_type == "press_back":
            result = self._client.get("/keyevent", {"key": "KEYCODE_BACK"})
            return ToolResult(ok=result.get("ok", False), output="press_back", summary="press_back")

        if action.action_type == "wait":
            time.sleep(2)
            return ToolResult(ok=True, output="waited 2s", summary="wait")

        if action.action_type == "open_app":
            for pkg in _app_candidates(action.text):
                result = self._client.get("/launch", {"package": pkg})
                if result.get("ok"):
                    return ToolResult(ok=True, output=f"opened {action.text} ({pkg})", summary=f"open_app {action.text}")
            return ToolResult(ok=False, output=f"unknown app: {action.text}", summary="open_app failed")

        return ToolResult(
            ok=False,
            output=f"Unsupported action type: {action.action_type}",
            summary=f"unsupported: {action.action_type}",
        )
