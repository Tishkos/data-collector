"""
One click, the whole measuring session, with the care a person would take, and then it keeps going.

    python -m plantcollector.cli --config config/site.plant.yaml --data data session --hours 2

Steps, logged to data/session_<date>.log and printed:
  1. probe: who answers on Modbus TCP (one read of one register per controller)
  2. check: one poll of every device through the collector, every point printed, values to compare with the screens
  3. trace, master only, for the first phase (default 30 min): the master's comm-fail rate is measured without us
     touching the SAU at all
  4. trace, master every 1 s and the SAU every 5 s, plus the stall catcher (a ping of the SAU and of the master
     every second, and one small read of the SAU on the connection the trace already holds every second), for
     the rest of the session. The guard: every 10 min the comm-fail count is checked; three or more fails in a
     window pause our reads of the SAU (the pings go on, the master is read as before) for one window - if the
     fails go on without us, we were not the cause and the reads resume; if they stop, the reads stay paused and
     the fact is logged. Nothing reconnects: one trace, one connection, for the whole phase
  5. at the end of the session: a report (data/trace/report_<date>.md) and a bundle
     (bundles/plant_<date>_<HHMM>.zip with the trace files, the report, the probe and check output, the log)
  6. then the long run: master every 1 s (it costs the SAU nothing and gives the fail lengths), SAU every 30 s,
     the watch read every 3 s, pings every 1 s, the same guard, until the window is closed; every day at
     --zip-hour (UTC, default 02:00 = 05:00 plant time) the previous day's files are reported and bundled

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
                 long_run: bool = True, zip_hour: int = 2, bundles_dir: str | None = None, log=print, clock=None,
                 watch_s: float = 1.0, guard_window_s: float = 600.0):
        self.config, self.data_dir = config, data_dir
        self.hours, self.phase1_min, self.sau_s = hours, phase1_min, sau_s
        self.watch_s = watch_s
        self.guard_window_s = guard_window_s
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
        until = until or self.clock()
        ev = []
        for d in sorted({since.strftime("%Y-%m-%d"), until.strftime("%Y-%m-%d")}):   # a window across UTC midnight
            ev += _read(os.path.join(self.trace_dir, f"events_{d}.csv"))
        fails = fails_from_events(ev)
        return sum(1 for f in fails if since <= f["start"] <= until)

    # ---- steps ---------------------------------------------------------------------------------------
    def step_probe(self) -> str:
        targets = []
        seen_hosts = set()
        for d in self.registry.devices.values():            # one probe per host:port (the ten inverter unit ids
            c = d.connection                                 # behind the SAU would be eleven connections for nothing)
            if d.adapter == "modbus_tcp" and "host" in c:
                key = (c["host"], int(c.get("port", 502)))
                if key in seen_hosts:
                    continue
                seen_hosts.add(key)
                targets.append((c["host"], key[1], int(c.get("unit_id", 1))))
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

    def _trace(self, every: dict, seconds: float, label: str, connect_test_s: float = 300.0,
               watch_s: float | None = None) -> Trace:
        """One trace for the whole phase, in a background thread, with the guard checked every window."""
        watch_s = self.watch_s if watch_s is None else watch_s
        watch = {"pv-array": watch_s} if watch_s and "pv-array" in every else {}
        tr = Trace(self.registry, self.trace_dir, every, connect_test_s=connect_test_s, log=self.log,
                   watch=watch, ping_s=1.0 if watch or len(every) > 1 else None)
        self.log(f"{label}: " + ", ".join(f"{d} every {s:g} s" for d, s in every.items())
                 + (f", watch read of pv-array every {watch_s:g} s, pings every 1 s" if watch else "") + f", {seconds / 60:.0f} min")
        th = threading.Thread(target=tr.run, kwargs=dict(duration_s=seconds, stop=self.stop, status_every_s=1e9), daemon=True)
        t_start = self.clock()
        th.start()
        if "pv-array" in every:
            self._guard(tr, seconds, t_start)
        th.join()
        self.log(f"{label} done: " + ", ".join(f"{d} {tr.rows[d]} rows, {tr.failed[d]} unanswered" for d in every) + f", {tr.events_logged} events"
                 + (f", watch {tr.watch_rows} rows, {tr.stalls} stalls" if watch else ""))
        return tr

    def _guard(self, tr: Trace, seconds: float, t_start) -> None:
        """Every window: 3 or more comm fails -> pause our SAU reads for one window and see whether the fails go
        on without us. They do: resume (we were not the cause). They stop: stay paused, say so."""
        window = min(self.guard_window_s, max(seconds / 2, 1.0))
        deadline = time.monotonic() + seconds
        t_win = t_start
        while time.monotonic() < deadline - 1 and not self.stop.is_set():
            if not self.stop.wait(min(window, max(deadline - time.monotonic(), 0.1))):
                pass
            if time.monotonic() >= deadline or self.stop.is_set():
                break
            now = self.clock()
            n = self.fail_count(t_win, now)
            if tr.paused("pv-array"):
                if n >= 2:
                    tr.resume("pv-array")
                    self.log(f"guard: {n} comm fails while our SAU reads were paused - the fails do not need us; SAU reads resumed")
                else:
                    self.log(f"guard: {n} comm fails while paused - the burst stopped without us; SAU reads stay paused (pings and the master go on). Tell the engineer.")
                    self.sau_dropped = True
            elif n >= 3:
                tr.pause("pv-array")
                self.sau_dropped = True
                self.log(f"guard: {n} comm fails in {window / 60:.0f} min while we read the SAU: SAU reads paused for one window to see whether the fails go on without us (pings and the master continue)")
            else:
                self.log(f"guard: {n} comm fails in the last {window / 60:.0f} min")
            t_win = now

    def step_trace(self) -> None:
        total = self.hours * 3600
        phase1 = min(self.phase1_min * 60, total)
        t0 = self.clock()
        if phase1 > 0:
            self._trace({"neo-01": 1.0}, phase1, "phase 1, master only")
            self.log(f"phase 1: {self.fail_count(t0)} comm fails in {phase1 / 60:.0f} min without the SAU read")
        remaining = total - phase1
        if remaining > 0 and not self.stop.is_set():
            self._trace({"neo-01": 1.0, "pv-array": self.sau_s}, remaining, "phase 2")

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
        with zipfile.ZipFile(zp + ".part", "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(os.listdir(self.trace_dir)):
                if date in name:
                    z.write(os.path.join(self.trace_dir, name), f"trace/{name}")
            for name, text in (extra_texts or {}).items():
                z.writestr(name, text)
            if os.path.exists(self.log_path):
                z.write(self.log_path, os.path.basename(self.log_path))
        os.replace(zp + ".part", zp)                        # never a half-written zip
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
        try:
            self.step_trace()
        except KeyboardInterrupt:                           # Ctrl+C = end now, still report and bundle
            self.stop.set()
            self.log("stopped by hand; reporting and bundling what there is")
        zp = self.step_report_and_bundle(extra_texts=extra)
        self.log("session done. Copy the bundle to the stick or send it; the files stay here too.")
        if self.long_run and not self.stop.is_set():
            self.long()
        return zp

    def long(self) -> None:
        """After the session: the master at 1 s (free for the SAU), the SAU every 30 s, the watch read every 3 s,
        pings every 1 s, the same guard; a report and a bundle every day at zip_hour."""
        every = {"neo-01": 1.0, "pv-array": 30.0}
        self.log("long run: " + ", ".join(f"{d} every {s:g} s" for d, s in every.items()) + f", watch read every 3 s, pings every 1 s; a bundle every day at {self.zip_hour:02d}:00 UTC. Close the window to stop.")
        while not self.stop.is_set():
            now = self.clock()
            nxt = now.replace(hour=self.zip_hour, minute=0, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            self._trace(every, (nxt - now).total_seconds(), "long run", connect_test_s=600.0, watch_s=3.0)
            if self.stop.is_set():
                break
            yesterday = (self.clock() - timedelta(hours=self.zip_hour + 1)).strftime("%Y-%m-%d")
            self.notes = []
            try:
                self.step_report_and_bundle(date=yesterday)
            except Exception as exc:                        # a bad file must not end the long run
                self.log(f"daily report failed: {exc}; the trace goes on")
