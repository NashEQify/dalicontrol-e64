"""HTTP transport: the CGI endpoint behind the device's own web page.

Standard library only. Blocking; run it in a thread if you need async.
See docs/http-api.md for the wire format and docs/lessons-learned.md for why
a timeout must never be answered with a blind retry of a write.
"""
from __future__ import annotations

import time
import urllib.request

from .commands import require_operational

WRITE_COMMAND = "F6C35101DA00010001"  # write, DALI object, property DA, count 1, start 1
READ = "FCC35101"


class CommandRejected(RuntimeError):
    """The device answered, but not with ``00``."""


class HttpClient:
    def __init__(self, host: str, *, timeout: float = 20, opener=urllib.request.urlopen):
        self._base = f"http://{host}/?x="
        self._timeout = timeout
        self._open = opener

    def _get(self, payload: str) -> str:
        # z is only a cache buster, exactly as the device's own page sends it.
        url = f"{self._base}{payload}&z={int(time.time())}"
        request = urllib.request.Request(url, headers={"Connection": "close"})
        with self._open(request, timeout=self._timeout) as response:
            if response.status != 200:
                raise CommandRejected(f"HTTP {response.status}")
            return response.read().decode("ascii", "replace")

    def send(self, command: str) -> None:
        """Send one operational command. HTTP 200 alone is not success: the body must be ``00``."""
        require_operational(command)
        body = self._get(WRITE_COMMAND + command.upper()).strip()
        if body != "00":
            raise CommandRejected(f"device answered {body[:80]!r} to {command}")

    def read_property(self, pid: str, count: int, start: int = 1) -> list[str]:
        """Read ``count`` items of a property; one hex string per item."""
        if len(pid) != 2:
            raise ValueError("property id is two hex characters, e.g. 'CD'")
        int(pid, 16)
        body = self._get(f"{READ}{pid.upper()}{count:04X}{start:04X}")
        return [line.strip() for line in body.split("\n") if line.strip()]

    def reachable(self) -> bool:
        """Cheap read-only liveness check (general failure flags, served from cache).

        Use this, never a write, when you only want to know whether the device answers.
        """
        try:
            return bool(self.read_property("D2", 1))
        except (OSError, CommandRejected):
            return False

    def cached_levels(self) -> list[int | None]:
        """Per-channel level from the device's cache; ``None`` where the cache is empty.

        The cache is not the lamp. After programming writes it reads all-zero for
        lamps that are visibly on. Do not use it to skip "unchanged" commands.
        """
        levels: list[int | None] = []
        for line in self.read_property("CD", 64):
            levels.append(None if line[:6] == "000000" else int(line[6:8], 16))
        return levels

    def scene_levels(self, scene: int) -> list[int]:
        """Stored levels of one scene slot (0..15) for channels 1..64. Reliable right after programming."""
        if not 0 <= scene <= 15:
            raise ValueError("scene slot is 0..15")
        lines = self.read_property("D7", 8, scene * 8 + 1)
        return [int(line[i:i + 2], 16) for line in lines for i in range(0, 16, 2)]
