# Draws docs/diagrams/plant_today_and_next.svg: the plant as understood on 14 Oct 2026 and the proposed modifications.
# Run from the repository root: python tools/plant_diagram.py
W, H = 1900, 1180
ink, muted, line = "#1f2937", "#6b7280", "#9ca3af"
c_grid, c_gen, c_pv, c_ups, c_neo, c_pc, c_cci = "#2563eb", "#b45309", "#15803d", "#7c3aed", "#0f766e", "#374151", "#be123c"
out = []
def add(s): out.append(s)
def box(x, y, w, h, title, lines=(), color=ink, fill="#ffffff", title_size=17, size=13.5, dashed=False, rx=10):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{color}" stroke-width="2" {"stroke-dasharray=\"7 5\"" if dashed else ""}/>')
    add(f'<text x="{x+14}" y="{y+26}" font-size="{title_size}" font-weight="700" fill="{color}">{title}</text>')
    for i, l in enumerate(lines):
        add(f'<text x="{x+14}" y="{y+50+i*19}" font-size="{size}" fill="{ink}">{l}</text>')
def arrow(x1, y1, x2, y2, color=line, width=2.2, dashed=False, label=None, lx=None, ly=None, marker="m"):
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}" marker-end="url(#{marker})" {"stroke-dasharray=\"6 5\"" if dashed else ""}/>')
    if label:
        add(f'<text x="{lx if lx is not None else (x1+x2)/2}" y="{ly if ly is not None else (y1+y2)/2 - 6}" font-size="12.5" fill="{muted}" text-anchor="middle">{label}</text>')
def text(x, y, s, size=13.5, color=ink, weight="400", anchor="start"):
    add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{s}</text>')

add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Segoe UI, Helvetica, Arial, sans-serif">')
add('<defs><marker id="m" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#9ca3af"/></marker>'
    '<marker id="mr" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#be123c"/></marker>'
    '<marker id="mt" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#0f766e"/></marker></defs>')
add(f'<rect width="{W}" height="{H}" fill="#fafaf9"/>')
text(40, 44, "Erbil Coca-Cola hybrid plant — as understood on 14 Oct 2026, and the proposed modifications", 24, ink, "700")
text(40, 68, "Left: power and data today (from the InteliNeo and SAU exports). Right: changes in the order I suggest, each one only after the previous one is proven on data.", 14, muted)

# ------------------------------------------------------------------ LEFT: today
LX = 40
add(f'<rect x="{LX}" y="90" width="900" height="1060" rx="14" fill="#ffffff" stroke="#e5e7eb" stroke-width="2"/>')
text(LX+20, 122, "TODAY", 18, ink, "700")
text(LX+100, 122, "gensets carry the plant, PV curtailed to keep them ≥ 25 % loaded, grid used when available", 13.5, muted)

# bus
BY = 560
add(f'<rect x="{LX+40}" y="{BY}" width="820" height="12" fill="{ink}"/>')
text(LX+40, BY-8, "400 V plant bus — measured 417 V / 50.01 Hz", 13.5, ink, "700")

# grid
box(LX+40, 160, 250, 150, "Government grid", ["3 incomers, MAIN 1-3 (InteliMains 210 G2)", "mains import today: 0 kW (outage)", "each unit reads ~1.47 MW on the bus;", "PLC sums them to 4.4 MW (looks wrong)"], c_grid)
arrow(LX+165, 310, LX+165, BY-2, c_grid, 3)
# gensets
box(LX+330, 160, 250, 150, "5 diesel gensets", ["IG4 200 controllers, CAN bus", "today: Gen 1, 2, 3, 5 loaded = 1,482 kW", "= 38 % of 3,851 kW running capacity", "not in power management (In PM = 0)"], c_gen)
arrow(LX+455, 310, LX+455, BY-2, c_gen, 3)
# pv
box(LX+600, 160, 262, 150, "PV 3.2 MW", ["10 x Sineng SP-320K-H, 320 kW, 12 strings", "800 V side, two windings (5 + 5)", "PV CB1 / PV CB2 switched by the Neo", "allowed output: curtailment 95.9 % (night)"], c_pv, size=12.5)
arrow(LX+740, 310, LX+740, BY-2, c_pv, 3)
# SAU + neo data
box(LX+620, 340, 242, 110, "SAU-100 gateway", ["RS485 to the inverters (99-100 % ok)", "Modbus TCP server :502", "unit 245 = whole array totals"], c_pv, dashed=True, title_size=15, size=12.5)
arrow(LX+740, 340, LX+740, 312, c_pv, 2, True)

# loads
box(LX+110, 640, 262, 120, "Plant load", ["~1.5 MW this evening, ~1.9 MW peak", "bottling lines, utilities"], ink, size=12.5)
arrow(LX+241, BY+12, LX+241, 640, line, 3)
box(LX+390, 640, 196, 120, "UPS 1 and UPS 2", ["critical loads behind the UPS", "Neo outputs UPS ON / OFF", "thresholds 70/35 % and 80/35 %", "today: both OFF active"], c_ups, size=12.5)
arrow(LX+488, BY+12, LX+488, 640, c_ups, 3)

# neo
box(LX+40, 800, 400, 150, "InteliNeo 5500 — site master (AUTO)", ["CAN: 5 gensets + 3 mains + itself = 9 (limit 8 → red alarm)", "reads PV totals from the SAU (Modbus TCP) → curtails PV", "controls PV CB1/2 and UPS1/2 through the PLC program", "Modbus server :502 unit 1 — read-only table, no setpoints", "PV energy statistics implausible (yield scaling wrong)"], c_neo)
arrow(LX+70, BY+12, LX+70, 800, c_neo, 2, True)
add(f'<text transform="translate({LX+62},{BY+140}) rotate(-90)" font-size="12.5" fill="{muted}" text-anchor="end">measures the bus</text>')
add(f'<path d="M{LX+300},800 L{LX+300},785 L{LX+600},785 L{LX+600},455 L{LX+618},455" fill="none" stroke="{c_neo}" stroke-width="2" stroke-dasharray="6 5" marker-end="url(#mt)"/>')
add(f'<text transform="translate({LX+612},{BY+120}) rotate(-90)" font-size="12.5" fill="{muted}" text-anchor="middle">PV request (limit)</text>')

# PC + collector
box(LX+480, 800, 380, 150, "Plant PC 192.168.0.200 — data collector", ["reads Neo + SAU once per hour, read-only, 3 requests", "SQLite on the PC, dashboard :8080, Excel / CSV", "Modbus TCP server :502 for the CCI platform (VPN)", "305 tags: 8 live from the Neo, 297 await the SAU map", "to be installed at the next site session"], c_pc)
arrow(LX+480, 875, LX+440, 875, c_pc, 2, True)
arrow(LX+740, 800, LX+740, 452, c_pc, 2, True, "hourly", LX+770, 640)
box(LX+480, 990, 380, 70, "CCI / DT Turkey platform", ["polls the plant PC every 30-60 min over VPN"], c_cci, dashed=True, title_size=15)
arrow(LX+670, 990, LX+670, 952, c_cci, 2, True)
text(LX+40, 1010, "Nothing in the collector writes to any device.", 13.5, c_neo, "700")
text(LX+40, 1032, "All numbers on this side are from one evening's snapshot (17:28 plant time).", 12.5, muted)

# ------------------------------------------------------------------ RIGHT: proposed
RX = 980
add(f'<rect x="{RX}" y="90" width="880" height="1060" rx="14" fill="#ffffff" stroke="#e5e7eb" stroke-width="2"/>')
text(RX+20, 122, "PROPOSED MODIFICATIONS — in this order", 18, ink, "700")
text(RX+20, 146, "each step is small, reversible, and justified by the data of the step before", 13.5, muted)

steps = [
 ("0", "Now — no change on the plant", c_pc, "weeks 1-4",
  ["install the collector; CHECK the Neo values against the screen; get the SAU point table",
   "4 weeks of hourly data: load profile, gensets running vs load, PV allowed vs available, grid hours",
   "ask the site: why 4 gensets manually, what 'Production Mode' and user buttons 1-4 do",
   "read the Neo archive: PLC program (PV request formula, UPS logic), Modbus-master map of the SAU"]),
 ("1", "Neo configuration — read-only additions (low risk)", c_neo, "after step 0",
  ["extend the Neo Modbus table: PV logger values, the 3 mains readings, PLC site load, statistics",
   "fix the PV-logger register scaling so daily / total yield become real numbers",
   "ask ComAp about the 8-controller CAN limit (firmware or layout) — clears the permanent alarm",
   "written by the plant with InteliConfig, from a file we prepare and test in the ComAp emulator"]),
 ("2", "Operation — gensets back under power management (no code change)", c_gen, "after step 1",
  ["run the gensets in AUTO with the existing reserves (start 300 / stop 400 kW), 2 engines for 1.5 MW",
   "then tune Gen P Min (25 %) and the solar coverage ratio against the recorded PV ramps",
   "expected: fewer engine hours, engines at 70-80 % load, PV allowed up from ~0.9 MW towards 2+ MW",
   "measured by the collector before/after; the plant can go back to manual at any time"]),
 ("3", "UPS logic — the two UPS as fast load support (medium risk)", c_ups, "after step 2, with the PLC program",
  ["today the UPS outputs shed critical loads when a genset is loaded above 70-80 % (PLC thresholds)",
   "proposal: also switch the UPS to battery for the seconds after a PV drop or a grid loss, so the",
   "gensets see a smaller load step → the spinning reserve can be reduced → fewer engines, more PV",
   "built in the archive copy, simulated in the ComAp emulator, tested on site in MAN with one UPS first"]),
]
y = 170
for n, title, color, when, lines in steps:
    h = 60 + len(lines)*20
    add(f'<rect x="{RX+20}" y="{y}" width="840" height="{h}" rx="10" fill="#ffffff" stroke="{color}" stroke-width="2.5"/>')
    add(f'<circle cx="{RX+52}" cy="{y+30}" r="18" fill="{color}"/>')
    text(RX+52, y+36, n, 17, "#ffffff", "700", "middle")
    text(RX+84, y+36, title, 16.5, color, "700")
    text(RX+840, y+36, when, 12.5, muted, "400", "end")
    for i, l in enumerate(lines):
        text(RX+84, y+64+i*20, "• " + l, 13.5)
    y += h + 16

# footer: how each change happens
add(f'<rect x="{RX+20}" y="{y}" width="840" height="128" rx="10" fill="#f3f4f6" stroke="#e5e7eb"/>')
text(RX+36, y+28, "How every modification is done", 15, ink, "700")
foot = ["we engineer it on a copy of the archive and prove it in the ComAp emulator + the collector's simulator;",
        "the plant writes it to the controller (never us, never remotely), with a saved archive to roll back;",
        "the collector measures before and after, so each step is judged on fuel, engine hours and PV kWh — not on opinion."]
for i, l in enumerate(foot):
    text(RX+36, y+54+i*20, "• " + l, 13.5)
add('</svg>')
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
open(os.path.join(ROOT, "docs", "diagrams", "plant_today_and_next.svg"), "w").write("\n".join(out))
print("svg written")
