"""
One click, the whole measuring session, with the care a person would take, and then it keeps going.

    python -m plantcollector.cli --config config/site.plant.yaml --data data session --hours 2

Steps, logged to data/session_<date>.log and printed:
  1. probe: who answers on Modbus TCP (one read of one register per controller)
  2. check: one poll of every device through the collector, every point printed, values to compare with the screens
  3. trace, master only, for the first phase (default 30 min): the master's comm-fail rate is measured without us
     touching the SAU at all
  4. trace, master every 1 s and the SAU every 5 s, for the rest of the session; every 10 min the comm-fail rate
     is compared with phase 3: if it has clearly risen (more than double and at least 3 fails in the window) the SAU
     is dropped from the trace again and the fact is logged - we must not be the ones making it worse
  5. at the end of the session: a report (data/trace/report_<date>.md) and a bundle
     (bundles/plant_<date>_<HHMM>.zip with the trace files, the report, the probe and check output, the log)
  6. then the long run: master every 10 s, SAU every 30 s, until the window is closed; every day at --zip-hour
     the previous day's files are reported and bundled the same way

Everything is read-only; the trace, the probe and the collector have no write function.
"""

from __future__ import annotations

import io
import os
import threading
import time
import zipfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

from .registry import load_registry
from .report import fails_from_events, summarize, _read
from .trace import Trace, probe


class Session:
    def __init__(self, config: str, data_dir: str, hours: float = 2.0, phase1_min: float = 30.0, sau_s: float = 5.0,
                 long_run: bool = True, zip_hour: int = 7, bundles_dir: str | None = None, log=print, clock=None):
        self.config, self.data_dir = config, data_dir
        self.hours, self.phase1_min, self.sau_s = hours, phase1_min, sau_s
        self.long_run, self.zip_hour = long_run, zip_hour
        self.trace_dir = os.path.join(data_dir, "trace")
        self.bundles_dir = bundles_dir or os.path.join(os.path.dirname(os.path.abspath(data_dir)), "bundles")
        os.makedirs(self.trace_dir, exist_ok=True)
        os.makedirs(self.bundles_dir, exist_ok=True)
        self.registry = load_registry(config)
        self._print = log
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.log_path = os.path.join(data_dir, f"session_{self.clock().strftime('%Y-%m-%d')}.log")
        self.notes: list[str] = []
        self.sau_dropped = False
        self.stop = threading.Event()

    # ---- helpers -----------------------------------------------------------------------------------
    def log(self, text: str) -> None:
        line = f"{self.clock().strftime('%H:%M:%S')} {text}"
        self.notes.append(line)
        self._print(line)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _capture(self, fn, *a, **k) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(*a, **k)
        return buf.getvalue()

    def fail_count(self, since: datetime, until: datetime | None = None) -> int:
        date = since.strftime("%Y-%m-%d")
        ev = _read(os.path.join(self.trace_dir, f"events_{date}.csv"))
        fails = fails_from_events(ev)
        until = until or self.clock()
        return sum(1 for f in fails if since <= f["start"] <= until)

    # ---- steps ---------------------------------------------------------------------------------------
    def step_probe(self) -> str:
        targets = []
        for d in self.registry.devices.values():
            c = d.connection
            if d.adapter == "modbus_tcp" and "host" in c:
                t = (c["host"], int(c.get("port", 502)), int(c.get("unit_id", 1)))
                if t not in targets:
                    targets.append(t)
        for h in (self.registry.extra.get("probe") or {}).get("hosts", []) or []:
            if (str(h), 502, 1) not in targets:
                targets.append((str(h), 502, 1))
        rows = probe(targets, timeout=3.0)
        text = "\n".join(f"{r['host']}:{r['port']} unit {r['unit']}: tcp {r['tcp']}, {r['modbus']}" for r in rows)
        answering = sum(1 for r in rows if r["modbus"].startswith("answers"))
        self.log(f"probe: {answering} of {len(rows)} targets answer on Modbus")
        return text

    def step_check(self) -> str:
        from .cli import cmd_check

        class A:
            pass
        a = A()
        a.config, a.data = self.config, self.data_dir
        text = self._capture(cmd_check, a)
        last = [ln for ln in text.splitlines() if "good" in ln][-1:] or ["?"]
        self.log(f"check: {last[0].strip()}")
        return text

    def _trace(self, every: dict, seconds: float, label: str) -> Trace:
        tr = Trace(self.registry, self.trace_dir, every, connect_test_s=60.0, log=lambda m: None)
        self.log(f"{label}: " + ", ".join(f"{d} every {s:g} s" for d, s in every.items()) + f", {seconds / 60:.0f} min")
        tr.run(duration_s=seconds, stop=self.stop, status_every_s=1e9)
        self.log(f"{label} done: " + ", ".join(f"{d} {tr.rows[d]} rows, {tr.failed[d]} unanswered" for d in every) + f", {tr.events_logged} events")
        return tr

    def step_trace(self) -> None:
        total = self.hours * 3600
        phase1 = min(self.phase1_min * 60, total)
        t0 = self.clock()
        self._trace({"neo-01": 1.0}, phase1, "phase 1, master only")
        base = self.fail_count(t0)
        base_rate = base / max(phase1 / 600, 1e-9)                   # fails per 10 minutes
        self.log(f"phase 1: {base} comm fails in {phase1 / 60:.0f} min without the SAU polled ({base_rate:.2f} per 10 min)")
        remaining = total - phase1
        every = {"neo-01": 1.0, "pv-array": self.sau_s}
        while remaining > 0 and not self.stop.is_set():
            chunk = min(600.0, remaining)
            t1 = self.clock()
            self._trace(every, chunk, "phase 2" if "pv-array" in every else "phase 2, master only")
            if "pv-array" in every:
                n = self.fail_count(t1)
                self.log(f"phase 2 window: {n} comm fails in {chunk / 60:.0f} min with the SAU polled every {self.sau_s:g} s")
                if n >= 3 and n > 2 * base_rate * (chunk / 600) + 1:
                    every = {"neo-01": 1.0}
                    self.sau_dropped = True
                    self.log("the comm-fail rate rose while we polled the SAU: SAU polling dropped for the rest of the session. Tell the engineer; this itself is a finding.")
            remaining -= chunk

    def step_report_and_bundle(self, date: str | None = None, extra_texts: dict | None = None) -> str:
        date = date or self.clock().strftime("%Y-%m-%d")
        report = summarize(self.trace_dir, date)
        if self.notes:
            report += "\n## Session log\n\n" + "\n".join(self.notes) + "\n"
        rp = os.path.join(self.trace_dir, f"report_{date}.md")
        with open(rp, "w", encoding="utf-8") as f:
            f.write(report)
        stamp = self.clock().strftime("%Y-%m-%d_%H%M")
        zp = os.path.join(self.bundles_dir, f"plant_{date}_{stamp[-4:]}.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(os.listdir(self.trace_dir)):
                if date in name:
                    z.write(os.path.join(self.trace_dir, name), f"trace/{name}")
            for name, text in (extra_texts or {}).items():
                z.writestr(name, text)
            if os.path.exists(self.log_path):
                z.write(self.log_path, os.path.basename(self.log_path))
        self.log(f"bundle: {zp} ({os.path.getsize(zp) / 1e6:.1f} MB), report: {rp}")
        return zp

    # ---- the whole thing --------------------------------------------------------------------------------
    def run(self) -> str:
        self.log(f"session start: {self.hours:g} h, phase 1 {self.phase1_min:g} min master only, then SAU every {self.sau_s:g} s")
        extra = {}
        try:
            extra["probe.txt"] = self.step_probe()
        except Exception as exc:
            self.log(f"probe failed: {exc}")
        try:
            extra["check.txt"] = self.step_check()
        except Exception as exc:
            self.log(f"check failed: {exc}")
        self.step_trace()
        zp = self.step_report_and_bundle(extra_texts=extra)
        self.log("session done. Copy the bundle to the stick or send it; the files stay here too.")
        if self.long_run and not self.stop.is_set():
            self.long()
        return zp

    def long(self) -> None:
        """After the session: a lighter trace that keeps going, with a report and a bundle every day at zip_hour."""
        every = {"neo-01": 10.0} if self.sau_dropped else {"neo-01": 10.0, "pv-array": 30.0}
        self.log("long run: " + ", ".join(f"{d} every {s:g} s" for d, s in every.items()) + f"; a bundle every day at {self.zip_hour:02d}:00 UTC. Close the window to stop.")
        while not self.stop.is_set():
            now = self.clock()
            nxt = now.replace(hour=self.zip_hour, minute=0, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            tr = Trace(self.registry, self.trace_dir, every, connect_test_s=300.0, log=lambda m: None)
            tr.run(duration_s=(nxt - now).total_seconds(), stop=self.stop, status_every_s=1e9)
            if self.stop.is_set():
                break
            yesterday = (self.clock() - timedelta(hours=self.zip_hour + 1)).strftime("%Y-%m-%d")
            self.notes = []
            self.step_report_and_bundle(date=yesterday)
