# dalicontrol-e64

Use and program the DALI lights behind an **IPAS DaliControl e64** KNX/DALI
gateway from any home automation system or script, without ETS and without
the DCA (IPAS' Device Configuration App plug-in for ETS). Unofficial protocol
notes, field experience, a Python library and a Home Assistant integration.

## Why

- **No ETS, no DCA, no licence.** Switching, dimming, group assignment,
  storing and recalling scenes, even a DALI new installation: all of it works
  over plain HTTP (a browser, curl, Python) or over KNX with a free library
  such as [xknx](https://github.com/XKNX/xknx).
- **Any home automation system.** Home Assistant (integration in this repo,
  or a few lines of YAML), Node-RED, openHAB, ioBroker, a shell script. The
  request is one HTTP GET.
- **Colour wheels for RGBW LED strips.** A strip is four DALI channels; the
  Home Assistant integration turns each into one colour light with the normal
  colour wheel, white slider and brightness. Pick a colour on a wall tablet
  and the strip follows.
- **Fast and stable on old hardware.** The approach here drives a flat with
  38 DALI ballasts every day and has for months: scene changes and dimming
  react immediately, nothing needs restarting. That was not expected from a
  gateway of this age on its original firmware; it took a lot of testing to
  find what makes it quick (scenes instead of many single writes, one queue,
  no blind retries; see [lessons-learned.md](docs/lessons-learned.md)).
- **No firmware update needed.** Everything here runs on firmware 1.3_06. An
  update to 3.x destroyed the KNX side of a second gateway in the same
  installation; manufacturer support could neither update the other one nor
  downgrade the broken one in a remote session. Staying on 1.x is a choice,
  and this repository is what makes it workable.

The gateway is usually commissioned once with ETS and then only reached
through KNX group addresses. Its built-in web page, however, uses a plain HTTP
interface, and the same commands can be sent over KNX as device management
telegrams, without any ETS change. Neither is documented by the manufacturer.
This repository writes both down, including the parts that went wrong.

ETS is only needed for the KNX side of the gateway: its individual address,
group objects and KNX application parameters. If the gateway was commissioned
once, you can leave that untouched. If you only use the HTTP interface, the
KNX side does not matter at all.

For that KNX side there is an open-source alternative to ETS as well:
[bussard](https://github.com/tmbo/bussard), a command-line tool that keeps the
KNX configuration in text files and writes it to the devices. Check its device
list for your gateway's application version before relying on it.

Reading this with an LLM or coding agent: start with [llms.txt](llms.txt).

> **Tested on one device type and firmware only:** DaliControl e64, firmware
> 1.3_06, ETS application e64-01-0110. Not affiliated with or endorsed by
> IPAS. Several commands re-address or reconfigure your installation and have
> no confirmation step. Read [the command table](docs/http-api.md#commands)
> before you send anything. You act at your own risk.

## What is in here

| | |
|---|---|
| [docs/http-api.md](docs/http-api.md) | The HTTP interface: request format, every command with its risk, readable properties, timing |
| [docs/knx-management.md](docs/knx-management.md) | The same commands over KNX, with the gateway's LAN cable unplugged; reading ETS parameters without ETS |
| [docs/scenes.md](docs/scenes.md) | How DALI scenes are stored and recalled, and how to use them from software |
| [docs/lessons-learned.md](docs/lessons-learned.md) | What failed and what replaced it |
| [docs/home-automation.md](docs/home-automation.md) | Home Assistant, Node-RED, openHAB, ioBroker: from one HTTP request to a full integration |
| `src/dalicontrol_e64/` | Python library on PyPI: command encoding with an allowlist, HTTP client, KNX transport, request queue |
| `custom_components/dalicontrol_e64/` | Home Assistant integration (HACS): one light per channel, RGBW strips with colour wheel, scene recall, all on/off, HTTP or KNX |

## Five things to know first

1. `010000` starts a DALI new installation. Nothing about the bytes tells you.
2. The answer `00` means "accepted". It does not mean a lamp reacted.
3. The channel-state read is a cache and is wrong after programming writes.
4. One scene recall beats twenty single-channel writes. Use the 16 slots.
5. If lights react late or not at all, check whether the gateway restarts
   itself every few minutes while its Ethernet port is connected.

## Home Assistant

Add this repository in HACS as a custom repository (type *Integration*),
install *DaliControl e64*, restart, then add the integration with the
gateway's IP address or its KNX individual address. Details, a YAML-only
alternative and examples for Node-RED, openHAB and ioBroker:
[docs/home-automation.md](docs/home-automation.md).

## Quick start (Python)

```bash
pip install dalicontrol-e64
```

```python
from dalicontrol_e64 import HttpClient, commands

gateway = HttpClient("192.0.2.10")          # the gateway's IP address
print(gateway.reachable())                  # read-only check

gateway.send(commands.level(4, 255))        # channel 4 to 100 %
gateway.send(commands.level(4, 0))          # channel 4 off
gateway.send(commands.scene_recall(5))      # recall scene slot 5
gateway.send(commands.ALL_OFF)
```

`send` refuses everything that is not a level, a scene recall or all-off, and
raises unless the gateway answers `00`.

With curl, no library needed:

```bash
curl -sS --max-time 15 'http://192.0.2.10/?x=F6C35101DA000100010B03FF'   # channel 4 on
```

### Over KNX

```bash
pip install "dalicontrol-e64[knx]"
```

```python
from dalicontrol_e64.knx import KnxTransport

transport = KnxTransport(lambda: xknx, "1.1.2")   # connected XKNX, gateway's individual address
await transport.send(commands.scene_recall(5))
```

### Many callers, one gateway

`Writer` serialises requests from switches, apps and automations. A newer
request replaces older waiting targets for the same channels; a failed write
aborts what is waiting instead of retrying.

```python
from dalicontrol_e64 import Writer, Macro, commands

writer = Writer(transport.send)                 # or an async wrapper around HttpClient.send
job = writer.submit({14: 0, 15: 0, 16: 0, 17: 200}, label="dining strip white")
result = await job.future                       # {'status': 'completed', 'commands': [...], ...}

room = {c: 0 for c in range(20, 28)}
writer.submit(room, macro=Macro(commands.scene_recall(8), dict(room)))   # one command
```

The Home Assistant integration in `custom_components/` is built on exactly
these two parts and reuses the KNX integration's connection.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests            # library
pip install pytest-homeassistant-custom-component xknx -e .
python3 -m pytest                                                # Home Assistant integration (Python 3.13)
```

The tests run against a simulated device. They prove the encoding, the
allowlist and the queue logic, not the behaviour of your hardware.

## Status and contributing

Finished for the installation it was written for: it has run stable and fast
there for months, so it will not be developed further from that side. Pull
requests are welcome, for example:

- other firmware versions (3.x) or the e64 Pro: which commands still work
- other gateways of the IPAS DaliControl family
- integrations or examples for further systems (openHAB, ioBroker, Node-RED, Loxone …)
- decoded error flags and properties
- tunable-white channels as one light in Home Assistant

Please say what you observed on the lamps, not only what the gateway answered.

## License

MIT, see [LICENSE](LICENSE).
