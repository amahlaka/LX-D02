"""A plain bleak connection to an LX-D02, for use outside Home Assistant."""
import logging

from bleak import BleakClient, BleakScanner

from . import protocol
from .protocol import PrinterError

log = logging.getLogger("lxd02")


async def find_printer(timeout: float = 10.0) -> str:
    """Return the address of the first LX-D02 found nearby."""
    dev = await BleakScanner.find_device_by_filter(
        lambda d, adv: (adv.local_name or d.name or "").startswith(protocol.NAME_PREFIXES),
        timeout=timeout,
    )
    if dev is None:
        raise PrinterError("No LX-D02 found. Is it on and disconnected from the phone app?")
    return dev.address


class LXD02(protocol.LXD02):
    """Connects by address (or scans) and unlocks the printer on entry."""

    def __init__(self, address: str | None = None):
        super().__init__(None)
        self.address = address

    async def __aenter__(self):
        if self.address is None:
            self.address = await find_printer()
        dev = await BleakScanner.find_device_by_address(self.address, timeout=15.0)
        if dev is None:
            raise PrinterError(f"{self.address} is not advertising. Is it on, and not "
                               "connected to the phone app or Home Assistant? (It powers off when idle.)")
        self.client = BleakClient(dev, timeout=20.0)
        await self.client.connect()
        try:
            await self.start()
        except BaseException:
            await self.client.disconnect()
            raise
        log.info("connected to %s", self.address)
        return self

    async def __aexit__(self, *exc):
        if self.client and self.client.is_connected:
            await self.client.disconnect()
