"""Protocol tests for the LX-D02 driver, against a simulated printer."""
import asyncio

import pytest
from PIL import Image

from lxd02 import protocol
from lxd02.protocol import LXD02, PrinterError

MAC = bytes.fromhex("c00000000048")


class SimulatedPrinter:
    """Answers like the real printer (replies captured from an LX-D02)."""

    def __init__(self, hold_at: int | None = None, no_paper: bool = False, bad_crc: bool = False):
        self.notify = None
        self.written: list[bytes] = []
        self.packets: list[bytes] = []
        self.hold_at = hold_at
        self.no_paper = no_paper
        self.bad_crc = bad_crc
        self.total = 0

    async def start_notify(self, uuid, callback):
        assert uuid == protocol.NOTIFY_UUID
        self.notify = callback

    def _reply(self, data: bytes):
        asyncio.get_running_loop().call_soon(self.notify, None, bytearray(data.ljust(12, b"\x00")))

    def _status(self):
        self._reply(bytes([0x5A, 0x02, 0x57, int(self.no_paper), 0, 0, 0, 3, 0x0D, 0x4C, 0, 0x40]))

    async def write_gatt_char(self, uuid, data, response=False):
        assert uuid == protocol.WRITE_UUID and response is False
        data = bytes(data)
        self.written.append(data)
        if data[0] == 0x55:
            self.packets.append(data)
            idx = int.from_bytes(data[1:3], "big")
            if idx == self.hold_at:
                self.hold_at = None
                self._reply(b"\x5a\x08")
                self._reply(b"\x5a\x05" + idx.to_bytes(2, "big"))  # resend this one
            elif idx == self.total - 1:
                self._reply(b"\x5a\x06" + self.total.to_bytes(2, "big") + b"\x01")
            return
        cmd = data[1]
        if cmd == 0x01:
            self._reply(b"\x5a\x01\x00\x03" + MAC)
            self._status()
        elif cmd == 0x0A:
            low, _ = protocol.handshake_crcs(data[2:12], MAC)
            self._reply(b"\x5a\x0a" + (bytes(10) if self.bad_crc else low))
        elif cmd == 0x0B:
            _, high = protocol.handshake_crcs(self._challenge(), MAC)
            self._reply(b"\x5a\x0b" + bytes([1 if data[2:12] == high else 0]))
        elif cmd == 0x0C:
            self._reply(data[:3])
        elif cmd == 0x04:
            self.total = int.from_bytes(data[2:4], "big")
            if data[4] == 0:
                self._reply(b"\x5a\x07\x14")
            self._reply(data[:6])

    def _challenge(self) -> bytes:
        return next(w for w in reversed(self.written) if w[1] == 0x0A)[2:12]

    @property
    def data(self) -> bytes:
        by_index = {int.from_bytes(p[1:3], "big"): p[3:99] for p in self.packets}
        return b"".join(by_index[i] for i in sorted(by_index))


@pytest.fixture(autouse=True)
def fast_packets(monkeypatch):
    monkeypatch.setattr(protocol, "PACKET_INTERVAL", 0)


def test_crc16_xmodem_check_value() -> None:
    assert protocol.crc16_xmodem(b"123456789") == 0x31C3


def test_build_packets_pads_and_numbers() -> None:
    packets = protocol.build_packets(bytes(range(200)))
    assert [len(p) for p in packets] == [100, 100, 100]
    assert packets[2][:3] == b"\x55\x00\x02" and packets[2][-1] == 0
    assert packets[2][3:11] == bytes(range(192, 200))
    assert packets[2][11:99] == bytes(88)


def test_rasterize_black_is_one() -> None:
    img = Image.new("L", (384, 2), 255)
    img.putpixel((0, 0), 0)
    data = protocol.rasterize(img, dither=False)
    assert len(data) == 96 and data[0] == 0x80 and not any(data[1:])
    with pytest.raises(ValueError, match="384 px"):
        protocol.rasterize(Image.new("L", (100, 10)))


def test_status_parse() -> None:
    st = protocol.Status.parse(bytes.fromhex("5a02570101010103 0d4c0040"))
    assert (st.battery, st.no_paper, st.charging, st.overheated, st.low_voltage, st.energy) == \
        (0x57, True, True, True, True, 3)


async def test_handshake_and_print() -> None:
    sim = SimulatedPrinter()
    printer = LXD02(sim)
    await printer.start()
    assert printer.mac_address == "C0:00:00:00:00:48"
    assert (await printer.wait_status()).battery == 0x57

    img = Image.new("L", (384, 9), 0)
    await printer.print_image(img, energy=4, feed_mm=1)
    commands = [w[:2] for w in sim.written if w[0] == 0x5A]
    assert commands == [b"\x5a\x01", b"\x5a\x0a", b"\x5a\x0b", b"\x5a\x0c", b"\x5a\x04", b"\x5a\x04"]
    assert sim.written[3] == b"\x5a\x0c\x04"
    start, end = (w for w in sim.written if w[:2] == b"\x5a\x04")
    assert start == b"\x5a\x04\x00\x09\x00\x00" and end == b"\x5a\x04\x00\x09\x01\x00"
    # 9 black rows + 8 blank feed rows, padded to whole packets of 2 rows
    assert sim.data == b"\xff" * 48 * 9 + bytes(48 * 9)


async def test_print_resends_after_hold() -> None:
    sim = SimulatedPrinter(hold_at=3)
    printer = LXD02(sim)
    await printer.start()
    await printer.print_raw(bytes(range(256)) * 6)
    indexes = [int.from_bytes(p[1:3], "big") for p in sim.packets]
    assert indexes.count(3) == 2
    assert sim.data[:1536] == bytes(range(256)) * 6


async def test_feed_counts_the_automatic_feed() -> None:
    sim = SimulatedPrinter()
    printer = LXD02(sim)
    await printer.start()
    await printer.feed(12)  # the printer adds 10 mm itself, so send 2 mm = 16 rows
    assert len(sim.data) == 16 * 48 and not any(sim.data)
    sim.packets.clear()
    await printer.feed(4)  # below the automatic feed: send the smallest job, 1 packet
    assert len(sim.packets) == 1


async def test_handshake_rejected() -> None:
    printer = LXD02(SimulatedPrinter(bad_crc=True))
    with pytest.raises(PrinterError, match="checksum"):
        await printer.start()


async def test_out_of_paper_refused() -> None:
    printer = LXD02(SimulatedPrinter(no_paper=True))
    await printer.start()
    await printer.wait_status()
    with pytest.raises(PrinterError, match="out of paper"):
        await printer.print_raw(bytes(96))
