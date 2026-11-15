# Home automation

Three ways in, from least to most setup:

| | Needs | Gives you |
|---|---|---|
| [Plain HTTP request](#plain-http-from-any-system) | The gateway's IP, any system that can send an HTTP GET | Level, scene recall, all off |
| [Home Assistant `rest_command`](#home-assistant-without-custom-code) | YAML only | Scripts and buttons, no entities |
| [Home Assistant integration](#home-assistant-integration) | HACS or a copied folder | One light per channel, RGBW strips with colour wheel, scene and broadcast actions, HTTP or KNX, a queue |

No ETS and no DCA plug-in in any of them.

## The request

Every write is one HTTP GET:

```
http://<gateway-ip>/?x=F6C35101DA00010001<command>
```

The gateway answers with the body `00` when it accepted the command. Anything
else is a rejection, also with HTTP status 200. Three commands cover normal
use (channel `N` is 1..64, level `L` is 0..255, scene slot `S` is 0..15, all
values as two hex digits):

| Command | Effect |
|---|---|
| `0B` `N-1` `L` | Channel `N` to level `L`. `0B03FF`: channel 4 to 100 %. |
| `07` `S` `00` | Recall scene slot `S`. `070500`: slot 5. |
| `0A0000` | All lamps off. |

Do not put other commands into automations. Several of them reprogram the
installation and none asks for confirmation; see
[the command table](http-api.md#commands). The examples below build the
command from numbers behind a fixed prefix (`0B`, `07`, `0A`), so a typo
cannot turn into a programming command.

Do not retry a write on timeout automatically. The command may have run; see
[lessons-learned.md](lessons-learned.md).

## Plain HTTP from any system

Node-RED: an *http request* node, method GET, URL
`http://192.0.2.10/?x=F6C35101DA00010001{{{payload}}}`, with `msg.payload` set
to the command (for example from a *change* node: `0B03FF`). Check
`msg.payload === "00"` afterwards.

openHAB (rule DSL):

```
val answer = sendHttpGetRequest("http://192.0.2.10/?x=F6C35101DA00010001" + "070500", 15000)
if (answer === null || answer.trim != "00") logWarn("dali", "e64 rejected: " + answer)
```

ioBroker (JavaScript adapter):

```javascript
httpGet('http://192.0.2.10/?x=F6C35101DA00010001070500', { timeout: 15000 }, (err, response) => {
    if (err || response.data.trim() !== '00') log('e64 rejected: ' + (err || response.data), 'warn');
});
```

These three snippets use only the request format above, which was tested on
the gateway. The snippets themselves were not run on those systems.

## Home Assistant without custom code

`configuration.yaml`:

```yaml
rest_command:
  dali_level:
    url: >-
      http://192.0.2.10/?x=F6C35101DA00010001{{ '0B%02X%02X' | format(channel | int - 1, level | int) }}
    timeout: 15
  dali_scene:
    url: >-
      http://192.0.2.10/?x=F6C35101DA00010001{{ '07%02X00' | format(scene | int) }}
    timeout: 15
  dali_all_off:
    url: "http://192.0.2.10/?x=F6C35101DA000100010A0000"
    timeout: 15
```

Use in a script or a dashboard button:

```yaml
action: rest_command.dali_level
data:
  channel: 4
  level: 255
```

```yaml
action: rest_command.dali_scene
data:
  scene: 5
```

Limits: `rest_command` does not check the `00` answer, has no queue (many
calls at once reach the gateway in parallel) and creates no light entities.
For that use the integration.

## Home Assistant integration

`custom_components/dalicontrol_e64` in this repository.

Install with [HACS](https://hacs.xyz): *HACS → ⋮ → Custom repositories*, add
`https://github.com/NashEQify/dalicontrol-e64` as type *Integration*, install
*DaliControl e64*, restart Home Assistant. Without HACS, copy the folder to
`<config>/custom_components/` and restart.

Then *Settings → Devices & services → Add integration → DaliControl e64*:

- **HTTP:** the gateway's IP address. Setup sends one read-only request to
  check that it answers.
- **KNX:** the gateway's individual address (for example `1.1.2`). Requires the
  Home Assistant KNX integration and reuses its connection; no second tunnel,
  no group addresses. The first command checks that the address really is an
  e64 (interface object type `C352`) before it writes.
- **Channels:** which DALI channels become lights, for example `1-12, 15`.
- **RGBW strips** (optional): the first channel of each four-channel strip, in
  the order R, G, B, W. `14, 20` makes channels 14–17 and 20–23 two colour
  lights instead of eight single ones.

Both are changeable later under *Configure*. Rename the entities in Home
Assistant.

What you get:

- One dimmable light per channel. Its state is what was last sent
  successfully, because the gateway's own channel read is not reliable
  (`assumed_state`). After a restart the last known state is restored.
- One colour light per RGBW strip, with Home Assistant's colour wheel, white
  slider and brightness. A colour change is one queued request for all four
  channels. Turning it on without a colour restores the last colour, or white
  (W channel only) the first time. Colour and brightness are derived from the
  four channel levels, so they stay right after a broadcast.
- Actions `dalicontrol_e64.recall_scene` (slot 0..15), `all_off`, `all_on`
  and `set_levels` (several channels in one request, for software scenes):

  ```yaml
  action: dalicontrol_e64.set_levels
  data:
    levels: {"1": 255, "2": 128, "3": 0}
  ```

- One queue per gateway. A newer request replaces older waiting targets for
  the same channels; a scene recall or broadcast replaces all waiting channel
  writes. A failed write aborts what is waiting and shows an error instead of
  retrying.

After a scene recall the lights show *unknown*: a scene only moves its member
lamps, and the integration does not know which they are.

Not included: storing scenes, group assignment, tunable white, effects. Those are programming operations or depend on
the installation; see [scenes.md](scenes.md) for doing them by hand.

Where it comes from: the transports and the queue are the ones that run the
lighting of one flat every day, through a Home Assistant integration built
for that flat (its rooms, scenes and wall switches). This integration is the
generic version of it, for any installation: same command path, generic
entities and setup dialog. That generic Home Assistant layer is covered by
tests against a simulated gateway (`tests_ha/`, Home Assistant 2026.2) and has
not yet run against hardware itself. Reports are welcome.
