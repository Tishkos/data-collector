"""
plantcollector command line.

  python -m plantcollector.cli --config config/site.yaml run           collector + dashboard
  python -m plantcollector.cli --config config/site.plant.yaml check   one poll, print every point
  python -m plantcollector.cli --config ... export --hours 24 --out data.xlsx
  python -m plantcollector.cli --config ... report --date 2026-10-12 --out report.xlsx
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone

from .core import Collector, run_loop


def _collector(a, *, seed: float = 0.0, fresh: bool = False, initial_poll: bool = True) -> Collector:
    return Collector(a.config, a.data, seed_hours=seed, fresh=fresh, initial_poll=initial_poll)


def _log_to_file(path: str) -> None:
    """Background runs have no console: send output to a small rotating log file."""
    import logging
    from logging.handlers import RotatingFileHandler
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    h = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)

    class _Stream:
        def write(self, m):
            m = m.rstrip()
            if m:
                logging.getLogger("collector").info(m)
        def flush(self): pass
    sys.stdout = sys.stderr = _Stream()


def _wait_for_port(host: str, port: int, seconds: int = 40) -> None:
    """A previous instance may still be closing; wait for the port instead of failing."""
    import socket
    for i in range(seconds):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sk.bind(("" if host == "0.0.0.0" else host, port))
                return
            except OSError:
                if i == 0:
                    print(f"[collector] port {port} is busy, waiting for it to free up...")
                time.sleep(1)
    print(f"[collector] port {port} still busy after {seconds} s; starting anyway")


def cmd_run(a) -> int:
    import uvicorn
    from .api import create_app

    if a.log:
        _log_to_file(a.log)
    try:
        c = _collector(a, seed=a.seed_hours if not a.no_seed else 0.0, fresh=True, initial_poll=False)
    except Exception as exc:
        print(f"[collector] cannot start: {exc}")
        raise
    forced = a.interval                       # --interval on the command line wins over the dashboard setting
    if a.interval is None:
        a.interval = float(c.registry.site.poll_interval_s)
    if c.simulated:
        print(f"[collector] DEMO mode: simulated plant, {a.seed_hours:.0f} h of history generated")
    else:
        n = len(c.registry.devices)
        reqs = sum(len(getattr(ad, "_blocks", [])) or 1 for ad in c.adapters.values())
        print(f"[collector] READ-ONLY polling of {n} devices every {a.interval:.0f} s "
              f"({reqs} Modbus requests per cycle); data in {c.db_path}")
    stop = threading.Event()
    t = threading.Thread(target=run_loop, args=(c, forced, stop), daemon=True, name="poller")
    t.start()
    url = f"http://localhost:{a.port}"
    _wait_for_port(a.host, a.port)
    print(f"[collector] dashboard: {url}   api: {url}/api/docs   database: {c.db_path}")
    if a.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(create_app(c), host=a.host, port=a.port, log_level="warning", log_config=None)
    finally:
        stop.set()
        c.close()
    return 0


def cmd_check(a) -> int:
    """One poll of every device, printed as a table. The commissioning tool for the plant PC."""
    c = _collector(a)
    bad = 0
    for d in c.registry.devices.values():
        rows = c.store.latest(d.id)
        conn = d.connection
        where = f"{conn.get('host', '')}:{conn.get('port', '')} unit {conn.get('unit_id', '')}" if d.adapter == "modbus_tcp" else d.adapter
        print(f"\n== {d.id}  ({d.kind}, {d.model})  {where}")
        for p in d.points:
            r = rows.get(p.id)
            if r is None:
                print(f"   {p.id:<28} --        (not read)")
                continue
            q = r.quality.value
            if q != "good":
                bad += 1
            val = "" if r.value is None else (f"{r.value:.3f}" if isinstance(r.value, float) else str(r.value))
            addr = p.raw.get("address", "")
            print(f"   {p.id:<28} {val:>12} {p.unit:<6} {q:<6} addr={addr!s:<6} {r.error or ''}")
    print(f"\n{sum(len(d.points) for d in c.registry.devices.values()) - bad} good, {bad} not good")
    c.close()
    return 0 if bad == 0 else 1


def _range(a, c: Collector):
    until = c.now()
    since = until - timedelta(hours=a.hours)
    if a.frm:
        since = datetime.fromisoformat(a.frm).replace(tzinfo=timezone.utc)
    if a.to:
        until = datetime.fromisoformat(a.to).replace(tzinfo=timezone.utc)
    return since, until


def cmd_export(a) -> int:
    from . import export
    c = _collector(a)
    since, until = _range(a, c)
    headers, rows = export.wide_table(c, since, until, a.step)
    out = a.out or f"export_{since.strftime('%Y%m%d%H%M')}_{until.strftime('%Y%m%d%H%M')}.{a.format}"
    data = export.to_csv(headers, rows) if a.format == "csv" else export.to_xlsx(c, headers, rows, since, until, a.step)
    with open(out, "wb") as f:
        f.write(data)
    print(f"wrote {out}: {len(rows)} rows x {len(headers)} columns")
    return 0


def cmd_report(a) -> int:
    from . import export
    c = _collector(a)
    day = datetime.fromisoformat(a.date).replace(tzinfo=timezone.utc) if a.date else c.now()
    out = a.out or f"daily_{day.strftime('%Y-%m-%d')}.xlsx"
    with open(out, "wb") as f:
        f.write(export.daily_report_xlsx(c, day))
    print(f"wrote {out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="plantcollector", description="read-only plant data collector")
    ap.add_argument("--config", default=os.path.join("config", "site.yaml"))
    ap.add_argument("--data", default="data", help="folder for the database")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="collector + dashboard")
    r.add_argument("--host", default="0.0.0.0")
    r.add_argument("--port", type=int, default=8080)
    r.add_argument("--interval", type=float, default=None, help="poll interval in seconds (default: site.poll_interval_s from the config)")
    r.add_argument("--open", action="store_true", help="open the dashboard in the browser")
    r.add_argument("--seed-hours", type=float, default=72.0, help="demo only: hours of history to generate")
    r.add_argument("--no-seed", action="store_true")
    r.add_argument("--log", help="write output to this file instead of the console")
    r.set_defaults(fn=cmd_run)

    k = sub.add_parser("check", help="poll once and print every point")
    k.set_defaults(fn=cmd_check)

    e = sub.add_parser("export", help="write the wide table to csv/xlsx")
    e.add_argument("--hours", type=float, default=24)
    e.add_argument("--from", dest="frm")
    e.add_argument("--to")
    e.add_argument("--step", type=int, default=300)
    e.add_argument("--format", choices=["csv", "xlsx"], default="xlsx")
    e.add_argument("--out")
    e.set_defaults(fn=cmd_export)

    p = sub.add_parser("report", help="daily report workbook")
    p.add_argument("--date")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_report)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
