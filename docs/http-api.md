# HTTP API

The device's built-in web page talks to a single CGI endpoint. Everything the
page can do, a script can do with plain `GET` requests. This was worked out
from the page's JavaScript and then checked against the device.

Tested on: **IPAS DaliControl e64, firmware 1.3_06, ETS application
e64-01-0110**. Other firmware generations (3.x) and the "e64 Pro" are untested
and may differ.

## Request and response

```
GET http://<device>/?x=<hex>&z=<timestamp>
```

- `x` is a hex string, described below. `z` is only a cache buster.
- The web page shows a login panel, but **the CGI itself asks for no
  credentials**. Anyone who can reach the device's IP can send every command
  on this page, including the destructive ones. Keep it off untrusted networks.
- The status is `200` even when the command fails. The **body** is the result:
  `00` means accepted, anything else is an error flag set (not decoded).
- `00` means "accepted", not "the lamp did it". See
  [lessons-learned.md](lessons-learned.md#an-acknowledgement-is-not-a-lamp).

The hex string has the shape of a KNX property access:

```
write:  F6 C35101 <pid> <count:4> <start:4> <data>
read:   FC C35101 <pid> <count:4> <start:4>
```

`C35101` addresses the DALI object, `<pid>` is a two-character property id,
count and start are four hex characters each, start is 1-based.

## Commands

Commands are three bytes written to property `DA`, always with count 1 and
start 1. The fixed prefix is therefore:

```
F6C35101DA00010001 + <6 hex characters>
```

Example, channel 4 to 100 %:

```bash
curl -sS -H 'Connection: close' --max-time 15 \
  'http://<device>/?x=F6C35101DA000100010B03FF'
```

### Operational commands

These do what a wall switch does. Channels and groups are written 0-based in
the command, so channel `N` is `N-1`.

| Command | Bytes | Meaning | Status |
|---|---|---|---|
| Level, one channel | `0B <N-1> <level>` | level 0..255, `00` off, `FF` 100 % | verified on lamps |
| Level, all | `0B FF <level>` | one broadcast for the whole bus | verified on lamps (`0BFFFF`) |
| All off | `0A 00 00` | broadcast off | verified on lamps |
| Scene recall | `07 <slot> 00` | slot 0..15, broadcast "go to scene" | verified on lamps |
| Blink, one channel | `04 <0x40+N-1> 00` | blinks the lamp a few times | used to map lamps |
| Blink, all | `04 FF 00` | | from the web UI |
| Group level | `08 <G-1> <level>` | group 1..16 | from the web UI, not field-tested |

Two traps:

- The web page's constant for "all on" encodes as `0B0000`. The device answers
  `00` and **no lamp reacts**. Use `0BFFFF`.
- The constant behind scene recall is called `CMD_TEST` in the page source. It
  is a persistent scene recall, not a test mode.

### Commands that change the installation

**Never send any of these to "see if the device answers".** The CGI has no
confirmation step; the request is the click on OK.

| First byte | Name in the web UI | What it does |
|---|---|---|
| `01` | NEWINST | **New installation.** Re-addresses every ballast. Lamps come on, the device is busy for minutes, the channel-to-lamp mapping and all group assignments are gone. `010000` is all it takes. |
| `02` | CONFIG | Post-installation: gives unaddressed ballasts a free address, keeps the rest. |
| `05` | SETGROUP | `05 <G-1> <N-1>`: puts channel N into group G. |
| `06` | PSCENE | `06 <slot> 00`: stores the current live levels as a scene, see [scenes.md](scenes.md). |
| `0D` | TUNNEL | Raw DALI passthrough. Declared in the page, never used by it. Unknown. |
| `0E` | SWAP | `0E <a> <b>`: swaps the short addresses of two ballasts. |
| `10` | RHRESET | Resets operating-hours counters. |
| `11` | BURNIN | Burn-in mode. |
| `13` | REPLACE | Single ballast replacement. |
| `14`–`17` | CFT, SDT, LDT, BRT | Emergency converter tests. |
| `12`, `1B`, `20`, `80` | FXUPDATE, TPLSYNC, ECGMODE, TPLRUN | Effects, templates, ballast modes. Not explored. |

Of these, only NEWINST, CONFIG, SETGROUP and PSCENE were actually sent in this
project. The others are listed from the page source so you can refuse them.

## Reading

| Property | Request | One line per | Content |
|---|---|---|---|
| `D2` general failures | `FCC35101D200010001` | – | cached failure flags; the cheapest read, good as a liveness probe |
| `CD` channel states | `FCC35101CD00400001` | channel | 6 hex chars of flags, then 2 hex chars of level |
| `CB` channel properties | `FCC35101CB00400001` | channel | flags and group membership |
| `CF` group names | `FCC35101CF00100001` | group | UTF-8 as hex |
| `D0` group states | `FCC35101D000100001` | group | level at chars 6..7 |
| `D3` scene names | `FCC35101D300100001` | scene | UTF-8 as hex |
| `D5` scene fade times | `FCC35101D500100001` | scene | |
| `D4` scene membership | `FCC35101D400100001` | scene | 2 group bytes + 8 channel bytes, bit mask |
| `D7` scene levels | `FCC35101D700400001` (slots 0–7), `FCC35101D700400041` (slots 8–15) | 8 channels | 8 lines per slot, 8 level bytes per line; slot `S` starts at index `S*8+1` |

The response body is text: one hex string per line.

### The channel-state cache is not the lamp

`CD` is served from a cache inside the gateway.

- Right after programming writes it returns `00000000` for **every** channel,
  including lamps that are visibly on. Waiting 40 seconds did not refill it.
- A level write to a channel makes the gateway poll that channel; about two
  seconds later its line is correct again. The other channels stay stale.
- A flags prefix of `000000` was seen whenever the entry was empty. A
  non-zero prefix carries no timestamp and is no freshness guarantee either.

So: do not use `CD` to verify programming, and do not use it to skip commands
you believe are unchanged. No purely reading way to force a refresh was found.
`D7` (scene levels) is different: it is correct immediately after a scene is
stored.

## Timing

- A level or scene command takes roughly 200–400 ms. The cached `D2` read is
  much faster. Measure them separately.
- One request at a time. Under load (blink-all, new installation, parallel
  requests) the web server stops accepting connections.
- After a timeout on a **read**, wait 15–30 seconds and try again. After a
  timeout on a **write**, do not repeat it blindly: a timeout does not prove
  the command was not executed.
- The device this was developed on restarted itself repeatedly while its
  Ethernet port was connected. If you see the same, read
  [lessons-learned.md](lessons-learned.md#the-ethernet-port-can-be-the-problem)
  and consider the [KNX transport](knx-management.md).
