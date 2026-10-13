"""
CLI.

  python -m qs_hybrid.cli demo            # simulate a day with autopilot, print what happened
  python -m qs_hybrid.cli plan --pv 60 --reserve 30
  python -m qs_hybrid.cli report
  python -m qs_hybrid.cli chat            # talk to the AI (needs ANTHROPIC_API_KEY)
  python -m qs_hybrid.cli audit           # verify the audit chain
  python -m qs_hybrid.cli serve           # REST API on :8000
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .control.autopilot import Autopilot
from .control.ems import Goal
from .control.policy import Caller
from .registry.models import OperatingMode
from .site import Site


def _site(args) -> Site:
    return Site(args.config, args.data)


def cmd_plan(args):
    s = _site(args)
    g = Goal(objective="share_target" if args.pv else "min_cost", pv_share_pct=args.pv, bess_reserve_pct=args.reserve)
    print(json.dumps(s.ems.plan(g).to_dict(), indent=2))


def cmd_report(args):
    from .analytics.report import build_daily_report
    s = _site(args)
    print(json.dumps(build_daily_report(s.registry, s.store, s.clock()), indent=2, default=str))


def cmd_audit(args):
    s = _site(args)
    ok, n = s.audit.verify()
    print(f"audit chain {'OK' if ok else 'BROKEN'} — {n} records")
    for r in s.audit.tail(args.n):
        print(r["ts"], r["kind"], {k: v for k, v in r.items() if k not in ("ts", "kind", "prev", "hash")})


def cmd_demo(args):
    """A full simulated day in autonomous mode, operator approving genset starts/stops."""
    s = _site(args)
    if not s.simulated:
        print("demo requires a simulator-only site.yaml")
        sys.exit(1)
    s.registry.policy.mode = OperatingMode.AUTONOMOUS
    notes: list[str] = []
    ap = Autopilot(s, notify=notes.append)
    ap.set_goal(Goal(objective="share_target", pv_share_pct=args.pv or 60, bess_reserve_pct=args.reserve or 30))
    ap.enable()
    op = Caller("operator:demo", "operator")
    print(f"=== demo day starting {s.clock().isoformat()}  mode=autonomous  goal={ap.state.goal}")
    day0 = s.clock()
    for tick in range(int(24 * 60 / 15)):          # every 15 min
        ap.tick()
        # operator auto-approves genset start/stop proposals in the demo (in real life: a person)
        for c in s.gateway.pending():
            s.gateway.approve(c.id, op)
        if tick % 4 == 0:
            sn = s.snapshot()
            d = sn["devices"]
            print(f"{s.clock().strftime('%H:%M')} load={sn['site']['load_kw']:>5.0f}  pv={d['pv-01'].get('power_kw',0):>5}  "
                  f"bess={d['bess-01'].get('power_kw',0):>6} soc={d['bess-01'].get('soc_pct',0):>5}  "
                  f"g1={d['genset-01'].get('power_kw',0):>5} g2={d['genset-02'].get('power_kw',0):>5}  unserved={sn['site']['unserved_kw']}")
        if args.faults and tick == 40:
            s.plant.inject("pv_soiling")
        s.advance(15 * 60, step=60)
    from .analytics.report import build_daily_report
    r = build_daily_report(s.registry, s.store, day0)
    print("\n=== totals", json.dumps(r["totals"], indent=1))
    for gid, d in r["devices"].items():
        print("   ", gid, d)
    print("=== faults", [(e["code"], e["device_id"]) for e in r["faults"]])
    print("=== notifications", len(notes), "(first 5)")
    for n in notes[:5]:
        print("  ", n)
    ok, n = s.audit.verify()
    print(f"=== audit chain ok={ok} records={n}  tripped={ap.state.tripped} {ap.state.trip_reason}")


def cmd_chat(args):
    from .ai.agent import SiteAgent
    s = _site(args)
    if args.warm:
        s.advance(6 * 3600)
    agent = SiteAgent(s)
    print("site AI ready. type a question (ctrl-D to exit)")
    try:
        while True:
            q = input("> ").strip()
            if not q:
                continue
            print(agent.ask(q, on_tool=lambda n, i, o: print(f"   [tool {n}]")))
    except (EOFError, KeyboardInterrupt):
        print()


def cmd_serve(args):
    import uvicorn
    os.environ["QS_SITE_CONFIG"] = args.config
    os.environ["QS_DATA_DIR"] = args.data
    uvicorn.run("qs_hybrid.api.app:app", host="0.0.0.0", port=args.port, reload=False)


def cmd_local(args):
    """Collector + dashboard on one machine: read devices every N seconds, serve the UI."""
    import threading
    import time as _time
    import webbrowser

    import uvicorn

    from .api import app as api_mod

    os.environ["QS_SITE_CONFIG"] = args.config
    os.environ["QS_DATA_DIR"] = args.data
    s = api_mod.site()                      # builds the Site once; the API shares it
    interval = max(5.0, args.interval)

    if s.simulated and args.seed_hours > 0:
        print(f"simulator: seeding {args.seed_hours} h of history…")
        from .control.autopilot import Autopilot
        from .control.ems import Goal
        from .control.policy import Caller
        from .registry.models import OperatingMode
        s.registry.policy.mode = OperatingMode.AUTONOMOUS
        ap = Autopilot(s)
        ap.set_goal(Goal(objective="share_target", pv_share_pct=60, bess_reserve_pct=30))
        ap.enable()
        op = Caller("operator:seed", "operator")
        for _ in range(int(args.seed_hours * 4)):
            ap.tick()
            for c in s.gateway.pending():
                s.gateway.approve(c.id, op)
            s.advance(15 * 60, step=60)
        print("seeded.")

    def loop():
        while True:
            t0 = _time.time()
            try:
                if s.simulated:
                    s.advance(interval, step=interval)   # sim time moves with wall time
                else:
                    s.poll()
            except Exception as exc:
                print(f"collector error: {exc}")
            _time.sleep(max(0.0, interval - (_time.time() - t0)))

    threading.Thread(target=loop, daemon=True).start()
    url = f"http://localhost:{args.port}/"
    print(f"collector running every {interval:.0f}s; dashboard at {url}")
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(api_mod.app, host=args.host, port=args.port, log_level="warning")


def main():
    ap = argparse.ArgumentParser(prog="qs_hybrid")
    ap.add_argument("--config", default="config/site.yaml")
    ap.add_argument("--data", default="./data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--pv", type=float); p.add_argument("--reserve", type=float); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("report"); p.set_defaults(fn=cmd_report)
    p = sub.add_parser("audit"); p.add_argument("-n", type=int, default=20); p.set_defaults(fn=cmd_audit)
    p = sub.add_parser("demo"); p.add_argument("--pv", type=float); p.add_argument("--reserve", type=float); p.add_argument("--faults", action="store_true"); p.set_defaults(fn=cmd_demo)
    p = sub.add_parser("chat"); p.add_argument("--warm", action="store_true", help="advance sim 6h first"); p.set_defaults(fn=cmd_chat)
    p = sub.add_parser("serve"); p.add_argument("--port", type=int, default=8000); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("local", help="collector + dashboard on this machine")
    p.add_argument("--port", type=int, default=8080); p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--interval", type=float, default=30.0); p.add_argument("--seed-hours", type=float, default=24.0)
    p.add_argument("--open", action="store_true", help="open the browser"); p.set_defaults(fn=cmd_local)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
