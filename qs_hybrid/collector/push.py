"""
Site collector: runs ON SITE (SCADA PC or a mini PC), reads the devices through the
normal adapters, buffers locally, and pushes batches to the VPS over HTTPS.

  - outbound only: the plant opens nothing inbound
  - store-and-forward: if the VPS or internet is down, readings queue in a local
    SQLite file and are sent when the link returns, oldest first
  - gentle: one block read per device per interval; interval >= 10 s
  - read-only: this process never imports a write path

Run:  python -m qs_hybrid.collector.push --config config/site.yaml --vps https://vps.example/api/v1/ingest --key <API_KEY>
Env:  QS_VPS_URL, QS_VPS_KEY can replace the flags.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import requests
import yaml

from ..adapters import build_adapter
from ..adapters.simulator import SimPlant
from ..registry.loader import load_registry


class Spool:
    """Local queue of batches not yet acknowledged by the VPS."""

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS spool (id INTEGER PRIMARY KEY, ts TEXT, body TEXT)")
        self.db.commit()

    def put(self, batch: dict) -> None:
        self.db.execute("INSERT INTO spool (ts, body) VALUES (?, ?)", (batch["ts"], json.dumps(batch)))
        self.db.commit()

    def oldest(self, n: int = 50) -> list[tuple[int, dict]]:
        return [(i, json.loads(b)) for i, b in self.db.execute("SELECT id, body FROM spool ORDER BY id LIMIT ?", (n,))]

    def ack(self, ids: list[int]) -> None:
        self.db.executemany("DELETE FROM spool WHERE id=?", [(i,) for i in ids])
        self.db.commit()

    def size(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM spool").fetchone()[0]


def read_all(adapters) -> dict:
    """One batch: every device, every point, with quality — nothing is hidden."""
    now = datetime.now(timezone.utc).isoformat()
    devices = {}
    for did, ad in adapters.items():
        rows = ad.read()
        devices[did] = {
            pid: {"v": r.value, "q": r.quality.value, "ts": r.source_ts.isoformat(), **({"e": r.error} if r.error else {})}
            for pid, r in rows.items()
        }
    return {"ts": now, "devices": devices}


def push(spool: Spool, url: str, key: str, timeout: float = 15.0) -> int:
    sent = 0
    while True:
        items = spool.oldest()
        if not items:
            return sent
        ids = [i for i, _ in items]
        try:
            r = requests.post(url, json={"batches": [b for _, b in items]},
                              headers={"X-API-Key": key}, timeout=timeout)
            if r.status_code == 200:
                spool.ack(ids)
                sent += len(ids)
                continue
            print(f"vps responded {r.status_code}: {r.text[:200]}")
        except requests.RequestException as exc:
            print(f"push failed: {exc}")
        return sent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/site.yaml")
    ap.add_argument("--vps", default=os.environ.get("QS_VPS_URL", ""))
    ap.add_argument("--key", default=os.environ.get("QS_VPS_KEY", ""))
    ap.add_argument("--interval", type=float, default=30.0, help="seconds between reads (min 10)")
    ap.add_argument("--spool", default="./data/spool.sqlite")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    if not args.vps or not args.key:
        print("need --vps and --key (or QS_VPS_URL / QS_VPS_KEY)")
        sys.exit(1)
    interval = max(10.0, args.interval)

    reg = load_registry(args.config)
    cfg = yaml.safe_load(open(args.config))
    plant = None
    if any(d.adapter == "simulator" for d in reg.devices.values()):
        plant = SimPlant(reg.devices, cfg.get("site_load", {}).get("profile_kw") or [300] * 24)
    adapters = {d.id: build_adapter(d, plant=plant) for d in reg.devices.values()}
    for a in adapters.values():
        a.connect()
    spool = Spool(args.spool)
    print(f"collector: {len(adapters)} devices, every {interval:.0f}s -> {args.vps}  (spool {spool.size()} pending)")

    while True:
        t0 = time.time()
        try:
            batch = read_all(adapters)
            spool.put(batch)
            if plant is not None:
                plant.step(interval)
        except Exception as exc:
            print(f"read error: {exc}")
        n = push(spool, args.vps, args.key)
        print(f"{datetime.now().strftime('%H:%M:%S')} read ok, sent {n}, pending {spool.size()}")
        if args.once:
            return
        time.sleep(max(0.0, interval - (time.time() - t0)))


if __name__ == "__main__":
    main()
