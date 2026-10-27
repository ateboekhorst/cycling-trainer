"""Shared Bluetooth LE plumbing for trainers and sensors."""
from __future__ import annotations

import logging
from typing import Callable

from bleak import BleakClient

log = logging.getLogger(__name__)


def uuid16(short: int) -> str:
    """Expand a 16-bit Bluetooth SIG UUID to its full 128-bit string form."""
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


class BleDevice:
    """A connectable BLE peripheral; subclasses subscribe to their characteristics in `_setup`."""

    def __init__(self, address: str, on_disconnect: Callable[[], None]):
        self.address = address
        self._on_disconnect = on_disconnect
        self._client: BleakClient | None = None

    async def connect(self) -> None:
        client = BleakClient(self.address, disconnected_callback=self._handle_disconnect, timeout=20)
        await client.connect()
        self._client = client
        try:
            await self._setup(client)
        except Exception:
            await self.disconnect()
            raise
        log.info("Connected to %s", self.address)

    async def disconnect(self) -> None:
        # Clearing _client first marks the disconnect as intentional (see _handle_disconnect).
        client, self._client = self._client, None
        if client is not None and client.is_connected:
            await client.disconnect()

    async def _setup(self, client: BleakClient) -> None:
        raise NotImplementedError

    def _handle_disconnect(self, _client: BleakClient) -> None:
        if self._client is None:
            return
        log.warning("Lost connection to %s", self.address)
        self._client = None
        self._on_disconnect()
