"""One gateway: transport, the shared writer and the channel list."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from dalicontrol_e64 import HttpClient, Macro, Writer, commands
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import TRANSPORT_HTTP, TRANSPORT_KNX


def parse_channels(text: str) -> list[int]:
    """Parse "1-12, 15, 20-23" into a sorted list of channels 1..64."""
    channels: set[int] = set()
    for part in str(text).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        low, sep, high = part.partition("-")
        try:
            first = int(low)
            last = int(high) if sep else first
        except ValueError:
            raise ValueError(f"not a channel or range: {part!r}") from None
        if not 1 <= first <= last <= commands.MAX_CHANNELS:
            raise ValueError(f"channels are 1..{commands.MAX_CHANNELS}: {part!r}")
        channels.update(range(first, last + 1))
    if not channels:
        raise ValueError("no channels given")
    return sorted(channels)


def parse_strips(text: str, channels: list[int]) -> list[int]:
    """Parse the first channels of RGBW strips, e.g. "14, 20" for 14-17 and 20-23.

    Each strip is four consecutive channels in the order R, G, B, W. All four
    must be configured channels, and strips must not overlap.
    """
    starts: list[int] = []
    used: set[int] = set()
    for part in str(text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            start = int(part)
        except ValueError:
            raise ValueError(f"not a channel: {part!r}") from None
        strip = set(range(start, start + 4))
        if not strip <= set(channels):
            raise ValueError(f"strip {start}-{start + 3} is not inside the configured channels")
        if strip & used:
            raise ValueError(f"strip {start}-{start + 3} overlaps another strip")
        used |= strip
        starts.append(start)
    return sorted(starts)


def knx_sender(hass: HomeAssistant, address: str) -> Callable[[str], Awaitable[None]]:
    """Send over the connection the Home Assistant KNX integration already owns."""
    from dalicontrol_e64.knx import KnxTransport

    def provider():
        module = hass.data.get("knx")  # homeassistant.components.knx.const.KNX_MODULE_KEY
        if module is None:
            raise HomeAssistantError("The Home Assistant KNX integration is not set up")
        return module.xknx

    return KnxTransport(provider, address).send


def http_sender(hass: HomeAssistant, host: str) -> Callable[[str], Awaitable[None]]:
    client = HttpClient(host)

    async def send(command: str) -> None:
        await hass.async_add_executor_job(client.send, command)

    return send


@dataclass
class Gateway:
    """Everything that writes to the gateway goes through ``writer``.

    Channel state in Home Assistant is what was last sent successfully. The
    gateway's own channel cache is not read: it is wrong after programming
    writes (docs/lessons-learned.md).
    """

    title: str
    transport: str
    channels: list[int]
    writer: Writer
    strips: list[int] = field(default_factory=list)
    levels: dict[int, int | None] = field(default_factory=dict)
    listeners: list[Callable[[], None]] = field(default_factory=list)

    @classmethod
    def create(cls, hass: HomeAssistant, title: str, transport: str, target: str,
               channels: list[int], strips: list[int] | None = None) -> "Gateway":
        if transport == TRANSPORT_KNX:
            send = knx_sender(hass, target)
        elif transport == TRANSPORT_HTTP:
            send = http_sender(hass, target)
        else:
            raise ValueError(f"unknown transport {transport!r}")
        return cls(title, transport, channels, Writer(send), list(strips or []),
                   dict.fromkeys(channels, None))

    def _notify(self) -> None:
        for listener in list(self.listeners):
            listener()

    async def _run(self, job) -> dict:
        result = await job.future
        if result["status"] in ("failed", "aborted"):
            raise HomeAssistantError(
                f"DaliControl e64 request {result['label'] or result['request']} "
                f"{result['status']}: {result.get('error', 'unknown error')}")
        return result

    async def set_levels(self, targets: dict[int, int], label: str = "") -> dict:
        """Queue channel levels; a newer request replaces waiting older ones."""
        result = await self._run(self.writer.submit(targets, label=label))
        if result["status"] in ("completed", "partial"):
            # On "partial" a newer request owns some channels and will set them.
            for channel, value in targets.items():
                if self.writer.owners.get(channel) == result["request"]:
                    self.levels[channel] = value
            self._notify()
        return result

    async def broadcast(self, command: str, level: int | None, label: str) -> dict:
        """Send one bus-wide command (scene recall, all on, all off).

        It replaces channel writes that are still waiting in the queue, because
        it can move every lamp. ``level`` is the known result, or None when the
        outcome per channel is unknown (a scene only moves its members).
        """
        footprint = dict.fromkeys(self.channels, 0 if level is None else level)
        job = self.writer.submit(footprint, label=label, macro=Macro(command, footprint))
        result = await self._run(job)
        for channel in self.channels:
            if self.writer.owners.get(channel) == result["request"]:
                self.levels[channel] = level
        self._notify()
        return result
