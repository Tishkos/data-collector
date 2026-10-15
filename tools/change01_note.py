"""
Builds the readable note for change 01 (PDF + PNG) from the two InteliConfig screenshots: sheet 10 as it is
and sheet 10 after the import. Text and figures, nothing else.

  python tools/change01_note.py BEFORE.png AFTER.png
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "changes", "change-01_comm_fail_tolerance")
FD = "/usr/share/fonts/truetype/dejavu/"
SERIF = FD + "DejaVuSerif.ttf"
SERIF_B = FD + "DejaVuSerif-Bold.ttf"
SERIF_I = FD + "DejaVuSerif-Italic.ttf"
SANS = FD + "DejaVuSans.ttf"
SANS_B = FD + "DejaVuSans-Bold.ttf"
INK = (34, 34, 34)
GREY = (105, 105, 105)
RED = (176, 32, 32)
PAPER = (253, 252, 249)
A4P = (1240, 1754)
A4L = (1754, 1240)
MARGIN = 96


def font(path, size):
    return ImageFont.truetype(path, size)


def wrap(text, f, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if f.getlength(t) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


class Page:
    def __init__(self, size):
        self.im = Image.new("RGB", size, PAPER)
        self.d = ImageDraw.Draw(self.im)
        self.y = MARGIN

    def text(self, s, f, color=INK, width=None, gap=10, x=MARGIN, after=18):
        width = width or (self.im.width - 2 * MARGIN)
        for line in wrap(s, f, width):
            self.d.text((x, self.y), line, fill=color, font=f)
            self.y += f.size + gap
        self.y += after

    def heading(self, s, f, color=INK, after=14):
        self.d.text((MARGIN, self.y), s, fill=color, font=f)
        self.y += f.size + after

    def rule(self, after=22):
        self.d.line([(MARGIN, self.y), (self.im.width - MARGIN, self.y)], fill=(200, 196, 188), width=2)
        self.y += after



TITLE = "The PV logger comm fail: what the controller does today, and the small change that stops it"
SUB = "Erbil plant, InteliNeo 5500 master, PLC sheet 10. Written from the archive and history exported on 14 October 2026."

STORY = [
    ("", "If you open the controller history for any day of the last two weeks you will find the lines. \"PV Logger group 1 "
         "Comm Fail\", \"PV Logger group 2 Comm Fail\", sometimes two in the same minute, sometimes thirty in a morning. "
         "On their own they look harmless: a warning that the master missed one reply from the solar logger. They are not "
         "harmless, and the reason sits in one corner of the PLC program."),
    ("", "Every tenth of a second the controller works out how much it wants from the solar plant and writes that number "
         "to the SAU, the logger that commands the ten inverters. The last stage of that calculation is sheet 10. It keeps "
         "the request within 100 kW of what the inverters are already giving, smooths it with a ramp, and sends it off. "
         "All of that is sensible. But the sheet also has a small switch, Item 76, with two positions. In the normal "
         "position the request passes through. In the other position the output is a fixed zero. And what flips the "
         "switch is the comm fail signal, from either of the two sessions, on any single missed reply."),
    ("", "So this is what happens, every time. One reply is lost and the switch flips; the order to the solar plant becomes "
         "zero. The inverters obey within a few seconds. The gensets, which a moment ago were idling at 25 percent, "
         "suddenly carry the whole plant. The UPS logic sees the jump and sheds the UPS breakers. Then the reply comes "
         "back, the switch flips back, and the request starts climbing again, but it may never be more than 100 kW above "
         "what the inverters are producing, so the climb takes about five minutes. We measured it on the 14th: roughly "
         "300 kW a minute."),
    ("", "The part that is hard to accept is that the zero never reaches the logger while the link is down. The link is "
         "down; nothing reaches it. The zero is delivered the moment the link is back, when there is nothing left to "
         "protect. We pay, every time, for a protection that cannot act."),
    ("The change", "We leave the switch, we leave the ramp, we leave every protection on the sheet. We change only what sits on the "
         "switch's second input: instead of a fixed zero, it is wired to the switch's own output. Now, when a reply is "
         "missed, the switch simply repeats the last value it sent. The solar plant keeps producing what it was "
         "producing, the gensets stay where they were, and the UPS logic has nothing to react to. When the link is back "
         "the fresh request takes over, and because the plant never dropped, the request is already where it should be."),
    ("", "Two small things come with it. The block in front of the switch, Item 68, used to flip it also when the request "
         "was zero or below. That was a harmless guard while the second position was zero anyway; with a hold it would "
         "be wrong, because if the controller genuinely wants zero it must get zero. So that third input comes off, and "
         "Item 68 reacts only to the two comm fail signals. And there is one new block, a five minute timer on the same "
         "signal, that raises a flag we called \"PV comm lost\". Nothing acts on it yet. It is there so the alarm list "
         "can show that the logger has been silent for five minutes, and so that later, if the plant wants it, sheet 3 "
         "can open the PV breakers on purpose instead of leaving the inverters running blind."),
    ("Is holding safe?", "While the link is down the inverters keep their last setpoint, in the old program and in the new one; the old "
         "program never had a say in that. What could go wrong in a blind window is a drop in load with the PV still at "
         "its setpoint, pushing the gensets towards reverse power. The gensets have 25 percent of their nominal in hand "
         "before that happens, 788 kW with three running, 290 kW with one, and the plant's load moves a couple of "
         "hundred kW in half an hour, not in seconds. The controller's own protections, bus, mains, anti islanding and "
         "the gensets' reverse power, do not depend on the logger at all. After five minutes the flag is up. If one "
         "genset runs alone we would set the timer shorter; 120 seconds is plenty."),
    ("What we checked", "The whole eleven sheet program was run block by block against the plant's own history before this was "
         "proposed. The new sheet gives the same outputs as the old one, to the kilowatt, whenever the link is healthy. "
         "With a lost reply it holds; with a request of zero it sends zero; after 300 seconds of silence it raises the "
         "flag. On the recorded day of 14 October the change takes three of the eleven UPS sheddings away and gives back "
         "about 350 kWh of solar. The rest of that day's trouble came from the OFF/AUTO toggles on the master, which no "
         "program can fix; that one is a conversation with the operators."),
]

LOADING = [
    ("Loading it", "On a copy of the archive, never on the live controller. In the PLC editor open the sheet \"PV req step "
         "protection\" and import the file sheet10_PV_req_step_protection_v2_const.xml. Check the three places shown on "
         "the picture: Item 68 has only the two ECU inputs, the switch has its own output on its second input, and the "
         "new Delay reads \"PV comm fail\" with 300.0 s. Add a warning on \"PV comm lost\" in the alarm list. Compile, "
         "compare with the 14 October archive (only sheet 10 should differ), save as a new archive with the date."),
    ("Testing it", "With the plant's agreement, at low load, with the data collector logging. Pull the SAU's Ethernet cable "
         "for thirty seconds: the solar must not move, one warning only. Do it three times. Then pull it for six "
         "minutes: the request stays held, \"PV comm lost\" comes up at five minutes, the UPS are not shed. Plug it "
         "back, fault reset, the solar continues. After that, watch the daily count of comm fail warnings, OFF/AUTO "
         "toggles and UPS sheddings for two weeks."),
    ("The way back", "Load the archive from 14 October, or in the editor put the constant 0.0 kW back on the switch's second "
         "input. Either takes under a minute."),
    ("Also worth doing", "In the ECU modules, raise the response timeout of both PV logger sessions from 3,000 to 5,000 ms. Once the "
         "SAU point table confirms that registers 2100 and 2101 are one setpoint, the two sessions can become one. And "
         "check whether the SAU has its own \"no command received\" fallback; if it does, it must be longer than a normal "
         "fail burst and shorter than five minutes."),
]

BEFORE_NOTES = [
    (1, "Item 68 decides when the request gets cut. It fires when either PV logger session misses a reply, and also when the request is zero or below."),
    (2, "Item 76 is the switch. Its second position is a fixed 0.0 kW. The moment Item 68 fires, that zero goes out to the logger, and the whole solar plant is told to stop."),
]
AFTER_NOTES = [
    (1, "Item 68 now reacts only to the two real signals, ECU 2 and ECU 3 comm fail. The third input is gone."),
    (2, "The same switch, now Item 77, has its own output on its second input. A missed reply makes it repeat the last value. The inverters see no drop, the gensets see no step."),
    (3, "Item 65 is new: a five minute timer on \"PV comm fail\". When it runs out it raises \"PV comm lost\", sheet output 5. For an alarm now; for sheet 3 later, if the plant agrees."),
]


def figure_page(img_path, title, notes, boxes, caption):
    p = Page(A4L)
    im = Image.open(img_path).convert("RGB")
    fig_w = p.im.width - 2 * MARGIN - 440
    s = fig_w / im.width
    fig = im.resize((fig_w, round(im.height * s)), Image.LANCZOS)
    max_h = p.im.height - 2 * MARGIN - 70
    if fig.height > max_h:
        s2 = max_h / fig.height
        fig = fig.resize((round(fig.width * s2), max_h), Image.LANCZOS)
        s *= s2
    p.heading(title, font(SERIF_B, 32), color=RED, after=18)
    oy = p.y
    p.im.paste(fig, (MARGIN, oy))
    p.d.rectangle([MARGIN, oy, MARGIN + fig.width, oy + fig.height], outline=(205, 200, 192), width=2)
    nf = font(SANS_B, 20)
    for (x0, y0, x1, y1, n) in boxes:
        X0, Y0, X1, Y1 = MARGIN + x0 * s, oy + y0 * s, MARGIN + x1 * s, oy + y1 * s
        p.d.rectangle([X0, Y0, X1, Y1], outline=RED, width=3)
        cx, cy = X0 - 14, (Y0 + Y1) / 2
        p.d.ellipse([cx - 15, cy - 15, cx + 15, cy + 15], fill=RED)
        p.d.text((cx - 6, cy - 12), str(n), fill="white", font=nf)
    nx = MARGIN + fig.width + 40
    ny = oy
    bf = font(SERIF, 21)
    for n, text in notes:
        p.d.ellipse([nx, ny + 2, nx + 30, ny + 32], fill=RED)
        p.d.text((nx + 9, ny + 5), str(n), fill="white", font=nf)
        yy = ny
        for line in wrap(text, bf, 440 - 50):
            p.d.text((nx + 42, yy), line, fill=INK, font=bf)
            yy += 30
        ny = yy + 22
    cf = font(SERIF_I, 18)
    for line in wrap(caption, cf, 440 - 10):
        p.d.text((nx, ny), line, fill=GREY, font=cf)
        ny += 26
    return p


def flow(items, first_title=None):
    """Lays (heading, paragraph) items onto as many portrait pages as needed."""
    body, head = font(SERIF, 24), font(SERIF_B, 26)
    pages, p = [], Page(A4P)
    if first_title:
        p.text(TITLE, font(SERIF_B, 36), gap=8, after=10)
        p.text(SUB, font(SERIF_I, 20), color=GREY, after=18)
        p.rule()
    bottom = A4P[1] - MARGIN - 40
    width = A4P[0] - 2 * MARGIN
    for h, para in items:
        need = (head.size + 14 if h else 0) + len(wrap(para, body, width)) * (body.size + 10) + 18
        if p.y + need > bottom:
            pages.append(p)
            p = Page(A4P)
        if h:
            p.heading(h, head, color=RED)
        p.text(para, body, gap=10, after=18)
    pages.append(p)
    return pages


def build(before_png, after_png):
    pages = flow(STORY, first_title=True)
    pages.append(figure_page(before_png, "Sheet 10 today", BEFORE_NOTES,
                             [(455, 540, 600, 562, 1), (1215, 346, 1300, 366, 2)],
                             "The sheet as it runs in the controller (archive of 14 October 2026)."))
    pages.append(figure_page(after_png, "Sheet 10 after the change", AFTER_NOTES,
                             [(462, 528, 600, 560, 1), (1138, 362, 1290, 382, 2), (330, 1565, 425, 1690, 3), (1648, 1476, 1765, 1498, 3)],
                             "After importing the modified sheet into a copy of the archive. Everything not marked is unchanged."))
    pages += flow(LOADING)
    for k, pg in enumerate(pages, 1):
        pg.d.text((MARGIN, pg.im.height - MARGIN + 30), f"{k} / {len(pages)}", fill=GREY, font=font(SANS, 16))
    pdf = os.path.join(OUT, "change-01_note.pdf")
    pages[0].im.save(pdf, "PDF", resolution=150, save_all=True, append_images=[pg.im for pg in pages[1:]])
    fig_pages = [pg for pg in pages if pg.im.size == A4L]
    fig = Image.new("RGB", (A4L[0], A4L[1] * len(fig_pages)), PAPER)
    for k, pg in enumerate(fig_pages):
        fig.paste(pg.im, (0, A4L[1] * k))
    png = os.path.join(OUT, "sheet10_before_after.png")
    fig.save(png)
    return pdf, png


if __name__ == "__main__":
    print(build(sys.argv[1], sys.argv[2]))
