"""GUI Action Adapter — bridges model-specific action formats to unified execution.

Each model has its own trained GUI action format:
  - Seed 2.0: XML  <function=click><parameter=point>500 300</parameter></function>
  - AutoGLM:  XML  <tool_call>click<tool_sep><arg_key>points</arg_key><arg_value>[[500,300]]</arg_value></tool_call>
  - Gemini/GPT: JSON tool_call (handled by outer tool_call API, not this adapter)

Adapter registry auto-selects parser by model name.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class GUIAction:
    """Unified GUI action representation."""
    action_type: str  # click, scroll, type, drag, press_home, press_back, wait, open_app, finished, call_user, unknown
    x: int = 0
    y: int = 0
    end_x: int = 0
    end_y: int = 0
    direction: str = ""  # up/down/left/right for scroll
    text: str = ""       # for type/finished/open_app
    raw: str = ""


# ── Seed 2.0 adapter ──────────────────────────────────────────────────

def parse_seed_action(response: str) -> GUIAction:
    """Parse Seed 2.0 XML action format.
    Format: <function=click><parameter=point>500 300</parameter></function>
    Coordinate system: 0-1000 normalized.
    """
    content = response.split("</think>")[-1] if "</think>" in response else response

    m = re.search(r'<function=finished>(.*?)</function>', content, re.DOTALL)
    if m:
        cm = re.search(r'<parameter=content>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        return GUIAction(action_type="finished", text=cm.group(1).strip() if cm else "")

    m = re.search(r'<function=click>(.*?)</function>', content, re.DOTALL)
    if m:
        pm = re.search(r'<parameter=point>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        if pm:
            point = re.sub(r'</?point>', '', pm.group(1)).strip()
            parts = re.split(r'[\s,]+', point)
            try:
                if len(parts) >= 2:
                    return GUIAction(action_type="click", x=int(float(parts[0])), y=int(float(parts[1])))
            except (ValueError, IndexError):
                pass

    m = re.search(r'<function=scroll>(.*?)</function>', content, re.DOTALL)
    if m:
        dm = re.search(r'<parameter=direction>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        pm = re.search(r'<parameter=point>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        direction = dm.group(1).strip() if dm else "down"
        x, y = 500, 500
        if pm:
            point = re.sub(r'</?point>', '', pm.group(1)).strip()
            parts = re.split(r'[\s,]+', point)
            try:
                x = int(float(parts[0]))
                y = int(float(parts[1])) if len(parts) >= 2 else 500
            except (ValueError, IndexError):
                pass
        return GUIAction(action_type="scroll", x=x, y=y, direction=direction)

    m = re.search(r'<function=type>(.*?)</function>', content, re.DOTALL)
    if m:
        cm = re.search(r'<parameter=content>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        return GUIAction(action_type="type", text=cm.group(1).strip() if cm else "")

    m = re.search(r'<function=drag>(.*?)</function>', content, re.DOTALL)
    if m:
        points = re.findall(r'<point>\s*([\d.]+)\s+([\d.]+)\s*</point>', m.group(1))
        if len(points) >= 2:
            try:
                return GUIAction(action_type="drag",
                                 x=int(float(points[0][0])), y=int(float(points[0][1])),
                                 end_x=int(float(points[1][0])), end_y=int(float(points[1][1])))
            except (ValueError, IndexError):
                pass

    if '<function=press_home>' in content:
        return GUIAction(action_type="press_home")
    if '<function=press_back>' in content:
        return GUIAction(action_type="press_back")
    if '<function=wait>' in content:
        return GUIAction(action_type="wait")

    m = re.search(r'<function=open_app>(.*?)</function>', content, re.DOTALL)
    if m:
        nm = re.search(r'<parameter=app_name>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        return GUIAction(action_type="open_app", text=nm.group(1).strip() if nm else "")

    m = re.search(r'<function=call_user>(.*?)</function>', content, re.DOTALL)
    if m:
        cm = re.search(r'<parameter=content>(.*?)(?:</parameter>|$)', m.group(1), re.DOTALL)
        return GUIAction(action_type="call_user", text=cm.group(1).strip() if cm else "")

    return GUIAction(action_type="unknown", raw=content[:500])


# ── AutoGLM adapter ───────────────────────────────────────────────────

def _parse_points(points_str: str) -> list[tuple[int, int]]:
    """Parse AutoGLM points like '[[500, 300]]' or '[[100,200],[300,400]]'."""
    import json as _json
    try:
        pts = _json.loads(points_str)
        if isinstance(pts, list):
            return [(int(p[0]), int(p[1])) for p in pts if isinstance(p, list) and len(p) >= 2]
    except Exception:
        pass
    # Fallback: extract number pairs
    pairs = re.findall(r'(\d+)\s*,\s*(\d+)', points_str)
    return [(int(x), int(y)) for x, y in pairs]


def parse_autoglm_action(response: str) -> GUIAction:
    """Parse AutoGLM / Open-AutoGLM action output.

    Primary format (Open-AutoGLM):
        <think>reasoning</think>
        <answer>do(action="Tap", element=[500, 300])</answer>
        or: finish(message="task done")

    Also supports:
        - <tool_call> XML format (hy_sft_toolcall variant)
        - Action: click=[x,y] text format
        - Loose coordinate patterns
    """
    # Strategy 0: Parse <answer> block (Open-AutoGLM do() format)
    answer_match = re.search(r'<answer>\s*(.*?)(?:</answer>|$)', response, re.DOTALL)
    if not answer_match:
        # Also try without <answer> tags — model might output do() directly
        answer_match = re.search(r'(do\s*\(action\s*=.*?\)|finish\s*\(.*?\))\s*$', response, re.MULTILINE)
    if answer_match:
        answer = answer_match.group(1).strip()

        # do(action="Tap", element=[x, y])
        tap_m = re.search(r'do\s*\(\s*action\s*=\s*"(?:Tap|tap)"\s*,\s*element\s*=\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]', answer)
        if tap_m:
            return GUIAction(action_type="click", x=int(tap_m.group(1)), y=int(tap_m.group(2)))

        # do(action="Long Press", element=[x, y])
        lp_m = re.search(r'do\s*\(\s*action\s*=\s*"Long Press"\s*,\s*element\s*=\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]', answer)
        if lp_m:
            return GUIAction(action_type="click", x=int(lp_m.group(1)), y=int(lp_m.group(2)))

        # do(action="Double Tap", element=[x, y])
        dt_m = re.search(r'do\s*\(\s*action\s*=\s*"Double Tap"\s*,\s*element\s*=\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]', answer)
        if dt_m:
            return GUIAction(action_type="click", x=int(dt_m.group(1)), y=int(dt_m.group(2)))

        # do(action="Swipe", start=[x1,y1], end=[x2,y2])
        sw_m = re.search(r'do\s*\(\s*action\s*=\s*"Swipe"\s*,\s*start\s*=\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]\s*,\s*end\s*=\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]', answer)
        if sw_m:
            return GUIAction(action_type="drag", x=int(sw_m.group(1)), y=int(sw_m.group(2)),
                             end_x=int(sw_m.group(3)), end_y=int(sw_m.group(4)))

        # do(action="Type", text="xxx")
        ty_m = re.search(r'do\s*\(\s*action\s*=\s*"(?:Type|Type_Name)"\s*,\s*text\s*=\s*"([^"]*)"', answer)
        if ty_m:
            return GUIAction(action_type="type", text=ty_m.group(1))

        # do(action="Launch", app="xxx")
        la_m = re.search(r'do\s*\(\s*action\s*=\s*"Launch"\s*,\s*app\s*=\s*"([^"]*)"', answer)
        if la_m:
            return GUIAction(action_type="open_app", text=la_m.group(1))

        # do(action="Back")
        if re.search(r'do\s*\(\s*action\s*=\s*"Back"', answer):
            return GUIAction(action_type="press_back")

        # do(action="Home")
        if re.search(r'do\s*\(\s*action\s*=\s*"Home"', answer):
            return GUIAction(action_type="press_home")

        # do(action="Wait", duration="x seconds")
        if re.search(r'do\s*\(\s*action\s*=\s*"Wait"', answer):
            return GUIAction(action_type="wait")

        # finish(message="xxx")
        fi_m = re.search(r'finish\s*\(\s*message\s*=\s*"([^"]*)"', answer)
        if fi_m:
            return GUIAction(action_type="finished", text=fi_m.group(1))

        # Fallback: any do() with element coordinates
        elem_m = re.search(r'element\s*=\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]', answer)
        if elem_m:
            return GUIAction(action_type="click", x=int(elem_m.group(1)), y=int(elem_m.group(2)))

    # Strategy 1: Parse XML <tool_call> block
    tc_match = re.search(r'<tool_call>(.*?)</tool_call>', response, re.DOTALL)
    if not tc_match:
        # Strategy 2: Parse "Action: xxx" format (AutoGLM simple format)
        action_line = re.search(r'Action:\s*(.*)', response)
        if action_line:
            atext = action_line.group(1).strip()
            # Action: click=[x, y]
            cm = re.search(r'click\s*=\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]', atext)
            if cm:
                return GUIAction(action_type="click", x=int(cm.group(1)), y=int(cm.group(2)))
            # Action: scroll=[[x1,y1],[x2,y2]]
            sm = re.findall(r'(\d+)\s*,\s*(\d+)', atext)
            if 'scroll' in atext.lower() and len(sm) >= 2:
                return GUIAction(action_type="drag", x=int(sm[0][0]), y=int(sm[0][1]),
                                 end_x=int(sm[1][0]), end_y=int(sm[1][1]))
            # Action: type=[text]
            tm = re.search(r'type\s*=\s*\[([^\]]+)\]', atext)
            if tm:
                return GUIAction(action_type="type", text=tm.group(1).strip())
            # Action: back / home
            if 'back' in atext.lower():
                return GUIAction(action_type="press_back")
            if 'home' in atext.lower():
                return GUIAction(action_type="press_home")
            # Action: finish=[text]
            fm = re.search(r'finish\s*=\s*\[([^\]]+)\]', atext)
            if fm:
                return GUIAction(action_type="finished", text=fm.group(1).strip())

        # Strategy 3: Parse do(action="Tap", element=[x,y]) format
        do_match = re.search(r'do\s*\(\s*action\s*=\s*"(\w+)"\s*,\s*element\s*=\s*\[(\d+)\s*,\s*(\d+)\]', response)
        if do_match:
            act = do_match.group(1).lower()
            x, y = int(do_match.group(2)), int(do_match.group(3))
            if act in ("tap", "click"):
                return GUIAction(action_type="click", x=x, y=y)

        # Strategy 4: Extract last x=N, y=N pair from reasoning text
        coords = re.findall(r'x\s*[=:]\s*(\d+)\s*[,，]\s*y\s*[=:]\s*(\d+)', response)
        if coords:
            x, y = int(coords[-1][0]), int(coords[-1][1])
            return GUIAction(action_type="click", x=x, y=y)

        # Strategy 4: Look for [[x, y]] or [x,y] patterns
        bracket_match = re.search(r'\[\s*(\d+)\s*,\s*(\d+)\s*\]', response)
        if bracket_match:
            x, y = int(bracket_match.group(1)), int(bracket_match.group(2))
            if x <= 1000 and y <= 2500:  # sanity check for coordinates
                return GUIAction(action_type="click", x=x, y=y)

        if '任务完成' in response or 'finish' in response.lower():
            return GUIAction(action_type="finished", text=response[:200])
        return GUIAction(action_type="unknown", raw=response[:500])

    block = tc_match.group(1)

    # Extract function name (before <tool_sep>)
    func_match = re.match(r'\s*(\w+)\s*<tool_sep>', block)
    func_name = func_match.group(1).strip() if func_match else ""

    # Extract all arg_key/arg_value pairs
    args: dict[str, str] = {}
    keys = re.findall(r'<arg_key>(.*?)</arg_key>', block)
    vals = re.findall(r'<arg_value>(.*?)</arg_value>', block, re.DOTALL)
    for k, v in zip(keys, vals):
        args[k.strip()] = v.strip()

    # Route by function name
    if func_name in ("click", "double_click", "long_press"):
        points = _parse_points(args.get("points", ""))
        if points:
            return GUIAction(action_type="click", x=points[0][0], y=points[0][1])

    if func_name in ("scroll", "drag"):
        points = _parse_points(args.get("points", ""))
        if points and len(points) >= 2:
            return GUIAction(action_type="drag",
                             x=points[0][0], y=points[0][1],
                             end_x=points[1][0], end_y=points[1][1])

    if func_name == "type":
        return GUIAction(action_type="type", text=args.get("text", ""))

    if func_name == "button_press":
        btn = args.get("type", "back")
        if btn == "home":
            return GUIAction(action_type="press_home")
        return GUIAction(action_type="press_back")

    if func_name == "open_app":
        return GUIAction(action_type="open_app", text=args.get("package", ""))

    if func_name == "close_app":
        return GUIAction(action_type="press_home")

    if func_name == "call_user":
        return GUIAction(action_type="call_user", text=args.get("text", ""))

    if func_name in ("finish", "output"):
        return GUIAction(action_type="finished", text=args.get("text", ""))

    if func_name == "wait":
        return GUIAction(action_type="wait")

    return GUIAction(action_type="unknown", raw=response[:500])


# ── JSON adapter (generic fallback) ───────────────────────────────────

def parse_json_action(response: str) -> GUIAction:
    """Parse JSON-style action (for models that output JSON naturally)."""
    import json
    try:
        data = json.loads(response.strip())
    except (json.JSONDecodeError, ValueError):
        return GUIAction(action_type="unknown", raw=response[:500])

    action = data.get("action", data.get("action_type", "unknown"))
    return GUIAction(
        action_type=action,
        x=int(data.get("x", 0)),
        y=int(data.get("y", 0)),
        end_x=int(data.get("end_x", 0)),
        end_y=int(data.get("end_y", 0)),
        direction=data.get("direction", ""),
        text=data.get("text", data.get("content", "")),
    )


# ── Adapter registry ──────────────────────────────────────────────────

ADAPTER_REGISTRY: dict[str, Any] = {
    "seed": parse_seed_action,
    "doubao": parse_seed_action,
    "autoglm": parse_autoglm_action,
    "default": parse_seed_action,
}


def get_adapter(model_name: str):
    """Get the right parser for a model name."""
    model_lower = model_name.lower()
    for key, adapter in ADAPTER_REGISTRY.items():
        if key in model_lower:
            return adapter
    return ADAPTER_REGISTRY["default"]
