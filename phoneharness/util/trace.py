from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..agent.message import PathLayer, PathRef


@dataclass(frozen=True)
class ArtifactPaths:
    run_dir: PathRef
    trace: PathRef


class ArtifactStore:
    def __init__(
        self,
        root_dir: str | Path,
        *,
        run_prefix: str = "m0a",
        path_layer: PathLayer = "host",
    ) -> None:
        root = Path(root_dir).expanduser().resolve()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = root / f"{run_prefix}-{stamp}-{uuid4().hex[:8]}"
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.trace_path = self.run_dir / "trace.jsonl"
        self.path_layer = path_layer

    def paths(self) -> ArtifactPaths:
        return ArtifactPaths(
            run_dir=PathRef(layer=self.path_layer, path=str(self.run_dir)),
            trace=PathRef(layer=self.path_layer, path=str(self.trace_path)),
        )

    def write_json(self, filename: str, payload: Any) -> PathRef:
        path = self.run_dir / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return PathRef(layer=self.path_layer, path=str(path))

    def write_text(self, filename: str, content: str) -> PathRef:
        path = self.run_dir / filename
        path.write_text(content, encoding="utf-8")
        return PathRef(layer=self.path_layer, path=str(path))

    def append_trace(self, event: str, payload: dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **payload,
        }
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
