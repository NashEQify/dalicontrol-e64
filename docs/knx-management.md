# Sending commands over KNX

The same three-byte commands as in [http-api.md](http-api.md) can be sent over
the KNX bus, with the gateway's Ethernet cable unplugged. No group addresses
are involved and nothing has to be changed in ETS.

Tested on: IPAS DaliControl e64, firmware 1.3_06, application e64-01-0110,
through a KNXnet/IP tunnel on a separate KNX IP router.

## The mechanism

The gateway exposes a manufacturer-specific KNX interface object:

| | |
|---|---|
| Object index | 6 |
| Object type (property 1) | `C352` |
| Command property | 218 (`0xDA`), the same id the HTTP API uses |

A `PropertyValueWrite` to object 6, property 218, start index 1, count 1, with
three data bytes executes the command. The gateway answers with a
`PropertyValueResponse` that echoes the bytes.

This is KNX device management: a connection-oriented, individually addressed
exchange with the gateway's individual address (for example `1.1.2`). It is the
same kind of traffic ETS uses to talk to a device.

```python
from xknx.telegram.apci import PropertyValueWrite, PropertyValueResponse

connection = await xknx.management.connect(IndividualAddress("1.1.2"), rate_limit=2)
response = await connection.request(
    PropertyValueWrite(object_index=6, property_id=218, count=1, start_index=1,
                       data=bytes.fromhex("0B03FF")),      # channel 4 to 100 %
    PropertyValueResponse)
await xknx.management.disconnect(IndividualAddress("1.1.2"))
```

`dalicontrol_e64.knx.KnxTransport` wraps this with the checks below.

## What was verified

- Single-channel levels: a four-channel RGBW strip went blue, off, white on
  command, watched on site.
- Scene recalls `070500` and `070300`, watched on site.
- Normal operation from wall switches and an app through Home Assistant, with
  the gateway's LAN cable unplugged, including the first press after more than
  eleven minutes without any command. Requests completed in 0.14–0.18 s.

Not separately verified: broadcasts under load, many channels in quick
succession, and anything other than level, scene recall and all-off.

## Rules that proved necessary

1. **Check what you are talking to.** Before the first write on a bus
   connection, read property 1 of object 6 and require `C352`. A wrong
   individual address then fails instead of writing property 218 into some
   other device.
2. **Check the echo.** Require object, property, index, count and bytes of the
   response to match. An unanswered write counts as failed.
3. **Allowlist the commands.** The transport refuses everything except level,
   scene recall and all-off. Programming commands work over this path just as
   well as over HTTP, which is exactly the problem.
4. **Pace it.** One command is several telegrams: connect, write, response,
   acknowledgements, disconnect, on a 9600 baud bus shared with everything
   else in the house. At least 0.5 s between transactions, so at most two
   commands per second, was reliable. Use scenes or broadcasts instead of
   bursts of single-channel writes (see [scenes.md](scenes.md)).
5. **One short connection per command, none while idle.** No keep-alive, no
   polling.
6. **Bound it.** Eight seconds for the transaction, two for the disconnect.
7. **Do not retry at the application level.** The KNX stack may repeat one
   unacknowledged telegram with the same sequence number; that is fine. A new
   attempt of the whole command is a new decision for whoever asked for the
   light, see [lessons-learned.md](lessons-learned.md).
8. **Reuse the existing tunnel.** If Home Assistant already holds a KNX
   connection, send through it. A second tunnel needs a second free tunnel
   address on the router and buys nothing.

Even on this path, single management confirmations occasionally went missing
and a multi-command scene was aborted. It is much more reliable than HTTP was
on the affected device; it is not a guarantee.

## What does not work the same way

The gateway also has a regular KNX **group object** for scene recall
(communication object 6 in the ETS application: byte value 0–15 recalls scene
1–16; with the top bit set it *stores* instead). In this installation correctly
sent recalls on that group address had no visible effect, while the management
path above worked. The reason was not found. If your group objects work, use
them; if not, the management path is independent of them.

## Reading and changing ETS parameters without ETS

Separate topic, short version: with [Calimero](https://github.com/calimero-project/calimero-tools)
the application's parameter memory can be read over the same tunnel
(`DeviceInfo`, `Property`, `Memory read`). On this device:

- Reads of 1 and 8 bytes are answered, a 32-byte read is not. Read in 8-byte
  blocks and check completeness.
- The parameter segment is application object 4; its base is in property 7
  (table reference), its size in the ETS project's load procedure.
- The checksum in the memory control block (property 27) uses CRC-16 with
  polynomial `1021`, start `FFFF`, two zero bytes appended, no final XOR
  (`123456789` gives `E5CC`). With control byte `01` the stored checksum is
  **not binding**: after a partial download the device kept reporting the old
  value although the memory was byte-identical to the original. A stale stamp
  alone is not corruption. Compare the whole block.
- Writing a single parameter is not one memory write. It needs the load
  procedure of the application (unload/load state, relative memory write,
  restart). Parameter dumps can contain device passwords; treat them as secrets.

Changing one parameter this way was done once, with a full backup, a simulated
dry run and a verified restore. It did not fix the problem it was meant to fix.
It is mentioned so you know it is possible, not as a recommendation.
