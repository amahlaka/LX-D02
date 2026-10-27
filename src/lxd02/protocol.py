"""Protocol for the LX-D02 ("Dolebo", Funny Print app) thermal printer.

Pure protocol code with no Home Assistant imports: part of the lxd02 package,
and copied unchanged into the Home Assistant integration (HA-LXD02). The
caller connects a bleak client; `LXD02` drives it. PROTOCOL.md in the LX-D02
project has the byte-level description.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import os
from dataclasses import dataclass

from PIL import Image, ImageOps

_LOGGER = logging.getLogger(__name__)

SERVICE_UUID = "0000ffe6-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"
NAME_PREFIXES = ("LX-D0",)

WIDTH = 384  # dots across the 58 mm roll (48 mm printable)
DOTS_PER_MM = 8
ROW_BYTES = WIDTH // 8
ROWS_PER_PACKET = 2
PACKET_DATA = ROW_BYTES * ROWS_PER_PACKET  # 96
PACKET_INTERVAL = 0.007  # seconds between data packets
# The printer advances this much blank paper by itself after every job, so the
# last printed line clears the tear bar (measured: 12 mm sent = 22 mm out).
AUTO_FEED_MM = 10
ENERGY_MIN, ENERGY_MAX = 1, 6

# Replies, keyed by the byte after 0x5A.
HARDWARE_INFO = 0x01
STATUS = 0x02
PRINT = 0x04
RETRANSMIT = 0x05
FINISHED = 0x06
COOLDOWN = 0x07
HOLD = 0x08
HANDSHAKE = 0x0A
APP_CHECK = 0x0B
ENERGY = 0x0C


class PrinterError(Exception):
    """The printer refused a command or stopped answering."""


@dataclass
class Status:
    """Unsolicited 5A 02 frame, sent every few seconds."""

    battery: int  # percent
    no_paper: bool
    charging: bool
    charged: bool
    overheated: bool
    low_voltage: bool
    energy: int
    raw: bytes

    @classmethod
    def parse(cls, raw: bytes) -> Status:
        return cls(battery=raw[2], no_paper=raw[3] != 0, charging=raw[4] == 1,
                   charged=raw[4] == 2, overheated=raw[5] != 0, low_voltage=raw[6] != 0,
                   energy=raw[7], raw=raw)


def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return crc


def handshake_crcs(challenge: bytes, mac: bytes) -> tuple[bytes, bytes]:
    """(low, high) CRC bytes for each challenge byte, as the app computes them."""
    crcs = [crc16_xmodem(bytes([c]) + mac) for c in challenge]
    return bytes(c & 0xFF for c in crcs), bytes(c >> 8 for c in crcs)


def frame(cmd: int, payload: bytes = b"", size: int = 12) -> bytes:
    body = bytes([0x5A, cmd]) + payload
    return body + bytes(max(0, size - len(body)))


def rasterize(img: Image.Image, dither: bool = True) -> bytes:
    """Pack an image WIDTH px wide into rows of 1-bit data (MSB first, 1 = black)."""
    if img.width != WIDTH:
        raise ValueError(f"image must be {WIDTH} px wide, got {img.width}")
    gray = img.convert("L")
    if dither:
        bw = ImageOps.invert(gray).convert("1")  # Floyd-Steinberg
    else:
        bw = ImageOps.invert(gray).point(lambda p: 255 if p >= 128 else 0, "1")
    return bw.tobytes()


def build_packets(data: bytes) -> list[bytes]:
    data += bytes(-len(data) % PACKET_DATA)
    return [bytes([0x55]) + i.to_bytes(2, "big") + data[o:o + PACKET_DATA] + b"\x00"
            for i, o in enumerate(range(0, len(data), PACKET_DATA))]


class LXD02:
    """Talks to a printer over an already connected bleak client."""

    def __init__(self, client) -> None:
        self.client = client
        self.mac: bytes | None = None
        self.status: Status | None = None
        # Called with each status frame (every ~3 s while connected).
        self.on_status: Callable[[Status], None] | None = None
        self._replies: asyncio.Queue[bytes] = asyncio.Queue()
        self._status_event = asyncio.Event()

    @property
    def is_connected(self) -> bool:
        return self.client is not None and self.client.is_connected

    async def start(self) -> None:
        """Subscribe to replies and unlock the printer (the app check)."""
        await self.client.start_notify(NOTIFY_UUID, self._on_notify)
        info = await self._request(frame(HARDWARE_INFO), HARDWARE_INFO)
        self.mac = info[4:10]
        challenge = bytes(b % 254 + 1 for b in os.urandom(10))
        low, high = handshake_crcs(challenge, self.mac)
        reply = await self._request(frame(HANDSHAKE, challenge), HANDSHAKE)
        if reply[2:12] != low:
            raise PrinterError("handshake failed: the printer's checksum doesn't match")
        reply = await self._request(frame(APP_CHECK, high), APP_CHECK)
        if reply[2] != 1:
            raise PrinterError(f"printer rejected the app check ({reply[2]})")

    @property
    def mac_address(self) -> str:
        return self.mac.hex(":").upper() if self.mac else ""

    def _on_notify(self, _, data: bytearray) -> None:
        data = bytes(data)
        _LOGGER.debug("<- %s", data.hex(" "))
        if data[:2] == bytes([0x5A, STATUS]):
            # Not queued: on a long-lived connection they would pile up unread.
            if len(data) >= 8:
                self.status = Status.parse(data)
                self._status_event.set()
                if self.on_status:
                    self.on_status(self.status)
            return
        self._replies.put_nowait(data)

    async def _send(self, data: bytes) -> None:
        await self.client.write_gatt_char(WRITE_UUID, data, response=False)

    async def _wait_for(self, cmd: int, timeout: float) -> bytes:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (remaining := deadline - loop.time()) > 0:
            try:
                reply = await asyncio.wait_for(self._replies.get(), remaining)
            except TimeoutError:
                break
            if reply[:2] == bytes([0x5A, cmd]):
                return reply
        raise PrinterError(f"no 0x{cmd:02x} reply from the printer")

    async def _request(self, data: bytes, cmd: int, timeout: float = 3.0) -> bytes:
        while not self._replies.empty():
            self._replies.get_nowait()
        await self._send(data)
        return await self._wait_for(cmd, timeout)

    async def wait_status(self, timeout: float = 5.0) -> Status:
        """The latest status frame (the printer sends one every ~3 s)."""
        if self.status is None:
            try:
                await asyncio.wait_for(self._status_event.wait(), timeout)
            except TimeoutError:
                raise PrinterError("the printer sent no status") from None
        return self.status

    async def set_energy(self, level: int) -> None:
        """Print darkness, 1 (light) to 6 (dark). The printer remembers it."""
        level = max(ENERGY_MIN, min(ENERGY_MAX, int(level)))
        await self._request(bytes([0x5A, ENERGY, level]), ENERGY)

    async def print_image(self, img: Image.Image, energy: int | None = None,
                          feed_mm: float = 0, dither: bool = True) -> None:
        """Print an image WIDTH px wide; `feed_mm` adds blank paper after it."""
        data = rasterize(img, dither)
        data += bytes(round(feed_mm * DOTS_PER_MM) * ROW_BYTES)
        await self.print_raw(data, energy)

    async def feed(self, length_mm: float) -> None:
        """Advance `length_mm` of blank paper, counting the printer's own
        AUTO_FEED_MM; anything up to that gives just the automatic feed."""
        rows = max(ROWS_PER_PACKET, round((length_mm - AUTO_FEED_MM) * DOTS_PER_MM))
        await self.print_raw(bytes(rows * ROW_BYTES))

    async def print_raw(self, data: bytes, energy: int | None = None) -> None:
        """Print packed rows (48 bytes per row)."""
        if self.status and self.status.no_paper:
            raise PrinterError("the printer is out of paper")
        if energy is not None:
            await self.set_energy(energy)
        packets = build_packets(data)
        total = len(packets).to_bytes(2, "big")
        await self._request(bytes([0x5A, PRINT]) + total + b"\x00\x00", PRINT)
        # Data has to follow the start ack straight away: the printer starts
        # feeding at once and prints blank paper for anything that arrives late.
        await self._stream(packets)
        await self._request(bytes([0x5A, PRINT]) + total + b"\x01\x00", PRINT)

    async def _stream(self, packets: list[bytes], timeout: float = 120.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        idx, held = 0, False
        while loop.time() < deadline:
            while not self._replies.empty():
                reply = self._replies.get_nowait()
                kind = reply[1] if len(reply) > 1 else None
                if kind == HOLD:
                    held = True
                elif kind == RETRANSMIT:
                    idx, held = int.from_bytes(reply[2:4], "big"), False
                elif kind == FINISHED:
                    return
            if self.status and self.status.no_paper:
                raise PrinterError("the printer ran out of paper")
            if held or idx >= len(packets):
                await asyncio.sleep(0.02)
                continue
            await self._send(packets[idx])
            idx += 1
            await asyncio.sleep(PACKET_INTERVAL)
        raise PrinterError("timed out waiting for the print to finish")
