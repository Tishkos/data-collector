"""The passive session: the SAU is pinged and captured, never read; the capture is read back with tshark."""

import os
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from plantcollector import capture as cap
from plantcollector.session import Session

from test_session import _serve

NEO_PORT = 15051

SAU, MASTER, PC = "192.168.0.128", "192.168.0.112", "192.168.0.50"


# ---- a tiny pcap writer: Ethernet / IPv4 / TCP or ICMP, checksums left at zero (tshark does not check them) ----
def _ip(a):
    return socket.inet_aton(a)


def _pkt(src, dst, proto, payload):
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 1, 0, 64, proto, 0, _ip(src), _ip(dst))
    return b"\x00\x11\x22\x33\x44\x55" + b"\x66\x77\x88\x99\xaa\xbb" + b"\x08\x00" + ip + payload


def _tcp(src, dst, sport, dport, seq, ack, data=b"", flags=0x18):
    hdr = struct.pack("!HHIIBBHHH", sport, dport, seq, ack, 5 << 4, flags, 8192, 0, 0)
    return _pkt(src, dst, 6, hdr + data)


def _icmp(src, dst, typ):
    return _pkt(src, dst, 1, struct.pack("!BBHHH", typ, 0, 0, 1, 1) + b"x" * 8)


def _mb_req(tid, fc=3, addr=1000, qty=2):
    return struct.pack("!HHHB", tid, 0, 6, 245) + struct.pack("!BHH", fc, addr, qty)


def _mb_resp(tid, fc=3, exc=None):
    if exc:
        return struct.pack("!HHHB", tid, 0, 3, 245) + struct.pack("!BB", fc | 0x80, exc)
    return struct.pack("!HHHB", tid, 0, 7, 245) + struct.pack("!BB", fc, 4) + b"\x00\x01\x00\x02"


def write_pcap(path, packets):
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for t, data in packets:
            f.write(struct.pack("<IIII", int(t), int(round((t - int(t)) * 1e6)), len(data), len(data)) + data)


def _stall_capture(path, t0):
    """Two master sessions. Group 2 (port 61416) polls every 0.5 s; at t0 + 10 s its request is acknowledged by the
    SAU's TCP in 30 ms and answered 2.5 s later with exception 06. Group 1 (port 61415) asks at t0 + 10.01 s and
    gets its answer 2.49 s later. Every other request is answered in 2 ms. A ping from this PC at t0 + 3 s."""
    pk = []
    seq = {61416: [1, 1], 61415: [1, 1]}

    def ask(t, port, tid, delay, exc=None, ack_at=None):
        m, sv = seq[port]
        req = _mb_req(tid)
        pk.append((t, _tcp(MASTER, SAU, port, 502, m, sv, req)))
        m += len(req)
        if ack_at is not None:
            pk.append((t + ack_at, _tcp(SAU, MASTER, 502, port, sv, m, b"", flags=0x10)))
        resp = _mb_resp(tid, exc=exc)
        pk.append((t + delay, _tcp(SAU, MASTER, 502, port, sv, m, resp)))
        seq[port] = [m, sv + len(resp)]

    t = t0
    tid = 100
    while t < t0 + 20:
        if abs(t - (t0 + 10)) < 1e-6:
            ask(t, 61416, tid, 2.5, exc=6, ack_at=0.030)
            ask(t + 0.01, 61415, 9000, 2.49)
            t += 3.0
        else:
            ask(t, 61416, tid, 0.002)
            t += 0.5
        tid += 1
    pk.append((t0 + 3, _icmp(PC, SAU, 8)))
    pk.append((t0 + 3.001, _icmp(SAU, PC, 0)))
    write_pcap(path, sorted(pk, key=lambda p: p[0]))


TSHARK = cap.find_tool("tshark")


@pytest.mark.skipif(not TSHARK, reason="tshark (Wireshark) not installed here")
def test_capture_shows_the_request_reached_the_sau_and_the_answer_came_late(tmp_path):
    p = str(tmp_path / "sau_00001_20261020093000.pcap")
    t0 = datetime(2026, 10, 20, 6, 33, 0, tzinfo=timezone.utc).timestamp()
    _stall_capture(p, t0)
    txs = cap.transactions([p], SAU, TSHARK)
    assert len(txs) == 36
    stall = [x for x in txs if x["tid"] == "120"][0]
    other = [x for x in txs if x["tid"] == "9000"][0]
    assert other["exc"] is None and 2400 < other["ans_ms"] < 2600
    assert stall["exc"] == 6 and 2400 < stall["ans_ms"] < 2600 and stall["ack_ms"] < 50
    v = cap.window_verdict(txs, datetime.fromtimestamp(t0 + 11.5, timezone.utc), datetime.fromtimestamp(t0 + 15, timezone.utc), MASTER)
    assert "exception 06" in v and "acknowledged 1 of them within 30 ms" in v and "software answered late" in v
    calm = cap.window_verdict(txs, datetime.fromtimestamp(t0 + 4, timezone.utc), datetime.fromtimestamp(t0 + 5, timezone.utc), MASTER)
    assert "all answered" in calm
    oth = cap.other_traffic([p], SAU, TSHARK)
    assert {o["what"] for o in oth} == {"ping"} and {o["peer"] for o in oth} == {PC}
    fail = dict(session="group 1", start=datetime.fromtimestamp(t0 + 11.5, timezone.utc), end=datetime.fromtimestamp(t0 + 15, timezone.utc))
    text = "\n".join(cap.report_section([p], [fail], SAU, MASTER, TSHARK))
    assert f"{MASTER} (36 requests)." in text and "software answered late" in text and "1 x code 6" in text


@pytest.mark.skipif(not TSHARK, reason="tshark (Wireshark) not installed here")
def test_without_a_mirror_the_report_says_so(tmp_path):
    p = str(tmp_path / "sau_00001_20261020093000.pcap")
    t0 = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc).timestamp()
    write_pcap(p, [(t0 + k, _icmp(PC, SAU, 8)) for k in range(5)])
    fail = dict(session="group 1", start=datetime.fromtimestamp(t0 + 2, timezone.utc), end=datetime.fromtimestamp(t0 + 4, timezone.utc))
    text = "\n".join(cap.report_section([p], [fail], SAU, MASTER, TSHARK))
    assert "does not mirror the SAU's port" in text and "no master requests in the capture" in text


def test_report_without_tshark_still_names_the_files(tmp_path):
    p = tmp_path / "sau_00001_20261020093000.pcapng"
    p.write_bytes(b"x")
    text = "\n".join(cap.report_section([str(p)], [], SAU, MASTER, None))
    assert "tshark (Wireshark) was not found" in text and "modbus.exception_code == 6" in text


def test_keep_copies_the_ring_files_around_a_fail(tmp_path):
    ring, ev = tmp_path / "ring", tmp_path / "ev"
    ring.mkdir()
    base = datetime(2026, 10, 20, 8, 0)                     # the PC's local time, as dumpcap names the files
    paths = []
    for k in range(4):
        start = base + timedelta(hours=k)
        p = ring / f"sau_{k + 1:05d}_{start.strftime('%Y%m%d%H%M%S')}.pcapng"
        p.write_bytes(b"data" * (k + 1))
        end = (start + timedelta(hours=1)).astimezone().timestamp() if k < 3 else time.time()
        os.utime(p, (end, end))
        paths.append(p)
    files = cap.ring_files(str(ring))
    assert [f["num"] for f in files] == [1, 2, 3, 4]
    t_fail = files[1]["start"] + timedelta(minutes=30)       # in the middle of file 2
    copied, complete = cap.keep(str(ring), str(ev), t_fail, t_fail + timedelta(seconds=12))
    assert [os.path.basename(c) for c in copied] == [paths[1].name] and complete
    edge = files[2]["start"] + timedelta(seconds=20)         # 20 s into file 3: file 2 holds the minute before
    copied, complete = cap.keep(str(ring), str(ev), edge, edge + timedelta(seconds=5))
    assert sorted(os.path.basename(c) for c in copied) == [paths[2].name] and complete   # file 2 was already kept
    assert os.path.exists(ev / paths[1].name)
    late = files[3]["start"] + timedelta(seconds=5)         # inside the file still being written
    copied, complete = cap.keep(str(ring), str(ev), late, late + timedelta(seconds=5))
    assert not complete


def test_pick_interface_matches_the_adapter_towards_the_sau(monkeypatch):
    monkeypatch.setattr(cap, "local_ip_for", lambda host: PC)
    monkeypatch.setattr(cap, "interface_alias", lambda ip: "Ethernet 3" if ip == PC else None)
    monkeypatch.setattr(cap, "_run", lambda cmd, timeout=20.0: "1. \\Device\\NPF_{AAA} (Local Area Connection* 8)\n"
                                                                "2. \\Device\\NPF_{BBB} (Ethernet 3)\n3. \\Device\\NPF_{CCC} (Wi-Fi 2)\n")
    iface, why = cap.pick_interface("dumpcap", SAU)
    assert iface == "2" and "Ethernet 3" in why and PC in why
    iface, why = cap.pick_interface("dumpcap", SAU, wanted="Wi-Fi 2")
    assert iface == "3"
    monkeypatch.setattr(cap, "interface_alias", lambda ip: None)
    iface, why = cap.pick_interface("dumpcap", SAU)
    assert iface is None and "no adapter name" in why


def test_dumpcap_command_is_a_ring_filtered_to_the_sau(tmp_path):
    c = cap.Capture("dumpcap", "2", SAU, str(tmp_path), file_s=3600, file_mb=100, files=48)
    cmd = c.command()
    assert cmd[:5] == ["dumpcap", "-i", "2", "-f", f"host {SAU}"]
    assert "duration:3600" in cmd and "filesize:100000" in cmd and "files:48" in cmd and "-w" in cmd


# ---- the session never touches the SAU ---------------------------------------------------------------------
class _Counter:
    """A TCP listener standing in for the SAU: counts every connection anyone opens to it."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.accepts = 0
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.accepts += 1
            c.close()


@pytest.fixture(scope="module")
def master():
    import asyncio
    neo = [0] * 1400
    neo[1040] = 1000
    h = _serve(NEO_PORT, neo)
    yield h
    h["loop"].call_soon_threadsafe(lambda: asyncio.ensure_future(h["srv"].shutdown()))


def test_passive_session_never_connects_to_the_sau(master, tmp_path, monkeypatch):
    from plantcollector import trace
    monkeypatch.setattr(trace, "ping", lambda host, timeout_s=1.0: (1, 0.4, ""))   # no ICMP in a bare container
    sau = _Counter()
    cfg = tmp_path / "site.yaml"
    cfg.write_text(f"""site: {{site_id: t, name: test, poll_interval_s: 3600}}
devices:
  - {{id: neo-01, profile: intelineo_master.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: {NEO_PORT}, unit_id: 1, timeout: 1}}}}
  - {{id: pv-array, profile: pv_array_sau.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.2, port: {sau.port}, unit_id: 245, timeout: 1}}}}
probe:
  hosts: [127.0.0.2]
""")
    data = tmp_path / "data"
    s = Session(str(cfg), str(data), hours=0.002, long_run=False, bundles_dir=str(tmp_path / "bundles"),
                mode="passive", capture=False)
    zp = s.run()
    assert sau.accepts == 0
    log = open(s.log_path, encoding="utf-8").read()
    assert "passive" in log and "no reads of the SAU" in log and "capture: off" in log and "first report done" in log
    import zipfile
    names = zipfile.ZipFile(zp).namelist()
    assert any(n.startswith("trace/neo-01_") for n in names) and not any(n.startswith("trace/pv-array_") for n in names)
    assert "probe.txt" in names and "127.0.0.2" not in zipfile.ZipFile(zp).read("probe.txt").decode()
    rep = [n for n in names if n.startswith("trace/report_")][0]
    text = zipfile.ZipFile(zp).read(rep).decode()
    assert "From the capture" in text and "No capture files kept" in text
    watch = [n for n in names if n.startswith("trace/watch_")]
    assert watch and "pv-array,ping" in zipfile.ZipFile(zp).read(watch[0]).decode()
