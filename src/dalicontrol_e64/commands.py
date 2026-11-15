"""Three-byte command encoding for the IPAS DaliControl e64 (firmware 1.3_06).

Every command is three bytes, written as six hex characters. The same bytes
travel over both transports (HTTP CGI and KNX property write).

Encoders here cover only *operational* commands: things a resident could also
trigger from a wall switch. Programming commands are listed in ``DANGEROUS``
so callers can refuse them; nothing in this package sends them by accident.
"""
from __future__ import annotations

MAX_CHANNELS = 64
MAX_GROUPS = 16
MAX_SCENES = 16

ALL_OFF = "0A0000"
ALL_ON = "0BFFFF"  # level broadcast at 100 %. "0B0000" is acknowledged but does nothing.

# First command byte -> (name, effect). Never send these to "test the connection".
DANGEROUS = {
    0x01: ("NEWINST", "DALI new installation: re-addresses every ballast, wipes groups"),
    0x02: ("CONFIG", "post-installation: assigns addresses to unaddressed ballasts"),
    0x05: ("SETGROUP", "writes a group assignment into the device"),
    0x06: ("PSCENE", "stores the current live levels as a scene"),
    0x0D: ("TUNNEL", "raw DALI passthrough, behaviour unknown"),
    0x0E: ("SWAP", "swaps the short addresses of two ballasts"),
    0x10: ("RHRESET", "resets operating-hours counters"),
    0x11: ("BURNIN", "burn-in mode, lamps at full load"),
    0x12: ("FXUPDATE", "writes effect configuration"),
    0x13: ("REPLACE", "single ballast replacement, touches the address table"),
    0x14: ("CFT", "converter function test"),
    0x15: ("SDT", "converter duration test"),
    0x16: ("LDT", "converter long duration test"),
    0x17: ("BRT", "converter battery test"),
    0x1B: ("TPLSYNC", "template sync"),
    0x20: ("ECGMODE", "changes ballast mode flags"),
    0x80: ("TPLRUN", "runs a configured template"),
}


def _check(name: str, value: int, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer {low}..{high}, got {value!r}")
    return value


def level(channel: int, value: int) -> str:
    """Set one channel (1..64) to a level 0..255. 0 is off, 255 is 100 %."""
    _check("channel", channel, 1, MAX_CHANNELS)
    _check("value", value, 0, 255)
    return f"0B{channel - 1:02X}{value:02X}"


def level_all(value: int) -> str:
    """Set every ballast on the bus to one level with a single broadcast."""
    return f"0BFF{_check('value', value, 0, 255):02X}"


def group_level(group: int, value: int) -> str:
    """Set one DALI group (1..16) to a level. Derived from the web UI, not field-tested."""
    _check("group", group, 1, MAX_GROUPS)
    _check("value", value, 0, 255)
    return f"08{group - 1:02X}{value:02X}"


def scene_recall(scene: int) -> str:
    """Recall scene slot 0..15. A bus broadcast: every member ballast moves."""
    return f"07{_check('scene', scene, 0, MAX_SCENES - 1):02X}00"


def blink(channel: int) -> str:
    """Blink one channel (1..64) a few times, to find out which lamp it is."""
    return f"04{0x40 + _check('channel', channel, 1, MAX_CHANNELS) - 1:02X}00"


def parse(command: str) -> bytes:
    """Return the three command bytes or raise ValueError."""
    if not isinstance(command, str) or len(command) != 6:
        raise ValueError("a command is exactly six hex characters")
    try:
        return bytes.fromhex(command)
    except ValueError:
        raise ValueError("a command is exactly six hex characters") from None


def is_operational(command: str) -> bool:
    """True for level writes, scene recalls and all-off; nothing else.

    This is an allowlist on purpose. The encoding gives no hint which values
    are harmless: ``010000`` looks like a no-op and starts a new installation.
    """
    try:
        b = parse(command)
    except ValueError:
        return False
    return (
        b == b"\x0a\x00\x00"
        or (b[0] == 0x0B and (b[1] < MAX_CHANNELS or b[1] == 0xFF))
        or (b[0] == 0x07 and b[1] < MAX_SCENES and b[2] == 0)
    )


def require_operational(command: str) -> bytes:
    if not is_operational(command):
        b = None
        try:
            b = parse(command)
        except ValueError:
            pass
        if b is not None and b[0] in DANGEROUS:
            name, effect = DANGEROUS[b[0]]
            raise ValueError(f"{command} is {name}: {effect}")
        raise ValueError(f"{command!r} is not a level, scene-recall or all-off command")
    return parse(command)
