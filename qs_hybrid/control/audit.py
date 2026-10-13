"""
Append-only audit trail. JSON lines, each record carries the SHA-256 of the
previous record so any edit or deletion breaks the chain. Independent of the AI
service and of the telemetry DB.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any


class AuditLog:
    def __init__(self, path: str = "./data/audit.jsonl"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._prev = self._last_hash()

    def _last_hash(self) -> str:
        if not os.path.exists(self.path):
            return "0" * 64
        last = None
        with open(self.path) as f:
            for line in f:
                if line.strip():
                    last = line
        return json.loads(last)["hash"] if last else "0" * 64

    def record(self, kind: str, **fields: Any) -> dict:
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields, "prev": self._prev}
        body = json.dumps(rec, sort_keys=True, default=str)
        rec["hash"] = hashlib.sha256(body.encode()).hexdigest()
        with self._lock:
            with open(self.path, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
            self._prev = rec["hash"]
        return rec

    def verify(self) -> tuple[bool, int]:
        """Walk the chain. Returns (ok, records_checked)."""
        prev = "0" * 64
        n = 0
        if not os.path.exists(self.path):
            return True, 0
        with open(self.path) as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                h = rec.pop("hash")
                if rec.get("prev") != prev:
                    return False, n
                if hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest() != h:
                    return False, n
                prev, n = h, n + 1
        return True, n

    def tail(self, limit: int = 100) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path) as f:
            lines = [l for l in f if l.strip()]
        return [json.loads(l) for l in lines[-limit:]]
