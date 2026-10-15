"""
PLC lab: the plant and the master's PLC program running live in the browser, with every block of every
sheet shown with its live pin values, like the controller's own PLC monitor, but on a model you can poke.

    python tools/plc_lab.py                      -> http://localhost:8090
    python tools/plc_lab.py --port 8091 --speed 10

Nothing here touches the plant. The plant is tools/plant_model.py's physics advanced one second at a time; the
PLC is tools/neo_plc.py executing the exported sheets (today's program, or the change-01 variants). Buttons
inject what the history shows: a PV logger comm fail on one or both sessions, a cloud, a load step, the
operator's OFF/AUTO toggle, a grid transfer, a fault reset.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from neo_plc import Plc, load_program, plant_inputs  # noqa: E402
import plant_model as pm  # noqa: E402

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
except ImportError:                                     # the model part works without the web part
    FastAPI = Request = HTMLResponse = JSONResponse = None

CHANGE_DIR = os.path.join(ROOT, "docs", "changes", "change-01_comm_fail_tolerance")
PROGRAMS = {
    "today": (pm.program, {}),
    "change-01 v2 (hold until the link is back)": (lambda: load_program(os.path.join(CHANGE_DIR, "program_v2")), {65514: 300.0}),
    "change-01 v3 (hold 60 s, then 0)": (lambda: load_program(os.path.join(CHANGE_DIR, "program_v3")), {}),
}
HISTORY_S = 900


class LivePlant:
    """plant_model.simulate(), unrolled into one-second ticks with injectable events."""

    def __init__(self, program_name: str = "today", start_hour: float = 10.0, manual_gensets: int = 3):
        self.lock = threading.RLock()
        self.sc = pm.Scenario("lab", manual_gensets=manual_gensets)
        self.day = pm.Day("lab day", clearness=1.0)
        self.t = start_hour * 3600.0                       # seconds of the day
        self.speed = 1.0
        self.paused = False
        self.zero_reaches_sau_in_fail = True               # hypothesis switch: does a write during a fail get through?
        self.program_name = program_name
        self.plc = None
        self._new_plc(program_name)
        # plant state
        self.running = manual_gensets
        self.pv_actual = 0.0
        self.sau_reading = 0.0
        self.curtail = 30.0
        self.pv_cb = [1, 1]
        self.ups_closed = [0, 0]
        self.fail_since = None
        self.last_transfer = -1e9
        self.warn_timer = 0.0
        self.load_offset = 0.0
        self.cloud_until = -1.0
        self.cloud_depth = 0.0
        self.comm_until = [-1.0, -1.0]
        self.off_until = -1.0
        self.grid_override = None                          # None = by the day's hours; True/False forced
        self.fault_reset_pending = False
        self.start_timer = self.stop_timer = 0
        self.auto_pm = False
        self.hist = {k: deque(maxlen=HISTORY_S) for k in ("t", "load", "pv_available", "pv_actual", "setpoint", "request", "gen_kw", "gen_load_pct", "comm")}
        self.events = deque(maxlen=200)
        self._last_bits = {}
        self.ticks = 0
        self._warm_up()

    # ---- program -------------------------------------------------------------------------------
    def _new_plc(self, name: str):
        loader, setpoints = PROGRAMS[name]
        self.plc = Plc(loader(), setpoints, breaker_timeout_s=15)
        self.program_name = name

    def set_program(self, name: str):
        with self.lock:
            self._new_plc(name)
            self._warm_up()
            self.log(f"program: {name}")

    def _warm_up(self):
        for _ in range(30):
            self._tick(1.0, warm=True)

    # ---- helpers ---------------------------------------------------------------------------------
    def log(self, text: str):
        self.events.appendleft((self._clock(), text))

    def _clock(self) -> str:
        s = int(self.t) % 86400
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"

    def on_grid(self) -> bool:
        if self.grid_override is not None:
            return self.grid_override
        return pm.grid_on(self.t / 60, self.day)

    # ---- one second ----------------------------------------------------------------------------------
    def _tick(self, dt: float, warm: bool = False):
        sc, plc = self.sc, self.plc
        minute = self.t / 60
        load = pm.load_profile_kw(int(minute)) + self.load_offset
        avail = pm.pv_available_kw(minute, self.day, [])
        if self.t < self.cloud_until:
            avail *= (1 - self.cloud_depth)
        on_grid = self.on_grid()
        if on_grid != pm.grid_on((self.t - dt) / 60, self.day) and self.grid_override is None:
            self.last_transfer = self.t
        if on_grid:
            self.running = 0
            run_nom = 0.0
        else:
            if self.running == 0:
                self.running = sc.manual_gensets or 3
            run_nom = sum(pm.GEN_NOMS_KW[:self.running])
        pvcb_close = int(not (self.t < self.off_until or self.t - self.last_transfer < 120))
        comm = [int(self.t < self.comm_until[0]), int(self.t < self.comm_until[1])]
        comm_any = comm[0] or comm[1]
        fault_reset = 0
        if self.fault_reset_pending or (self.fail_since is not None and self.t - self.fail_since >= 120):
            fault_reset, self.fail_since, self.fault_reset_pending = 1, None, False
        pv_live = bool(pvcb_close and any(self.pv_cb))
        if not pv_live:
            self.pv_actual = 0.0
        neo_pv_term = self.sau_reading if pv_live else 0.0
        shed_load = sum(sc.ups_critical_share * load for i in range(2) if not self.ups_closed[i])
        load_on_bus = load - shed_load
        gen_kw = 0.0 if on_grid else max(0.0, load_on_bus - self.pv_actual)
        mains_each = (load_on_bus - self.pv_actual) / 3 if on_grid else 0.0
        inputs = plant_inputs(pv_kw=self.sau_reading, max_avail_kw=pm.SAU_MAX_AVAILABLE_KW, curtailment_pct=self.curtail,
                              gensets_kw=gen_kw, running_nominal_kw=run_nom, mains_import_kw=(mains_each,) * 3,
                              on_grid=on_grid, gens_loaded=self.running, pv_cb=tuple(self.pv_cb), ups_fb=tuple(self.ups_closed),
                              comm_fail=(comm[0], comm[1]), pvcb_close=pvcb_close, fault_reset=fault_reset,
                              pv_valid=not comm_any, neo_pv_sample_kw=neo_pv_term)
        plc.step(inputs, dt)
        setpoint = plc.get(21267) or 0.0
        request = plc.get(21265) or 0.0
        cb_cmd = [plc.get(10425, 2) or 0, plc.get(10426, 2) or 0]
        ups_cmd = [plc.get(10427, 2) or 0, plc.get(10434, 2) or 0]
        self.warn_timer = self.warn_timer + dt if plc.get(10433, 2) else 0.0
        if any(plc.get(n, b) for n, b in ((10425, 7), (10426, 0), (10426, 7), (10427, 0), (10427, 7), (10428, 0), (10434, 7), (10435, 0))):
            self.fail_since = self.fail_since if self.fail_since is not None else self.t
        self.pv_cb = cb_cmd
        self.ups_closed = [int(bool(c)) for c in ups_cmd]
        # the PV plant follows the setpoint it actually received
        if comm_any and not self.zero_reaches_sau_in_fail:
            received = self._last_received
        else:
            received = setpoint
        self._last_received = received
        if pvcb_close and any(self.pv_cb):
            target = min(avail, received)
            self.pv_actual += (target - self.pv_actual) * (1 - math.exp(-dt / sc.pv_response_s))
        else:
            self.pv_actual = 0.0
        self.sau_reading = self.pv_actual
        if not on_grid and run_nom:
            gen_load_now = max(0.0, load_on_bus - self.pv_actual) / run_nom * 100
            self.curtail = max(0.0, min(100.0, self.curtail + sc.curtail_rate * (gen_load_now - sc.gen_p_min * 100) * dt))
        elif on_grid:
            self.curtail = 0.0
        # automatic power management, once a minute
        if self.auto_pm and not on_grid and int(self.t) % 60 == 0:
            reserve = sc.start_reserve_kw + sc.solar_coverage * self.pv_actual
            if run_nom - gen_kw < reserve and self.running < pm.GENSETS:
                self.start_timer += 60
                if self.start_timer >= sc.next_start_delay_s:
                    self.running += 1; self.start_timer = 0; self.log(f"power management: genset {self.running} started")
            else:
                self.start_timer = 0
            if self.running > 1 and sum(pm.GEN_NOMS_KW[:self.running - 1]) - gen_kw >= sc.stop_reserve_kw + sc.solar_coverage * self.pv_actual and gen_kw >= sc.min_run_power_kw:
                self.stop_timer += 60
                if self.stop_timer >= sc.next_stop_delay_s:
                    self.log(f"power management: genset {self.running} stopped"); self.running -= 1; self.stop_timer = 0
            else:
                self.stop_timer = 0
        if warm:
            return
        self.t += dt
        self.ticks += 1
        gl = 100 * gen_kw / run_nom if run_nom else 0.0
        for k, v in (("t", self._clock()), ("load", round(load)), ("pv_available", round(avail)), ("pv_actual", round(self.pv_actual)),
                     ("setpoint", round(setpoint)), ("request", round(request)), ("gen_kw", round(gen_kw)), ("gen_load_pct", round(gl, 1)),
                     ("comm", int(comm_any))):
            self.hist[k].append(v)
        self.snapshot = dict(clock=self._clock(), load=round(load), pv_available=round(avail), pv_actual=round(self.pv_actual),
                             setpoint=round(setpoint, 1), request=round(request, 1), gen_kw=round(gen_kw), run_nom=round(run_nom),
                             gen_load_pct=round(gl, 1), gensets=self.running, on_grid=on_grid, curtail=round(self.curtail, 1),
                             pv_cb=self.pv_cb, ups=self.ups_closed, comm=comm, pvcb_close=pvcb_close,
                             warning=int(bool(plc.get(10433, 2))), latch=int(bool(plc.get(10424, 4))))
        # event edges
        for name, key in (("PV logger group 1 comm fail", ("comm", 0)), ("PV logger group 2 comm fail", ("comm", 1)),
                          ("PV prod > PV req warning", (10433, 2)), ("PV CB1 ON command", (10425, 2)), ("UPS1 ON command", (10427, 2)),
                          ("UPS2 ON command", (10434, 2)), ("PV comm lost", (10436, 2))):
            v = comm[key[1]] if key[0] == "comm" else int(bool(plc.get(*key)))
            old = self._last_bits.get(name)
            if old is not None and old != v:
                self.log(f"{name}: {'on' if v else 'off'}")
            self._last_bits[name] = v
        if self.hist["setpoint"][-1] == 0 and len(self.hist["setpoint"]) > 1 and self.hist["setpoint"][-2] > 0:
            self.log("PV setpoint forced to 0")

    _last_received = 0.0
    snapshot = {}

    # ---- the thread ---------------------------------------------------------------------------------
    def run(self, stop: threading.Event):
        while not stop.is_set():
            if self.paused:
                time.sleep(0.1)
                continue
            t0 = time.perf_counter()
            with self.lock:
                self._tick(1.0)
            wait = 1.0 / self.speed - (time.perf_counter() - t0)
            if wait > 0:
                time.sleep(wait)

    # ---- injections ------------------------------------------------------------------------------------
    def inject(self, what: str, value: float = 0):
        with self.lock:
            if what == "comm1":
                self.comm_until[0] = self.t + (value or 10)
            elif what == "comm2":
                self.comm_until[1] = self.t + (value or 10)
            elif what == "comm12":
                self.comm_until = [self.t + (value or 10)] * 2
            elif what == "cloud":
                self.cloud_until, self.cloud_depth = self.t + (value or 120), 0.5
            elif what == "load":
                self.load_offset += value
            elif what == "off":
                self.off_until = self.t + (value or 60)
            elif what == "grid":
                self.grid_override = not self.on_grid()
            elif what == "fault_reset":
                self.fault_reset_pending = True
            elif what == "speed":
                self.speed = max(0.2, min(120.0, value))
            elif what == "pause":
                self.paused = not self.paused
            elif what == "zero_hypothesis":
                self.zero_reaches_sau_in_fail = not self.zero_reaches_sau_in_fail
            elif what == "auto_pm":
                self.auto_pm = not self.auto_pm
            elif what == "gensets":
                self.running = int(value); self.sc.manual_gensets = int(value)
            elif what == "time":
                self.t = float(value) * 3600
            self.log(f"{what} {value:g}" if value else what)

    # ---- the sheet view ---------------------------------------------------------------------------------
    def sheet_view(self, index: int) -> dict:
        prog, plc = self.plc.prog, self.plc
        sheet = next((s for s in prog.sheets if s.index == index), prog.sheets[0])
        produced = {}
        blocks = []
        for b in sheet.blocks:
            ins = [p for p in b.inputs if p.connected or p.raw_const is not None]
            outs = [p for p in b.outputs if p.connected]
            bid = f"b{b.order}"
            for j, o in enumerate(outs):
                produced[o.key] = (bid, j)
            blocks.append(dict(id=bid, type=b.type_name, x=b.x, y=b.y,
                               inputs=[dict(name=prog.name_of(p) if p.connected else (p.name or "const"), value=_fmt(plc._get(p)),
                                            neg=p.negated, const=p.raw_const is not None, key=_key(p)) for p in ins],
                               outputs=[dict(name=prog.name_of(p), value=_fmt(plc.values.get(p.key)), key=_key(p)) for p in outs]))
        wires = []
        for b, blk in zip(sheet.blocks, blocks):
            for i, p in enumerate([q for q in b.inputs if q.connected or q.raw_const is not None]):
                if p.connected and p.key in produced:
                    src, j = produced[p.key]
                    wires.append(dict(frm=src, out=j, to=blk["id"], inp=i))
        return dict(index=sheet.index, name=sheet.name, blocks=blocks, wires=wires,
                    sheets=[dict(index=s.index, name=s.name) for s in prog.sheets])


def _fmt(v):
    if v is None:
        return "####"
    if isinstance(v, float):
        return f"{v:.1f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:.0f}"
    return str(v)


def _key(p):
    return f"{p.comobj}.{p.bit}" if p.is_binary else str(p.comobj)


# ---- web ------------------------------------------------------------------------------------------------
PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>PLC lab</title>
<style>
:root{--bg:#f4f5f3;--fg:#1d2229;--muted:#5c6570;--panel:#fff;--rule:#d6d9d4;--on:#1f5fbf;--warn:#a86d00;--bad:#b3261e}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 'Segoe UI',system-ui,sans-serif}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--rule)}
header b{font-size:16px}
.num{display:inline-block;min-width:92px}.num span{display:block;color:var(--muted);font-size:11px}
.num strong{font-size:18px;font-variant-numeric:tabular-nums}
.bar{display:flex;flex-wrap:wrap;gap:6px;padding:8px 16px;border-bottom:1px solid var(--rule)}
button,select{font:13px 'Segoe UI',system-ui,sans-serif;padding:4px 10px;border:1px solid var(--rule);background:var(--panel);border-radius:3px;cursor:pointer}
button.on{background:var(--on);color:#fff;border-color:var(--on)}
main{display:grid;grid-template-columns:1fr 280px;gap:0}
#chart{width:100%;height:170px;display:block;background:var(--panel);border-bottom:1px solid var(--rule)}
#sheet{overflow:auto;background:var(--panel);min-height:600px}
aside{border-left:1px solid var(--rule);padding:8px 12px;font-size:12px;overflow:auto;max-height:calc(100vh - 230px)}
aside div{padding:2px 0;border-bottom:1px dotted var(--rule);font-variant-numeric:tabular-nums}
.tabs{display:flex;flex-wrap:wrap;gap:4px;padding:6px 16px;background:var(--bg)}
.tabs button.on{background:var(--fg);border-color:var(--fg)}
svg text{font:11px 'Segoe UI',system-ui,sans-serif}
.blk{fill:#fbfbfa;stroke:#1d2229;stroke-width:1}
.pin1{fill:var(--on);font-weight:600}.pin0{fill:var(--muted)}.pinv{fill:#1d2229}
.wire{fill:none;stroke:#1f5fbf;stroke-width:1.2;opacity:.75}
.hdr{font-weight:600}
</style></head><body>
<header><b>PLC lab</b>
<span class="num"><span>time</span><strong id="clock">--:--:--</strong></span>
<span class="num"><span>load kW</span><strong id="load">-</strong></span>
<span class="num"><span>solar available</span><strong id="pv_available">-</strong></span>
<span class="num"><span>solar actual</span><strong id="pv_actual">-</strong></span>
<span class="num"><span>setpoint to SAU</span><strong id="setpoint">-</strong></span>
<span class="num"><span>gensets kW</span><strong id="gen_kw">-</strong></span>
<span class="num"><span>gen load %</span><strong id="gen_load_pct">-</strong></span>
<span class="num"><span>sets / nominal</span><strong id="gensets">-</strong></span>
<span class="num"><span>curtail %</span><strong id="curtail">-</strong></span>
<span class="num"><span>comm fail g1 g2</span><strong id="comm">-</strong></span>
<span class="num"><span>PV CB / UPS</span><strong id="cbs">-</strong></span>
<span class="num"><span>warning</span><strong id="warning">-</strong></span>
</header>
<div class="bar">
<select id="program"></select>
<button onclick="ctl('pause')" id="pause">pause</button>
speed <button onclick="ctl('speed',1)">1x</button><button onclick="ctl('speed',10)">10x</button><button onclick="ctl('speed',60)">60x</button>
<span style="width:12px"></span>
<button onclick="ctl('comm1',10)">comm fail g1 10 s</button>
<button onclick="ctl('comm2',10)">comm fail g2 10 s</button>
<button onclick="ctl('comm12',10)">both 10 s</button>
<button onclick="ctl('comm12',90)">both 90 s</button>
<button onclick="ctl('comm12',400)">both 400 s</button>
<button onclick="ctl('cloud',120)">cloud 50 % 2 min</button>
<button onclick="ctl('load',300)">load +300</button>
<button onclick="ctl('load',-300)">load -300</button>
<button onclick="ctl('off',60)">operator OFF 60 s</button>
<button onclick="ctl('grid')">grid on/off</button>
<button onclick="ctl('fault_reset')">fault reset</button>
<button onclick="ctl('gensets',2)">2 sets</button><button onclick="ctl('gensets',3)">3 sets</button><button onclick="ctl('gensets',4)">4 sets</button>
<button onclick="ctl('auto_pm')" id="auto_pm">power management AUTO</button>
<button onclick="ctl('zero_hypothesis')" id="zero_hypothesis">zero reaches the SAU during a fail</button>
<button onclick="ctl('time',8.8)">jump to 08:48</button><button onclick="ctl('time',12)">12:00</button>
</div>
<canvas id="chart"></canvas>
<div class="tabs" id="tabs"></div>
<main><div id="sheet"></div><aside id="events"></aside></main>
<script>
let sheet=10, state=null;
async function ctl(what,value){await fetch('/api/control',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({what,value:value||0})});}
async function poll(){
  try{const r=await fetch('/api/state?sheet='+sheet); state=await r.json(); render();}catch(e){}
  setTimeout(poll,300);
}
function render(){
  const s=state.plant; if(!s.clock) return;
  for(const k of ['clock','load','pv_available','pv_actual','setpoint','gen_kw','gen_load_pct','curtail']) document.getElementById(k).textContent=s[k];
  document.getElementById('gensets').textContent=s.gensets+' / '+s.run_nom;
  document.getElementById('comm').textContent=s.comm[0]+' '+s.comm[1];
  document.getElementById('cbs').textContent=s.pv_cb.join('')+' / '+s.ups.join('');
  document.getElementById('warning').textContent=s.warning?'PV prod > req':(s.on_grid?'on grid':'-');
  document.getElementById('pause').textContent=state.paused?'play':'pause';
  document.getElementById('auto_pm').className=state.auto_pm?'on':'';
  document.getElementById('zero_hypothesis').className=state.zero_hypothesis?'on':'';
  const sel=document.getElementById('program');
  if(sel.options.length==0){for(const p of state.programs){const o=document.createElement('option');o.value=p;o.textContent=p;sel.appendChild(o);} sel.onchange=()=>fetch('/api/program',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:sel.value})});}
  if(sel.value!=state.program) sel.value=state.program;
  chart(state.hist);
  tabs(state.sheet.sheets);
  drawSheet(state.sheet);
  document.getElementById('events').innerHTML=state.events.map(e=>'<div>'+e[0]+' '+e[1]+'</div>').join('');
}
function chart(h){
  const c=document.getElementById('chart'); const W=c.width=c.clientWidth*devicePixelRatio, H=c.height=170*devicePixelRatio; const g=c.getContext('2d');
  g.clearRect(0,0,W,H); const n=h.load.length; if(n<2) return; const max=Math.max(2200,...h.load,...h.pv_available)*1.05;
  const x=i=>i/(900-1)*W, y=v=>H-v/max*H;
  for(let i=0;i<n;i++){ if(h.comm[i]){g.fillStyle='rgba(168,109,0,.18)'; g.fillRect(x(i),0,Math.max(1,W/900),H);} }
  const line=(arr,col,w)=>{g.beginPath();g.strokeStyle=col;g.lineWidth=w*devicePixelRatio;for(let i=0;i<n;i++){i?g.lineTo(x(i),y(arr[i])):g.moveTo(x(i),y(arr[i]));}g.stroke();};
  line(h.load,'#1d2229',1); line(h.pv_available,'#9aa3ad',1); line(h.pv_actual,'#2a8a3c',2); line(h.setpoint,'#1f5fbf',1.2); line(h.gen_kw,'#b3261e',1.5);
  g.fillStyle='#5c6570'; g.font=(11*devicePixelRatio)+'px sans-serif';
  g.fillText('black load, grey solar available, green solar actual, blue setpoint to SAU, red gensets, amber = comm fail, last 15 min',8*devicePixelRatio,14*devicePixelRatio);
}
function tabs(list){
  const t=document.getElementById('tabs'); if(t.children.length) {for(const b of t.children) b.className=(+b.dataset.i==sheet)?'on':''; return;}
  for(const s of list){const b=document.createElement('button');b.dataset.i=s.index;b.textContent=s.index+' '+s.name;b.onclick=()=>{sheet=s.index;};t.appendChild(b);}
}
function drawSheet(sh){
  const CX=34, RY=16, BW=170, M=240, MY=20; let maxx=0,maxy=0; const pos={};
  for(const b of sh.blocks){ const rows=Math.max(b.inputs.length,b.outputs.length,1); const h=RY*(rows+1)+6; const px=M+(b.x-4)*CX, py=MY+b.y*RY; pos[b.id]={x:px,y:py,h:h,b:b}; maxx=Math.max(maxx,px+BW+260); maxy=Math.max(maxy,py+h+10); }
  let out=`<svg width="${maxx}" height="${maxy}" xmlns="http://www.w3.org/2000/svg">`;
  for(const w of sh.wires){ const a=pos[w.frm], b=pos[w.to]; if(!a||!b) continue; const x1=a.x+BW, y1=a.y+RY*(w.out+1)+10, x2=b.x, y2=b.y+RY*(w.inp+1)+10; const dx=Math.max(30,(x2-x1)/2);
    out+=`<path class="wire" d="M${x1},${y1} C${x1+dx},${y1} ${x2-dx},${y2} ${x2},${y2}"/>`; }
  for(const id in pos){ const p=pos[id], b=p.b;
    out+=`<rect class="blk" x="${p.x}" y="${p.y}" width="${BW}" height="${p.h}" rx="2"/><text class="hdr" x="${p.x+6}" y="${p.y+12}">${esc(b.type)}</text>`;
    b.inputs.forEach((i,k)=>{ const y=p.y+RY*(k+1)+14; const cls=i.value==='1'?'pin1':(i.value==='0'?'pin0':'pinv');
      out+=`<text class="${cls}" x="${p.x+6}" y="${y}">${esc(i.value)}</text><text x="${p.x-4}" y="${y}" text-anchor="end" fill="#5c6570">${i.neg?'NOT ':''}${esc(i.name)}</text>`; });
    b.outputs.forEach((o,k)=>{ const y=p.y+RY*(k+1)+14; const cls=o.value==='1'?'pin1':(o.value==='0'?'pin0':'pinv');
      out+=`<text class="${cls}" x="${p.x+BW-6}" y="${y}" text-anchor="end">${esc(o.value)}</text><text x="${p.x+BW+4}" y="${y}" fill="#5c6570">${esc(o.name)}</text>`; });
  }
  out+='</svg>'; document.getElementById('sheet').innerHTML=out;
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
poll();
</script></body></html>"""


def create_app(plant: LivePlant):
    app = FastAPI(title="PLC lab")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE

    @app.get("/api/state")
    def state(sheet: int = 10):
        with plant.lock:
            return JSONResponse(dict(plant=plant.snapshot, paused=plant.paused, speed=plant.speed, program=plant.program_name,
                                     programs=list(PROGRAMS), auto_pm=plant.auto_pm, zero_hypothesis=plant.zero_reaches_sau_in_fail,
                                     hist={k: list(v) for k, v in plant.hist.items()}, events=list(plant.events),
                                     sheet=plant.sheet_view(sheet)))

    @app.post("/api/control")
    async def control(req: Request):
        body = await req.json()
        plant.inject(str(body.get("what", "")), float(body.get("value") or 0))
        return {"ok": True}

    @app.post("/api/program")
    async def program(req: Request):
        body = await req.json()
        name = body.get("name")
        if name in PROGRAMS:
            plant.set_program(name)
        return {"ok": name in PROGRAMS}

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description="the plant and its PLC program, live in the browser")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--hour", type=float, default=10.0, help="start time of day")
    a = ap.parse_args()
    import uvicorn
    plant = LivePlant(start_hour=a.hour)
    plant.speed = a.speed
    stop = threading.Event()
    threading.Thread(target=plant.run, args=(stop,), daemon=True).start()
    print(f"PLC lab: http://localhost:{a.port}   (Ctrl+C stops)")
    try:
        uvicorn.run(create_app(plant), host="127.0.0.1", port=a.port, log_level="warning")
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
