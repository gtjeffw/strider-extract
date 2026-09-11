# Method — how these rips are made

Shared groundwork behind both platforms. Platform specifics are in
[megadrive.md](megadrive.md) and [arcade.md](arcade.md).

---

## 1. The shape of the problem

A "sound rip" that only pulls sample data out of a ROM misses most of the
content, because most retro game audio is *synthesised on the fly* — FM
operators, PSG tones, envelopes — and only some of it is sampled playback. The
audible result exists only when the game's own sound driver runs on the real
chips.

So the approach here is not to reimplement anything. It is:

1. Reverse engineer just enough of the sound driver to learn **how the game
   asks for a sound** — the one byte, port or memory location that means
   "play number N".
2. Get the machine into a state where **nothing else is making noise**.
3. Have an accurate emulator poke that request, and record what comes out.

Step 1 is usually a few hundred bytes of disassembly. Step 2 is where the real
work is. Step 3 is nearly free once 1 and 2 are done.

## 2. Why an emulator, and which one

MAME, headless. It gives three things nothing else here would:

* **Accurate chip emulation** for YM2612, SN76489, YM2151 and OKIM6295 — the
  actual synthesis, which is the whole point.
* **A Lua API** that can read and write CPU memory, set dipswitches, install
  memory taps, save and load states, and step frames. That is what makes the
  whole thing scriptable rather than a manual recording session.
* **`-wavwrite`**, which captures the emulated analogue output directly rather
  than going through the host audio stack.

`-wavwrite` works with `-sound none`; the WAV is written by the sound manager
regardless of which output module is selected.

## 3. MAME specifics worth writing down

These cost the most time, so they are recorded rather than rediscovered.

### 3.1 Write-tap semantics

`space:install_write_tap(start, end, name, fn)` where `fn(offset, data, mask)`
returns the (possibly modified) data:

* On a **16-bit bus** (68000), `offset` is the **word-aligned** address and
  `data` has the byte **mirrored into both halves**. `mask` selects which byte:
  `$FF00` is the even address, `$00FF` the odd one.
* On an **8-bit bus** (Z80), `offset` is the exact byte address and `data` is
  the byte.
* Ranges on a 16-bit space must start even and end odd, or MAME refuses the
  range.
* A tap can **rewrite** a write by returning different data. That is used on
  the arcade side to neutralise the game's idle command byte.

### 3.2 Keep subscriptions alive

Notifier and tap objects returned by `emu.add_machine_frame_notifier` and
`install_write_tap` are garbage-collected if nothing holds a reference. As
locals in an autoboot chunk they silently stop working once the chunk returns.
**Store them in globals.**

### 3.3 `-seconds_to_run` is absolute machine time

Not "run for N more seconds". After loading a save state taken at ~16 s, a
`-seconds_to_run` below that makes MAME exit immediately with an empty capture.
Set it comfortably above `state time + capture length`.

### 3.4 Never let it take the screen

`-video none` alone still brings up a fullscreen SDL surface on macOS. The
project's `mame.ini` sets

```
video        none
videodriver  dummy
window       1
```

`videodriver dummy` is what actually prevents any window being created, and it
also makes runs measurably faster (~2400 % → ~3000 % on this machine). MAME's
default `inipath` includes `.`, so every run started from the project root
picks it up automatically — including ad-hoc ones.

## 4. Getting to silence

This is the crux. A game left to its own devices plays attract-mode music and
effects, which contaminate every capture. Three strategies, in increasing order
of intrusiveness:

1. **A built-in idle screen.** If the board has a service/test mode reachable by
   a dipswitch, the game usually sits there issuing no sound commands. Free, no
   patching. This is what the arcade version uses.
2. **Neutralise the game's sound requests.** Find *every* instruction that
   enqueues a sound and patch it out. Needs an exhaustive opcode scan, not a
   guess — see §5.
3. **Park the CPU.** Let the game boot fully (so interrupts, video and any
   sound-CPU upload are initialised), then trap its main loop. This is what the
   Mega Drive version uses, on top of 2.

Two lessons from doing it the hard way:

* **Do not freeze from boot.** Interrupts are typically enabled by game code
  well after reset. Freezing at reset gives a completely silent machine that
  looks like a driver bug. (Measured on the Mega Drive version: `SR = $2704`,
  interrupts masked, driver never ticks.)
* **Verify the freeze engaged; don't assume it.** Many mode handlers run their
  whole sequence internally and only return to the main loop occasionally, so
  writing a "stop" flag does not take effect when you wrote it. Prove the parked
  loop is *currently* spinning — e.g. thousands of reads of the loop's own
  variable per frame, for tens of consecutive frames.

And whichever strategy is used, **measure the idle baseline**: count chip writes
and check the rendered audio is actually silent before capturing anything.

One approach that sounds appealing and does **not** work: leaving the ROM
untouched and waiting for the attract mode to trigger each sound by itself, then
recording it. Attract mode has music playing continuously, so the chips are
never idle and no individual effect can be isolated. Tried on the Mega Drive
version across 14 000 frames with a "settled bus" precondition: zero captures.

## 5. Find every sound request, exhaustively

A table of "play sound N" stubs is a trap: game code often calls the driver from
other places too. Scan the whole ROM for the instruction encoding instead.

On the Mega Drive version, scanning for `move.b <ea>,$FF9C0A/0B/0C.w`
(`$11 $C0..$FF` + extension words + the destination) found 7 writers outside the
89-entry stub table, **3 of which the stub table does not reach at all**. Those
three would have contaminated captures in ways that are very hard to notice.

When patching such a site out, preserve its side effects. One of Strider's is
`move.b (a2)+,$9C0A.w`, whose caller depends on the post-increment; it becomes
`move.b (a2)+,d0` + `nop`, not three `nop`s.

## 6. Patch ROMs safely

* **Never modify the original.** Write patched copies into `build/`.
* **Recompute header checksums.** Mega Drive boot code sums words from `$000200`
  to the ROM-end field and hangs on mismatch. A patch without a checksum fixup
  produces a dead cartridge that looks like a bad patch.
* **Use ROM padding for injected code.** Strider has 4106 bytes of `$FF` between
  the Z80 blob and the DAC tables; the relocated main loop lives there.
* **Audit the patch afterwards.** Diff against the original, group the changed
  bytes into ranges, and assert that none of them overlap sound data. That check
  is what turns "I think I only touched code" into a fact.

## 7. Knowing when a sound has ended

Capture windows should come from the sound, not from a guess.

* **Sequenced sounds** (FM/PSG) write to the chips continuously while playing,
  so "no significant writes for N frames" works well. Stop *after* that window
  so the FM release tail is included.
* **Filter housekeeping first.** A driver may write timer registers every tick
  forever. On the arcade side the only idle writes are YM2151 `$10/$11/$12/$14`;
  counting those as activity would mean no sound ever appears to end.
* **Autonomous sample chips need a fixed window.** An OKIM6295 needs three bus
  writes to start a sample and then plays it with no further traffic, so
  write-based detection measures the detection window, not the sample. This bug
  silently truncated every arcade phrase longer than ~1.3 s to exactly 1.497 s
  (= 90 frames) before it was caught. Give such chips a window longer than their
  longest sample and take the real length from the audio.
* **Looping music needs a cap**, and should be labelled as capped rather than
  silently presented as a complete track.

## 8. WAV post-processing

`scripts/wavutil.py`, applied to both platforms:

* **DC offset removal.** With the YM2612's DAC enabled and a value latched,
  MAME's idle output sits at a constant offset (+252 on the Mega Drive side)
  rather than at zero. Real hardware AC-couples its output, so subtracting the
  offset measured from the guaranteed-silent lead-in is the *more* faithful
  result, not a cosmetic tweak. The arcade side needs none — its idle output is
  exactly zero.
* **Conservative trimming.** 10 ms before the first audible sample, 200 ms after
  the last, so release envelopes and delayed echoes survive.
* **No gain, no normalisation, no dither.** Levels are as the chips produced
  them. The Mega Drive output is quiet (peaks ~3000/32768) and the arcade output
  is loud (peaks ~28000); that difference is real and is preserved.
* **Native channel count.** Stereo for the Mega Drive, mono for CPS-1.
* **Silent results get a row, not a file** — a zero-sample WAV breaks players
  and downstream tools.

## 9. Validate against the game, not against yourself

Two kinds of check are worth the effort:

* **Make the game's own code do the lookup.** For the Mega Drive sound test, a
  ROM variant leaves the real dispatch instruction intact and calls it with each
  menu index, then compares the sound id the game enqueues against the table
  read statically. All 66 matched, and the resulting `(port, register, value)`
  chip-write sequences were identical to the harness's — so the two paths drive
  the chips identically and the WAVs are equivalent by construction.
* **Derive the same number two independent ways.** Every arcade OKI sample's
  duration was computed both by decoding the ADPCM in Python from the phrase
  table and by measuring MAME's rendered audio. All 28 agreed to within 11 ms,
  which simultaneously confirms the table parse, the decoder, the sample-rate
  derivation and the capture harness.

Cheap checks worth running every time: nothing silent that shouldn't be, no
effect truncated by its window, no stray sound requests during capture, and a
hash-grouping pass to find aliases and accidental duplicates.

## 10. Adapting this to another game

Roughly the order that worked:

1. Locate the sound driver and its work RAM. Look for a large data region, then
   disassemble around any code that writes the sound chip ports.
2. Find the **sound request mechanism** — a queue byte, a latch address, a
   command port.
3. Find the driver's **table-of-tables**: the structure it dereferences to get
   its song and effect pointer tables, and the `cmp`/bounds checks in its
   dispatcher that define the valid id ranges.
4. **Sanity-check structurally.** Pointer tables should be monotonic, contiguous,
   and should abut the data they point into. If a count is right, the table will
   end exactly where the next structure begins. That is how the Mega Drive
   counts (31 songs, 48 effects) and the arcade counts (48 + 72 sequences, 29
   sample codes) were each confirmed without running anything.
5. Scan exhaustively for sound requests (§5), get to silence (§4), verify it.
6. Poke ids and capture. Derive channel usage from the actual chip writes rather
   than from the data format — it is less work and it is what really happened.
7. Validate with §9.
