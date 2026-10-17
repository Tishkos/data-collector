"""
Turns a day of trace files into the numbers the questions need: comm fails per session with their lengths,
whether both sessions fall together, the SAU's response times and our connection tests next to the master's
fails, the cadence of the setpoint words the master writes, the regulator through a fail, the load steps.

    python -m plantcollector.cli report-trace --date 2026-10-16          (files in data/trace)
"""

from __future__ import annotations

import csv
import os
import statistics
from datetime import datetime, timedelta


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _read(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _pct(values: list[float], p: float):
    if not values:
        return None
    vs = sorted(values)
    k = max(0, min(len(vs) - 1, int(round((len(vs) - 1) * p))))
    return vs[k]


def fails_from_events(events: list[dict], device: str = "neo-01") -> list[dict]:
    """Each comm fail as {session, start, end, seconds} from the rising and falling edges of the two bits."""
    out: list[dict] = []
    open_: dict[str, datetime] = {}
    for e in events:
        if e["device"] != device or e["point"] not in ("ecu2_comm_fail", "ecu3_comm_fail"):
            continue
        s = "group 1" if e["point"] == "ecu2_comm_fail" else "group 2"
        t = _ts(e["ts"])
        if e["new"] == "1":
            open_[s] = t
        elif e["new"] == "0" and s in open_:
            t0 = open_.pop(s)
            out.append(dict(session=s, start=t0, end=t, seconds=(t - t0).total_seconds()))
    for s, t0 in open_.items():
        out.append(dict(session=s, start=t0, end=None, seconds=None))
    return sorted(out, key=lambda f: f["start"])


def episodes(fails: list[dict], gap_s: float = 120.0) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for f in fails:
        if groups and (f["start"] - (groups[-1][-1]["end"] or groups[-1][-1]["start"])).total_seconds() <= gap_s:
            groups[-1].append(f)
        else:
            groups.append([f])
    return groups


def _in_fail(t: datetime, fails: list[dict], margin_s: float = 0) -> bool:
    for f in fails:
        end = f["end"] or (f["start"] + timedelta(seconds=600))
        if f["start"] - timedelta(seconds=margin_s) <= t <= end + timedelta(seconds=margin_s):
            return True
    return False


def summarize(trace_dir: str, date: str) -> str:
    """A markdown summary of one day's files. Every number says what it is counted from."""
    neo = _read(os.path.join(trace_dir, f"neo-01_{date}.csv"))
    sau = _read(os.path.join(trace_dir, f"pv-array_{date}.csv"))
    ev = _read(os.path.join(trace_dir, f"events_{date}.csv"))
    con = _read(os.path.join(trace_dir, f"connect_{date}.csv"))
    L = [f"# Trace of {date}", ""]
    L.append(f"Rows: master {len(neo)}, SAU {len(sau)}, events {len(ev)}, connection tests {len(con)}.")
    if neo:
        L.append(f"Master read from {neo[0]['ts'][11:19]} to {neo[-1]['ts'][11:19]} UTC; reads that got no answer at all: "
                 f"{sum(1 for r in neo if r.get('ok_blocks') == '0')}.")
    L.append("")

    # ---- comm fails
    fails = fails_from_events(ev)
    L.append("## Comm fails (from the master's ECU 2 / ECU 3 bits in the event log)")
    if not fails:
        L.append("None declared while the trace ran.")
    else:
        lens = [f["seconds"] for f in fails if f["seconds"] is not None]
        by = {s: [f for f in fails if f["session"] == s] for s in ("group 1", "group 2")}
        L.append(f"{len(fails)} fails: group 1 {len(by['group 1'])}, group 2 {len(by['group 2'])}; "
                 f"{len(episodes(fails))} episodes (fails closer than 2 min).")
        if lens:
            L.append(f"Length: median {statistics.median(lens):.0f} s, p95 {_pct(lens, 0.95):.0f} s, max {max(lens):.0f} s "
                     f"(what a hold time N has to cover).")
        both = 0
        for f in by["group 1"]:
            if any(abs((g["start"] - f["start"]).total_seconds()) <= 5 for g in by["group 2"]):
                both += 1
        L.append(f"Both sessions within 5 s of each other: {both} of {len(by['group 1'])} group-1 fails "
                 f"(both silent = nothing we write reaches the SAU; one alive = the zero gets through the other).")
        L.append("")
        L.append("| start (UTC) | session | seconds |")
        L.append("|---|---|---|")
        for f in fails[:40]:
            L.append(f"| {f['start'].strftime('%H:%M:%S')} | {f['session']} | {'open' if f['seconds'] is None else f'{f['seconds']:.0f}'} |")
        if len(fails) > 40:
            L.append(f"| ... | {len(fails) - 40} more | |")
    L.append("")

    # ---- the SAU next to the fails
    L.append("## The SAU as we see it (our own reads and connection tests)")
    if sau:
        ms = [_num(r["ms"]) for r in sau if _num(r["ms"]) is not None]
        bad = [r for r in sau if r.get("ok_blocks") == "0"]
        L.append(f"Response time: median {statistics.median(ms):.0f} ms, p95 {_pct(ms, 0.95):.0f} ms, max {max(ms):.0f} ms; "
                 f"reads with no answer: {len(bad)} of {len(sau)}.")
        if fails:
            inside = [r for r in sau if _in_fail(_ts(r["ts"]), fails)]
            inside_bad = sum(1 for r in inside if r.get("ok_blocks") == "0")
            inside_ms = [_num(r["ms"]) for r in inside if _num(r["ms"]) is not None and r.get("ok_blocks") != "0"]
            L.append(f"During the master's fails: {len(inside)} of our reads, {inside_bad} unanswered"
                     + (f", answered ones median {statistics.median(inside_ms):.0f} ms" if inside_ms else "") + ".")
            if inside and inside_bad == 0:
                L.append("The SAU answered us while the master logged a fail: the fault is not the SAU being dead; look at the master's sessions and the write overlap.")
            elif inside and inside_bad == len(inside):
                L.append("The SAU answered nobody during the fails: the SAU box or the network to it.")
    else:
        L.append("No SAU rows (the SAU was not traced).")
    if con:
        fails_con = [r for r in con if r.get("ok") != "1"]
        hosts = sorted({r["host"] for r in con})
        L.append(f"Connection tests: {len(con)} on {', '.join(hosts)}; failed {len(fails_con)}"
                 + (": " + ", ".join(f"{r['ts'][11:19]} {r['host']}" for r in fails_con[:10]) if fails_con else "") + ".")
    L.append("")

    # ---- the inverters on the SAU's RS485 (the counts the SAU reports)
    if sau and "inverters_online" in sau[0]:
        L.append("## Inverters on the SAU's RS485 (online count the SAU reports)")
        pts = [(_ts(r["ts"]), _num(r.get("inverters_online"))) for r in sau if _num(r.get("inverters_online")) is not None]
        if pts:
            vals = [v for _, v in pts]
            drops = [(t, v) for (t, v), (t0, v0) in zip(pts[1:], pts[:-1]) if v < v0]
            L.append(f"Online inverters: max {max(vals):.0f}, min {min(vals):.0f}; the count fell {len(drops)} times"
                     + (": " + ", ".join(f"{t.strftime('%H:%M:%S')} to {v:.0f}" for t, v in drops[:12]) if drops else "") + ".")
            if fails:
                near = 0
                for f in fails:
                    if any(0 <= (f["start"] - t).total_seconds() <= 30 for t, _ in drops):
                        near += 1
                L.append(f"Master comm fails with an inverter dropping off the RS485 in the 30 s before them: {near} of {len(fails)}. "
                         "A high share says the fail is born on the RS485 bus (inverter off, SAU stalled, master timed out).")
        else:
            L.append("No inverter counts in the SAU rows.")
        L.append("")

    # ---- the setpoint words
    if sau and "set_2101" in sau[0]:
        L.append("## The setpoint words the master writes (read back from the SAU)")
        for key in ("set_2100", "set_2101", "set_2102", "set_2103"):
            vals = [(r["ts"], r.get(key, "")) for r in sau]
            changes = [(t, v) for (t, v), (t0, v0) in zip(vals[1:], vals[:-1]) if v != v0 and v != "" and v0 != ""]
            distinct = len({v for _, v in vals if v != ""})
            L.append(f"- {key}: {distinct} distinct values, {len(changes)} changes between reads.")
        try:
            dec = [(int(float(r["set_2100"])) << 16 | int(float(r["set_2101"]))) * 0.1 for r in sau if r.get("set_2100") not in ("", None) and r.get("set_2101") not in ("", None)]
            if dec:
                L.append(f"- AP Set decoded as 32-bit at 2100-2101 (group 1 layout): min {min(dec):.1f}, max {max(dec):.1f} kW.")
            dec2 = [(int(float(r["set_2101"])) << 16 | int(float(r["set_2102"]))) * 0.1 for r in sau if r.get("set_2101") not in ("", None) and r.get("set_2102") not in ("", None)]
            if dec2:
                L.append(f"- the same decoded at 2101-2102 (group 2 layout): min {min(dec2):.1f}, max {max(dec2):.1f} kW. "
                         "One of the two should look like a real setpoint, the other like garbage; that tells which layout the SAU uses.")
        except ValueError:
            pass
        L.append("")

    # ---- the regulator and the solar through a fail
    if neo and fails:
        L.append("## The master's regulator and the solar through the fails")
        for f in [f for f in fails if f["seconds"]][:5]:
            before = [r for r in neo if f["start"] - timedelta(seconds=30) <= _ts(r["ts"]) < f["start"]]
            during = [r for r in neo if f["start"] <= _ts(r["ts"]) < (f["end"] or f["start"] + timedelta(seconds=600))]
            after = [r for r in neo if (f["end"] or f["start"]) < _ts(r["ts"]) <= (f["end"] or f["start"]) + timedelta(seconds=30)]
            def m(rows, key):
                v = [_num(r.get(key)) for r in rows if _num(r.get(key)) is not None]
                return f"{statistics.mean(v):.1f}" if v else "-"
            L.append(f"- {f['start'].strftime('%H:%M:%S')} {f['session']} {f['seconds']:.0f} s: curtailment % before/during/after "
                     f"{m(before, 'pv_curtailment_pct')} / {m(during, 'pv_curtailment_pct')} / {m(after, 'pv_curtailment_pct')}; "
                     f"PV kW {m(before, 'pv_prod_kw')} / {m(during, 'pv_prod_kw')} / {m(after, 'pv_prod_kw')}; "
                     f"gensets kW {m(before, 'power_kw')} / {m(during, 'power_kw')} / {m(after, 'power_kw')}.")
        L.append("If the curtailment % collapses during a fail and is still low after it, the hold is undone the moment the link returns.")
        L.append("")

    # ---- load steps
    if neo:
        L.append("## Load steps (from gensets + solar + mains, one-minute windows)")
        series = []
        for r in neo:
            g, pv = _num(r.get("power_kw")), _num(r.get("pv_prod_kw"))
            if g is None or pv is None:                       # an invalid solar reading (during a fail) is not a load drop
                continue
            mains = _num(r.get("mains_power_kw"), 0.0)
            series.append((_ts(r["ts"]), g + pv + max(0.0, mains)))
        drops = []
        j = 0
        for i, (t, v) in enumerate(series):
            while j < len(series) and (series[j][0] - t).total_seconds() < 60:
                j += 1
            window = series[i:j]
            if len(window) > 1:
                drops.append((t, v - min(w for _, w in window)))
        if drops:
            worst = max(drops, key=lambda d: d[1])
            L.append(f"Largest fall within one minute: {worst[1]:.0f} kW at {worst[0].strftime('%H:%M:%S')}; "
                     f"p99 of one-minute falls: {_pct([d for _, d in drops], 0.99):.0f} kW. The reverse-power margin with three sets is about 788 kW.")
        L.append("")
    return "\n".join(L)
