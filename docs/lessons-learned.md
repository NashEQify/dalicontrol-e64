# Lessons learned

What went wrong while building a lighting control on top of a DaliControl e64,
and what replaced it. Each entry says what was tried, why it failed and what
to do instead. Where the cause is still unknown, it says so.

## The encoding gives no hint what is dangerous

`010000` looks like a harmless test value. It is the new-installation command.
When it is sent, every lamp comes on, the gateway is busy for minutes, all
ballasts lose their short addresses, and the lamp-to-channel map has to be
rebuilt by blinking each channel and walking through the building.

Therefore:

- Look every command up before sending it. There is no convention like "reads
  are safe" in this protocol.
- For liveness, use a read (`FCC35101D200010001`). Never a write.
- Put an allowlist in code, at the lowest layer that sends bytes. This
  package refuses everything but level, scene recall and all-off.
- Keep a written channel-to-lamp map. It is the one thing a new installation
  destroys that no backup restores.

## An acknowledgement is not a lamp

The page source has a constant for "all on" that encodes as `0B0000`. The
gateway answers `00`. It was documented as verified, on the strength of that
answer, for weeks. No lamp ever reacted. The working command is `0BFFFF`.

The same happened with a scene that was stored without membership: commit
`00`, recall `00`, nothing moves.

Instead: a command counts as verified when someone has seen the lamp do it.
Write down which of your commands have that status and which are only derived.

## The state cache lies after writes

Reading channel states (`CD`) to check a result, or to skip commands whose
target "is already set", looked like an easy optimisation. After programming
writes the cache returns zero for every channel while the lamps are on, and it
does not recover by waiting.

Instead: treat the software as the source of intent, not of state. The writer
tracks what was asked for and never claims to know what the lamps do. Send the
command even if you believe nothing changed.

## A FIFO queue makes the house feel broken

The first version queued every single-channel write in order. Someone presses
"movie", then "off": the off waits behind twenty writes of a scene nobody
wants any more. People press again, the queue grows, lamps keep changing long
after the last press.

Instead: last request wins, per channel.

- A new request replaces older *waiting* targets for the same channels.
  Channels of other rooms are untouched.
- The command already on the wire finishes; then the superseded rest is
  dropped.
- A one-command request (scene recall, broadcast) keeps its place in line, so
  a later single-room change still lands after it.
- Exactly one writer for all callers: wall switches, app, automations.

## Retrying writes hides the problem and adds new ones

After a timeout it is tempting to send the command again. A timeout does not
say whether the gateway executed it. With multi-step scenes a blind retry or
"continue with the rest" leaves a half-built scene on top of an unknown state.

Instead: on a failed write, fail the request, drop everything that was
waiting, report it, and let the next explicit press start fresh.

## Keep-alives and "pre-wake" requests do nothing useful

Slow first reactions led to the idea that the gateway falls asleep. Periodic
dummy requests and a request on every button press were added to keep it
awake. Timeouts continued unchanged after they were removed, and the first
dummy command chosen for this was a write to an unused channel, which is still
a write.

Instead: no polling, no keep-alive. If the device is unreachable, find out why.

## The Ethernet port can be the problem

Sluggish and ineffective switches turned out to be the gateway
re-initialising itself every few minutes:

- It stopped answering ARP, ICMP and TCP for 10–20 seconds.
- Each time, it then asked the bus for time and date, which the manual
  describes as application start-up behaviour.
- Its TCP initial sequence number, otherwise growing steadily, jumped back to
  a small value.
- Intervals were irregular (roughly 330–370 seconds under probing). It also
  happened with pings only, without a single TCP connection, and without any
  light command. A KNX management read during such a window timed out too.
- With the LAN cable unplugged for eight minutes, no restart was seen. Within
  18 seconds of plugging it back in, the pattern returned.

What was ruled out: the command queue, Home Assistant, the wall switches, HTTP
as such. What was not separated: a firmware fault in the network stack, other
traffic on the LAN, or a hardware fault that depends on the link. The exact
cause is unknown. Changing the "cycle time for failure queries" parameter made
no difference.

What fixed daily operation: **unplug the gateway's LAN cable for good and send
the commands over KNX** ([knx-management.md](knx-management.md)). KNX, DALI and
power stay connected. The price is losing the web page until you plug the
cable in again.

How to recognise it on your own device: capture on the KNX tunnel and look for
the gateway's time/date read requests recurring every few minutes, together
with gaps in ping.

## Think twice before a firmware generation jump

A second gateway of the same type was updated from 1.3_06 to 3.1_09. After
that its KNX side was gone: individual address back to the factory value, no
answer on the bus, programming LED dead, while the DALI web page kept working.
The ETS product database for 3.x is a different application than the one for
1.x, and the manufacturer states that the 1.x to 3.x update erases the ETS and
DALI configuration, scenes and effects. Support could not recover the device.
Its lamps were rewired to the remaining gateway.

Instead: on a working 1.x device, do not update on suspicion. If you must,
plan it as a full re-commissioning with ETS and the DCA, and have the
channel-to-lamp map on paper first. A dump of the KNX tables is not a backup
of the DALI side.

## You can program more without ETS than people say

"Groups and scenes need ETS and the DCA plug-in" is not true for this
firmware. Group assignment and scene storage work over HTTP
([scenes.md](scenes.md)). What does need the ETS application's load procedure
are the KNX application parameters.

## Rules for working on a live installation

- People live under these lamps. Announce programming, blinking and tests.
- One change, one observation. Ask the person in the room what they see; that
  was faster and more reliable than any log.
- Separate "service restored" from "cause found". Both entries above that end
  in "cause unknown" are in daily use regardless.
- A correlation is a lead. Several plausible explanations here (sleep mode,
  web server bug, the queue) were each disproved by one targeted measurement.
