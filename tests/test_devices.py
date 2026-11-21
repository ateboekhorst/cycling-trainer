import asyncio

import pytest

from trainer.devices import manager
from trainer.devices.manager import _Slot


class FakeState:
    def __init__(self):
        self.states = []

    def emit(self, state):
        self.states.append(state)


class FakeDevice:
    """Not found for the first `failures` attempts, then connects."""

    attempts = 0

    def __init__(self, failures, on_disconnect):
        self.failures = failures
        self.on_disconnect = on_disconnect
        self.connected = False

    async def connect(self):
        FakeDevice.attempts += 1
        if FakeDevice.attempts <= self.failures:
            raise OSError("Device not found")
        self.connected = True

    async def disconnect(self):
        self.connected = False


@pytest.fixture(autouse=True)
def fast_retry(monkeypatch):
    monkeypatch.setattr(manager, "RECONNECT_DELAY", 0.01)
    FakeDevice.attempts = 0


def make_slot(failures):
    loop = asyncio.get_running_loop()
    state = FakeState()
    slot = _Slot(loop, lambda address, lost: FakeDevice(failures, lost), state)
    return slot, state


async def wait_for(condition, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


def test_keeps_searching_until_device_is_found():
    async def run():
        slot, state = make_slot(failures=3)
        await slot.connect("AA:BB")
        await wait_for(lambda: slot.device is not None)
        assert FakeDevice.attempts == 4
        assert state.states[:2] == ["disconnected", "connecting"]
        assert state.states[-1] == "connected"
        assert "searching" in state.states
        await slot.disconnect()

    asyncio.run(run())


def test_disconnect_stops_searching():
    async def run():
        slot, state = make_slot(failures=10**6)
        await slot.connect("AA:BB")
        await wait_for(lambda: "searching" in state.states)
        await slot.disconnect()
        attempts = FakeDevice.attempts
        await asyncio.sleep(0.1)
        assert FakeDevice.attempts == attempts
        assert state.states[-1] == "disconnected"
        assert slot.wanted is None

    asyncio.run(run())


def test_reconnects_after_losing_connection():
    async def run():
        slot, state = make_slot(failures=0)
        await slot.connect("AA:BB")
        await wait_for(lambda: slot.device is not None)
        lost = slot.device
        lost.on_disconnect()
        await wait_for(lambda: slot.device is not None and slot.device is not lost)
        assert "reconnecting" in state.states
        assert state.states[-1] == "connected"
        await slot.disconnect()

    asyncio.run(run())
