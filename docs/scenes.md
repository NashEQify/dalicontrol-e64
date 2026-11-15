# Scenes

DALI scenes are the single most useful feature of the gateway once you drive
it from software: one command, one bus broadcast, any number of lamps change
at the same moment. Setting twenty channels one by one takes seconds and looks
like it; recalling a scene does not.

The gateway has 16 scene slots (0..15), global for the whole DALI line.

## How a recall works

`07 <slot> 00` sends the DALI "go to scene" broadcast. Every ballast looks into
its own scene table: if it has a level stored for this slot, it goes there; if
not, it ignores the command. Selectivity therefore comes from *membership*, not
from an address in the command. Lamps that are not members keep doing whatever
they did.

Consequences:

- A scene can cover one room or the whole flat; it is the same single command.
- "Room off" is a scene whose members are the room's channels, all at level 0.
- 16 slots is the hard budget. Spend them on what is pressed most.

## How to store a scene

The obvious reading of the web page's source (write the level table, then
commit) is wrong. The gateway does not take scene levels from a property you
write. On commit it **reads the current live level of every member ballast**
and stores that.

Working sequence for slot `S`:

1. **Set the lamps.** Send a level command (`0B <N-1> <level>`) for every
   channel that shall belong to the scene, including those that shall be off
   (level 0). The lamps really go to these levels; this is visible.
2. Wait about 200 ms.
3. **Write the membership** to property `D4`, count 1, start `S+1`:

   ```
   F6 C35101 D4 0001 <S+1:4 hex> <20 hex characters>
   ```

   The data is 10 bytes: 2 bytes of group membership (groups 1..16), then
   8 bytes of channel membership (channels 1..64), least significant bit first
   within each byte. Channel 12 only: `0000` + `0008000000000000`.
4. Wait about 125 ms (the web page does the same).
5. **Commit** with `06 <S> 00`.
6. **Verify** by reading property `D7` for that slot (see
   [http-api.md](http-api.md#reading)). This read is correct immediately. The
   channel-state property `CD` is not; do not use it here.

Name (`D3`) and fade time (`D5`) are optional.

Without step 3 the commit succeeds, the recall is acknowledged with `00`, and
no lamp moves, because the scene has no members.

To empty a slot, write an all-zero membership and commit. A recall then has no
effect.

All of this was done over HTTP. Storing a scene is a programming operation:
tell the people in the house first, because the lamps will change while you
do it.

## Using scenes from software

What worked in daily use:

- **Stored scenes for whole-room states** that are pressed often and must feel
  instant: room off, the two or three favourite moods per room.
- **Software scenes for everything else**: a list of channel levels sent one
  after the other. They need no slot and are easy to change, at the cost of a
  visible build-up.
- **Combine them.** A software scene that covers a whole room can start with
  the room's stored "off" scene and then send only the non-zero channels. A
  colourful scene went from 23 commands to 9 that way. The price is a short
  dark moment, which was accepted.
- **Keep broadcasts whole.** All-off is one command (`0A0000`), all-on is one
  command (`0BFFFF`). Never expand them into 64 writes.
- **Do not replay stored levels as single writes.** What `D7` reports for a
  slot is the gateway's record, not a verified copy of what each ballast
  stored. Use it for display and for knowing which channels a recall touches,
  not as a substitute recipe.
- **Keep one switch to turn stored scenes off.** If the scene memory is ever
  lost (for example after a new installation), software scenes for everything
  keep the house usable until the slots are programmed again.

`dalicontrol_e64.writer.Writer` implements the queueing side of this: a
one-command request (scene recall, broadcast) is a `Macro`, an optional leading
room-off is a reset.

## RGBW strips

A four-channel strip is four DALI channels. Two things to decide once and keep:

- White: either drive only the W channel, or R, G, B and W together. Mixing
  both conventions across scenes and switches gives "white" buttons that look
  different.
- One code path for every caller. In this project wall switches, dashboard
  tiles and sliders originally wrote through three different paths and a
  button labelled "white, full" showed red. One function that takes the
  complete target and one writer fixed it.
