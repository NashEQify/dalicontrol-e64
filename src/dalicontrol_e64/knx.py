"""KNX transport: the same three-byte commands, without the device's Ethernet port.

The gateway exposes a manufacturer-specific interface object (index 6, object
type 0xC352). Writing three bytes to its property 218 (0xDA) executes them
exactly like the HTTP CGI does. The telegrams are individually addressed
management telegrams, so no group address and no ETS change is needed.

Requires ``xknx`` and an already connected ``XKNX`` instance (for example the
one Home Assistant's KNX integration owns). No second tunnel is opened.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable

from xknx import XKNX
from xknx.telegram import IndividualAddress
from xknx.telegram.apci import PropertyValueRead, PropertyValueResponse, PropertyValueWrite

from .commands import require_operational

OBJECT_INDEX = 6
OBJECT_TYPE = bytes.fromhex("c352")
PID_OBJECT_TYPE = 1
PID_COMMAND = 218


class KnxTransport:
    """One short management connection per command, nothing while idle.

    - The interface object type is verified once per bus connection before the
      first write, so a wrong individual address fails instead of writing
      property 218 on some other device.
    - The response must echo object, property, index, count and the bytes.
    - ``interval`` spaces transactions: one command is several telegrams on a
      9600 baud bus. Two commands per second proved reliable.
    - A failed command is never retried here. The caller decides.
    """

    def __init__(self, provider: Callable[[], XKNX], address: str, *,
                 interval: float = 0.5, timeout: float = 8, cleanup_timeout: float = 2):
        self._provider = provider
        self._address = IndividualAddress(address)
        self._interval = interval
        self._timeout = timeout
        self._cleanup_timeout = cleanup_timeout
        self._next_send = 0.0
        self._lock = asyncio.Lock()
        self._verified: tuple[XKNX, object] | None = None

    @staticmethod
    def _check(response, pid: int, expected: bytes) -> None:
        value = response.payload
        if (value.object_index, value.property_id, value.start_index,
                value.count, value.data) != (OBJECT_INDEX, pid, 1, 1, expected):
            raise RuntimeError(f"device rejected or mismatched KNX property {pid}")

    async def send(self, command: str) -> None:
        payload = require_operational(command)
        async with self._lock:
            await asyncio.sleep(max(0, self._next_send - time.monotonic()))
            xknx = self._provider()  # resolve each time: the owner may have reconnected
            if not xknx.connection_manager.connected.is_set():
                raise RuntimeError("KNX connection is unavailable")
            generation = (xknx, xknx.connection_manager.connected_since)
            connection = None
            failure = None
            try:
                async with asyncio.timeout(self._timeout):
                    connection = await xknx.management.connect(self._address, rate_limit=2)
                    if self._verified != generation:
                        response = await connection.request(PropertyValueRead(
                            object_index=OBJECT_INDEX, property_id=PID_OBJECT_TYPE,
                            count=1, start_index=1), PropertyValueResponse)
                        self._check(response, PID_OBJECT_TYPE, OBJECT_TYPE)
                        self._verified = generation
                    response = await connection.request(PropertyValueWrite(
                        object_index=OBJECT_INDEX, property_id=PID_COMMAND,
                        count=1, start_index=1, data=payload), PropertyValueResponse)
                    self._check(response, PID_COMMAND, payload)
            except BaseException as exc:
                self._verified = None
                failure = exc
                raise
            finally:
                try:
                    if connection is not None:
                        try:
                            await asyncio.wait_for(
                                xknx.management.disconnect(self._address),
                                self._cleanup_timeout)
                        except Exception:
                            self._verified = None
                            if failure is None:
                                raise
                finally:
                    self._next_send = time.monotonic() + self._interval
