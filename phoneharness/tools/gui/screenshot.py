from __future__ import annotations

import base64
import io
from typing import Any

from ...agent.message import ToolResult
from ..base import BaseTool
from .proxy_client import GUIProxyClient

# Image compression for vision model (balance quality vs token cost)
_JPEG_QUALITY = 50

# In flat mode, resize to 1000px width so model outputs 0-1000 normalized coords
# (same coordinate space as seed_gui controller)
_FLAT_MODE_WIDTH = 1000
_DELEGATED_MODE_WIDTH = 900


def _compress_screenshot(png_b64: str, target_width: int = 900) -> tuple[str, str, int, int]:
    """Compress PNG screenshot to smaller JPEG for vision model.
    Returns (b64, mime_type, resized_width, resized_height).
    """
    try:
        from PIL import Image
        png_data = base64.b64decode(png_b64)
        img = Image.open(io.BytesIO(png_data))
        orig_w, orig_h = img.width, img.height
        # Resize to target width
        if img.width > target_width:
            ratio = target_width / img.width
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
        # Convert to JPEG
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=_JPEG_QUALITY)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg", img.width, img.height
    except ImportError:
        return png_b64, "image/png", 0, 0
    except Exception:
        return png_b64, "image/png", 0, 0


class ScreenshotTool(BaseTool):
    """Take a screenshot of the current Android screen."""

    name = "gui_screenshot"
    description = (
        "Take a screenshot of the current device screen. "
        "Returns the screenshot image so you can see the current UI state. "
        "Use this to observe the screen before deciding what GUI action to take."
    )
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919",
                 normalized_coords: bool = False) -> None:
        self._client = GUIProxyClient(gui_proxy_url)
        self._normalized = normalized_coords  # True in flat mode: resize to 1000px width
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        result = self._client.get("/screenshot")
        if not result.get("ok"):
            self.set_error_type("backend_error")
            return ToolResult(
                ok=False,
                output=f"Screenshot failed: {result.get('error', 'unknown')}",
                summary="gui_screenshot failed",
                checks={"screenshot_captured": False},
            )

        size = result.get("size_bytes", 0)
        b64 = result.get("base64", "")
        target_width = _FLAT_MODE_WIDTH if self._normalized else _DELEGATED_MODE_WIDTH
        compressed, image_mime_type, img_w, img_h = _compress_screenshot(b64, target_width) if b64 else (None, None, 0, 0)

        coord_note = (
            f"Image resized to {img_w}x{img_h}. "
            f"Coordinate system: 0-{img_w} horizontal, 0-{img_h} vertical. "
            f"(0,0) is top-left."
        ) if self._normalized and img_w else ""

        return ToolResult(
            ok=True,
            output=f"Screenshot captured ({size} bytes). {coord_note}Image is attached for your inspection.",
            summary=f"gui_screenshot succeeded, {size} bytes",
            checks={"screenshot_captured": True},
            image_base64=compressed,
            image_mime_type=image_mime_type,
        )
