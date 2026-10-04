"""The repository's mock device, answering identity checks late.

Usage: python slow_identity_mock.py PORT DELAY

ReadProperty of the wildcard device instance (the server's identity check
before a write) is answered DELAY seconds late, so a test can disconnect or
cancel while a write is still being prepared. Every WriteProperty that
arrives is reported on stdout as a "WRITE <invoke id>" line. The process
stops when its stdin closes, so it never outlives the test that started it.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bacnet-mock-device"))

import bacnet_mock_device as mock  # noqa: E402

PORT, DELAY = int(sys.argv[1]), float(sys.argv[2])


class SlowIdentity(mock.MockApplication):
    async def do_ReadPropertyRequest(self, apdu) -> None:  # noqa: N802 - bacpypes3 name
        object_type, instance = apdu.objectIdentifier
        if str(object_type) == "device" and instance == 4194303:
            await asyncio.sleep(DELAY)
        await super().do_ReadPropertyRequest(apdu)

    async def do_WritePropertyRequest(self, apdu) -> None:  # noqa: N802 - bacpypes3 name
        print(f"WRITE {apdu.apduInvokeID}", flush=True)
        await super().do_WritePropertyRequest(apdu)


mock.MockApplication = SlowIdentity


async def main() -> None:
    device = mock.BACnetMockDevice(mock.MockConfig(host="127.0.0.1", port=PORT))
    await device.start(drift=False)
    print("listening", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    threading.Thread(target=mock._watch_stdin, args=(loop, stop), daemon=True).start()
    try:
        await stop.wait()
    finally:
        await device.stop()


asyncio.run(main())
