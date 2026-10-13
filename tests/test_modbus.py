"""Modbus adapter against a local fake server: block planning, decoding, failure handling."""

import threading
import time

import pytest
from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import ModbusTcpServer

from plantcollector.adapters.modbus_tcp import ModbusTcpAdapter, _decode
from plantcollector.registry import Device, PointDefinition, Quality

PORT = 15020


@pytest.fixture(scope="module")
def server():
    regs = [0] * 200
    regs[0], regs[1] = 0x0000, 0x1388          # int32 big-endian 5000 -> *0.1 = 500.0 kW
    regs[2] = 1                                # running
    regs[7] = 0xFFF6                           # int16 -10
    regs[20] = 0b101                           # bits 0 and 2 set
    store = ModbusDeviceContext(hr=ModbusSequentialDataBlock(1, regs), ir=ModbusSequentialDataBlock(1, regs))   # pymodbus data blocks are 1-based
    ctx = ModbusServerContext(devices={1: store}, single=False)
    import asyncio

    holder = {}

    async def serve():
        holder["srv"] = ModbusTcpServer(ctx, address=("127.0.0.1", PORT))
        await holder["srv"].serve_forever()

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=lambda: loop.run_until_complete(serve()), daemon=True)
    t.start()
    time.sleep(1.0)
    yield holder
    loop.call_soon_threadsafe(lambda: asyncio.ensure_future(holder["srv"].shutdown()))


def device(port=PORT, unit=1) -> Device:
    return Device(id="t", kind="genset", adapter="modbus_tcp", connection={"host": "127.0.0.1", "port": port, "unit_id": unit, "timeout": 1},
                  points=[PointDefinition(id="power_kw", unit="kW", raw={"address": 0, "kind": "int32", "scale": 0.1}),
                          PointDefinition(id="running", type="boolean", raw={"address": 2, "kind": "uint16"}),
                          PointDefinition(id="temp", raw={"address": 7, "kind": "int16"}),
                          PointDefinition(id="bit2", type="boolean", raw={"address": 20, "kind": "uint16", "bit": 2}),
                          PointDefinition(id="bit1", type="boolean", raw={"address": 20, "kind": "uint16", "bit": 1}),
                          PointDefinition(id="far", raw={"address": 150, "kind": "uint16", "fn": "input"}),
                          PointDefinition(id="unmapped"),
                          PointDefinition(id="comm_error", type="boolean", raw={"derived": "comm_error"})])


def test_decode():
    assert _decode([0x0000, 0x1388], "int32", 0.1, "big") == pytest.approx(500.0)
    assert _decode([0x1388, 0x0000], "int32", 0.1, "little") == pytest.approx(500.0)
    assert _decode([0xFFF6], "int16", 1, "big") == -10
    assert _decode([0x4248, 0x0000], "float32", 1, "big") == pytest.approx(50.0)


def test_blocks_are_contiguous_and_few():
    blocks = ModbusTcpAdapter._plan_blocks(device().points)
    assert [(fn, start, count) for fn, start, count, _ in blocks] == [("holding", 0, 21), ("input", 150, 1)]


def test_read_values(server):
    ad = ModbusTcpAdapter(device())
    assert ad.connect()
    out = ad.read()
    assert out["power_kw"].value == pytest.approx(500.0) and out["power_kw"].quality == Quality.GOOD
    assert out["running"].value is True
    assert out["temp"].value == -10
    assert out["bit2"].value is True and out["bit1"].value is False
    assert out["far"].quality == Quality.GOOD
    assert out["unmapped"].quality == Quality.BAD and "no register" in out["unmapped"].error
    assert out["comm_error"].value is False
    ad.close()


def test_unreachable_device_marks_everything_bad():
    ad = ModbusTcpAdapter(device(port=15999))
    out = ad.read()
    assert all(r.quality == Quality.BAD for pid, r in out.items() if pid != "comm_error")
    assert out["comm_error"].value is True
