# Strider (CPS-1 arcade) — sound system

Reverse-engineering notes and extraction details for the CPS-1 arcade version.
The two games share almost nothing in their sound systems, so the Mega Drive
version has its own write-up in [megadrive.md](megadrive.md). Shared
methodology is in [method.md](method.md).

Status legend: **[C] Confirmed** — read out of the disassembly and/or
cross-checked against live emulator state or a structural invariant.
**[H] Hypothesis** — plausible but not proven.

---

## 1. ROM set identity **[C]**

`roms/strider/` is an unzipped MAME romset. All 23 files match MAME 0.289's
`strider` set — *Strider (USA, B-Board 89624B-2)* — by name, size and CRC32
(`mame -verifyroms strider` → "romset strider is good").

Sound-relevant members:

| File | Size | CRC32 | Region |
|---|---|---|---|
| `09.12b` | 65 536 | `2ed403bc` | `audiocpu` — Z80 sound program |
| `18.11c` | 131 072 | `4386bc80` | `oki` @ `$00000` — ADPCM samples |
| `19.12c` | 131 072 | `444536d7` | `oki` @ `$20000` — ADPCM samples |

The ROM set is used **unmodified**; no patching is needed (§7).

## 2. Hardware **[C]**

From `mame -listxml strider`:

| Chip | Clock | Role |
|---|---|---|
| MC68000 | 10.000000 MHz | main CPU |
| Z80 | 3.579545 MHz | sound CPU, program = `09.12b` |
| YM2151 (OPM) | 3.579545 MHz | 8-channel FM |
| OKIM6295 | 1.000000 MHz | 4-channel ADPCM sample playback |

**Output is mono** (a single speaker device), unlike the Mega Drive version's
stereo.

### 2.1 Contrast with the Mega Drive version

| | Mega Drive | CPS-1 arcade |
|---|---|---|
| Sequencer runs on | **68000** (SMPS-68k variant) | **Z80** |
| Sequencer clocked by | VBlank (60 Hz) | YM2151 Timer A |
| FM chip | YM2612 (6 ch) | YM2151 (8 ch) |
| Second sound chip | SN76489 PSG — **never used** | — |
| Samples | Capcom 4-bit DPCM on the Z80, into the YM2612 DAC | OKIM6295, standard OKI 4-bit ADPCM |
| Sample count | 8 | 28 |
| Sample audio | 13.5 s total | 66.4 s total |
| Output | stereo | mono |

## 3. Z80 memory map **[C]**

Standard CPS-1. The bank mapping was verified live by writing `$F004` and
reading back through the Z80's address space in MAME.

| Z80 | Contents |
|---|---|
| `$0000`–`$7FFF` | ROM, direct → ROM `$0000`–`$7FFF` |
| `$8000`–`$BFFF` | ROM, banked → bank 0 = ROM `$8000` (**all `$FF`**), bank 1 = ROM `$C000` |
| `$D000`–`$D7FF` | RAM |
| `$F000` / `$F001` | YM2151 address / data |
| `$F002` | OKIM6295 |
| `$F004` | ROM bank select |
| `$F006` | OKI pin 7 / sample bank |
| **`$F008`** | **soundlatch — sound code from the 68000** |
| `$F00A` | soundlatch2 (fade) |

The ROM even identifies itself: ASCII `CPS CAPCOM` at `$0014`.

## 4. Driver structure **[C]**

Reset at `$0000`: `DI / IM 1 / LD SP,$D7FF / JP $005E`. Work RAM is cleared from
`$D000`.

The driver is **YM2151-timer driven**, not VBlank driven. `$0038` (IM 1
interrupt):

```
0038: DI
0039: LD   BC,$F001
003C: LD   A,(BC)          ; YM2151 status
003D: BIT  0,A             ; Timer A flag
003F: JR   z,$0052
0041: CALL $00EA           ; sequencer tick
0044: CALL $0B04
0047: CALL $03D1           ; per-channel update (banks to 0, reads $D01B)
004A: LD   BC,$F001
004D: LD   A,(BC)
004E: BIT  1,A             ; Timer B flag
0050: JR   z,$005B
0052: CALL $0102
0055: CALL $03BA           ; deferred OKI start (see 6.2)
0058: CALL $0137           ; sound command handler
005B: EI
005C: RETI
```

Measured rate: ~4 Timer-A and ~8 Timer-B events per video frame.

Handy RST vectors the driver uses as table helpers **[C]**:

| RST | Address | Effect |
|---|---|---|
| `RST $18` | `$0018` | `HL += A` |
| `RST $20` | `$0020` | `A = (HL + A)` — index a byte table |
| `RST $28` | `$0028` | `HL += A*2; DE = word at (HL)` — index a word table |
| `RST $30` | `$0030` | `POP HL; HL += A*2; HL = word at (HL); JP (HL)` — jump table right after the `RST` |

## 5. Sound command interface **[C]**

The 68000 writes a single byte:

| 68000 address | Meaning |
|---|---|
| **`$800180`** | **sound code** |
| `$800188` | fade |

Verified live: the game writes `$800180` from `pc=$004932` (real codes) and
`pc=$004948` (the idle value). `$FF` is the idle code.

Handler at `$0137`:

```
0137: LD   A,($D000)       ; the last code consumed
013A: LD   B,A
013B: LD   A,($F008)       ; the latch
013E: CP   B
013F: RET  z               ; unchanged -> nothing to do
0140: LD   ($D000),A
0143: LD   B,A
0144: AND  $F0
0146: CP   $F0
0148: JP   z,$02D2         ; $F0-$FF -> driver command
014B: LD   A,B
014C: BIT  7,A
014E: JP   nz,$0395        ; $80-$EF -> OKIM6295 sample
0151: ...                  ; $00-$7F -> FM sequence
```

Because the handler compares against the **last code consumed**, writing the
same value twice in a row is a no-op. That property is what makes the capture
harness safe (§7.2).

### 5.1 Code space **[C]**

| Codes | Count | Meaning |
|---|---|---|
| `$00`–`$77` | **120** | FM sequence |
| `$78`–`$7F` | — | alias back to `$00`–`$07` (see below) |
| `$80`–`$9C` | **29** | OKIM6295 sample |
| `$9D`–`$EF` | — | alias `$80`–`$9C` modulo 29 |
| `$F0`–`$F9` | 10 | driver commands |
| `$FA`–`$FF` | — | `$0394` = `RET`, no-op (`$FF` is the game's idle code) |

The FM lookup searches two tables in a loop, subtracting each table's length
until the index falls inside one:

```
0151: LD  HL,$0DCD         ; count byte  (= $2F -> 48 entries)
0154: LD  DE,$0DD0         ; word table, Z80 addresses
0158: LD  A,0 ; LD ($F004),A
015D: CALL $0171
0160: LD  HL,$8000         ; count byte  (= $47 -> 72 entries)  [bank 1]
0163: LD  DE,$8004         ; word table
0167: LD  A,1 ; LD ($F004),A
016C: CALL $0171
016F: JR  $0151            ; wrap round and try again
0171: LD  A,B ; LD B,(HL) ; INC B ; CP B ; JR c,$0179 ; SUB B ; RET
0179: POP BC               ; found: discard the return address and tail-call
017A: EX  DE,HL ; RST $28  ; DE = table[index]
```

48 + 72 = **120** entries, so codes `$78`–`$7F` wrap to `$00`–`$07`.

Of the 120 codes there are only **94 distinct sequence pointers**: `$2230` is
shared by `$00` and `$18`–`$2F` (25 codes — unused table filler), and `$9276`
by `$70`, `$76`, `$77`.

### 5.2 Driver commands `$F0`–`$F9` **[C]**

`$02D2` does `AND $0F` then `RST $30` against the 16-word table at `$02D6`:

| Code | Target | Effect |
|---|---|---|
| **`$F0`** | `$02F6` | **full stop / reset** — stop OKI, silence FM, clear state |
| `$F1` | `$02F9` | clear channel + sequence state |
| `$F2` | `$02FC` | clear the 8-entry channel array at `$D100` |
| `$F3` | `$0335` | clear `$D012` and the `$D300` sequence block |
| `$F4` | `$0347` | stop OKI then silence FM |
| `$F5` | `$034A` | silence FM |
| `$F6` | `$036B` | stop OKI, clear `$D023`/`$D024` |
| `$F7` | `$0385` | stop OKI voices 1 and 0 |
| `$F8` | `$0388` | stop OKI voice 0 (writes `$08`) |
| `$F9` | `$038E` | stop OKI voice 1 (writes `$10`) |
| `$FA`–`$FF` | `$0394` | `RET` |

## 6. OKIM6295 sample playback **[C]**

### 6.1 Code → phrase

`$0395`:

```
0395: AND  $7F                 ; $80+i -> i
0398: LD   A,($0DAF)           ; count = $1C
039B: INC  A                   ; 29
039E: CP   B / SUB B           ; index modulo 29
03A4: LD   HL,$0DB0            ; play-byte table
03A7: LD   D,A                 ; D = phrase index
03A8: RST  $20                 ; A = table[index]
03A9: LD   E,A
03AA: AND  $F0 / RRCA
03AD: LD   ($F002),A           ; stop that voice first
03B0: LD   ($D002),DE          ; defer the start
03B6: LD   ($D001),$FF         ; pending flag
```

and the deferred half, called from the interrupt at `$03BA`:

```
03C3: LD   HL,($D002)          ; L = play byte, H = phrase index
03C6: LD   A,H / OR $80
03C9: LD   ($F002),A           ; sample select: $80 | phrase
03CC: LD   A,L
03CD: LD   ($F002),A           ; play: voice mask in bits 4-7, atten in 0-3
```

So **sound code `$80+i` selects OKI phrase `i` directly**, and the byte at
`$0DB0+i` is the chip's play byte. The `AND $F0 / RRCA` that precedes it turns
the play byte's voice mask (bits 4–7) into the chip's *stop* mask (bits 3–6) —
`$10`→`$08`, `$20`→`$10`.

All 29 play bytes: `$10` except `$20` for phrases 13 and 17, and `$21` for
phrase 24. So almost everything plays on OKI voice 0 at full volume; phrases
13/17 use voice 1, and phrase 24 uses voice 1 with attenuation 1.

### 6.2 Phrase table **[C]**

The OKIM6295 reads its own table from the start of the sample ROM: 128 entries
of 8 bytes, each `[0..2]` = 24-bit big-endian start, `[3..5]` = end, `[6..7]`
unused.

* entry 0 is null (`start = end = 0`)
* entries **1–28 are populated** — the real samples
* entries 29–31 are zero, 32–127 are `$FF` padding

The 28 populated entries are **perfectly contiguous** (0 non-contiguous joins),
spanning `$000800`–`$03D6FF` = 251 648 bytes, and the first sample starts at
`$000800`, immediately after the `$400`-byte table area. `$03D700`–`$03FFFF` is
unused.

Consequence worth noting: sound code `$80` maps to the **null** phrase 0. MAME's
OKIM6295 only starts a voice when `start < end`, so the write is a no-op and the
code is silent by design — the capture confirms it (peak 0). It is a dead entry
in the driver's 29-code table, so there are 29 codes but only 28 real samples.
It gets a row in `sounds.csv` and no WAV file, since a zero-sample WAV is a trap
for players and downstream tools.

### 6.3 Rate **[C]**

`$007B: LD A,($0DCF) ; LD ($F006),A` and `$0DCF` = `$01`, so OKI pin 7 is
**HIGH** → divider /132 → **1 000 000 / 132 = 7575.76 Hz**.

### 6.4 Codec **[C]**

Standard OKI/Dialogic 4-bit ADPCM — *not* a Capcom-specific delta table, unlike
the Mega Drive version. Two samples per byte, high nibble first, 12-bit signed
output, decoded with MAME's step/diff construction:

```
index_shift = [-1,-1,-1,-1, 2, 4, 6, 8]          indexed by nibble & 7
stepval(s)  = floor(16 * (11/10)^s)               s = 0..48
diff(s,nib) = sign * (stepval*b2 + stepval/2*b1 + stepval/4*b0 + stepval/8)
signal starts at -2, step at 0, signal clamped to [-2048, 2047]
```

Decoded output looks like real audio: sensible attack/decay envelopes, DC
offsets under ~3 % of full scale, zero-crossing rates of 1–2.4 kHz.

## 7. Extraction method

### 7.1 No ROM patching needed **[C]**

The **"Game Mode" dipswitch** (`DSWC` bit 7, value 0 = Test) puts the board on
its test screen. Measured there over 900–2400 frames:

* **0** real sound commands from the game (only the idle `$FF`, 1145 of them)
* **0** OKIM6295 writes
* YM2151 writes **only** to registers `$10`, `$11`, `$12`, `$14` — Timer A/B
  period and the timer control/IRQ-reset register, i.e. pure housekeeping
* rendered audio **exactly zero**: peak 0, one distinct sample value, for 30 s

So the arcade version needs none of the ROM surgery the Mega Drive version did
(no muting of queue writers, no relocated main loop, no checksum fixup). There
is also **no DC offset** to remove.

`scripts/arcade_make_state.lua` verifies this silence (300 consecutive frames
with zero significant YM writes, zero OKI writes and zero real commands) before
saving the state the captures load.

### 7.2 Neutralising the idle `$FF` **[C]**

The game keeps writing `$FF` every couple of frames even on the test screen.
That is harmless in itself (`$FF` → `$0394` = `RET`), but it could land between
the harness's write and the Z80 polling `$F008`, losing the command.

So after poking, a MAME write tap on `$800180` rewrites any further `$FF` into
the code we just poked. Since `$0137` compares each new code against the last
one consumed (`$D000`) and returns early when they match, the rewritten value
is a guaranteed no-op — and the race disappears entirely. The harness also
reads back Z80 `$D000` to record which code the driver actually consumed.

### 7.3 Channel allocation — the music / SFX discriminator **[C]**

The single most useful thing the captures revealed is that the driver
**partitions the YM2151**. Across all 94 distinct FM sequences:

| YM2151 channels keyed on | Codes | What they are |
|---|---|---|
| 1, 2, 3, 4, 5, 6 | 24 (`$00`–`$17`) | music |
| 7 only | 47 | FM sound effect |
| 8 only | 23 | FM sound effect |

There is **no overlap**: music never touches channels 7–8, and FM effects never
touch 1–6. So sound effects can never steal a voice from the music — the
opposite of the Mega Drive version, where an SFX explicitly marks and takes over
a music track's FM channel ([megadrive.md §4.2](megadrive.md)).

This is what the extraction uses to classify, rather than "does it loop", which
would be wrong: of the 24 music entries only 14 loop, and the other 10 are
finite cues 2.9–11.7 s long (stage clear, game over and similar) that a
looping test would misfile as sound effects.

Also notable: **no FM sequence triggers an OKIM6295 sample.** In many CPS-1
games the music drives the OKI for drums; here the sample chip is reserved
entirely for the 29 direct `$80`–`$9C` codes. Measured: 0 OKI writes across all
94 FM captures.

### 7.4 End-of-sound detection **[C]**

Unlike the Mega Drive driver, this one writes to the YM2151 constantly. So
"quiet" is defined as no OKI writes and no YM writes **outside** the four timer
registers, for 90 frames. Capture caps: 3600 frames (60 s) for FM codes,
900 (15 s) for OKI codes.

This also gives the music/SFX classification for free: a capture that runs to
the cap is still driving the chips and is therefore looping music; one that goes
quiet on its own is a sound effect.

### 7.5 Emulator specifics

MAME tap semantics, subscription lifetime, `-seconds_to_run` and the display
settings are documented once in
[method.md §3](method.md#3-mame-specifics-worth-writing-down).

## 8. Results

| Metric | Value |
|---|---|
| FM sequence codes | 120 (94 distinct pointers) |
| — of which music | 24 (`$00`–`$17`): 14 looping BGM + 10 finite cues |
| — of which FM sound effects | 70 (`$30`–`$77`, on YM channels 7/8) |
| OKIM6295 sample codes | 29 (28 real phrases + 1 null → `$80` is silent) |
| **Total sound effects** | **98** (70 FM + 28 sampled) |
| OKI ADPCM extracted | 28 samples, 251 648 bytes, 503 296 samples, 66.4 s |
| OKI playback rate | 7575.76 Hz |
| FM sequences that also trigger OKI | 0 |
| Output format | 48 kHz / 16-bit / **mono** |
| Silent outputs | 1 (`$80`, the null phrase — by design) |
| Identical-output groups | 0 |
| Malformed VGM | 0 |

See `output/arcade/sounds.csv` for the per-code results.

### 8.1 Cross-validation of the sample pipeline **[C]**

Two fully independent paths produce a duration for each OKIM6295 sample:

1. **Static** — parse the phrase table out of the ROM, decode the ADPCM with
   the Python implementation in `scripts/arcade_info.py`, divide by 7575.76 Hz.
2. **Rendered** — let MAME's own OKIM6295 emulation play the sample, capture the
   analogue output, and measure the trimmed WAV.

All **28 / 28 agree to within 11 ms**, which is exactly the 10 ms pre-roll
margin the trimmer adds:

```
 code phrase  static s  rendered s    delta
  $81      1     0.406       0.616   +0.010
  $8D     13     6.758       6.969   +0.011
  $98     24     7.772       7.983   +0.011
  $9C     28     3.244       3.454   +0.010
  ... 28/28 OK, 0 mismatches
```

So the phrase table parse, the ADPCM decoder, the pin-7 rate derivation and the
capture harness are all confirmed against each other. `$80` renders silent,
which independently confirms that phrase 0 is the null entry.

## 9. Open questions / not established

* The YM2151 sequence data format itself (note/duration encoding, coordination
  flags) was not decoded — unnecessary, because the sounds are rendered by the
  real driver rather than re-interpreted. **[H]**
* The music / FM-effect split is taken from the YM2151 channel allocation
  (§7.3), which separates perfectly and is therefore strong evidence — but it
  is still an inference from behaviour rather than from a label in the ROM.
  Within the music group, "looping BGM" vs "finite cue" is just whether the
  capture hit the 60 s cap. **[H]**
* `$D000`–`$D7FF` RAM layout is only partially identified: `$D000` last code,
  `$D001` OKI-start pending flag, `$D002`/`$D003` deferred OKI phrase and play
  byte, `$D010`/`$D020` tempo values derived from `$0DCE`/`$8001`, `$D011`/`$D012`
  sequence priorities, `$D100` 8-entry channel array, `$D300` sequence block.
* The VGM files are **structurally** validated (header fields, command-stream
  walk, accumulated waits vs the total-samples field) but have **not** been
  played back — no VGM player is installed. YM2151 + OKIM6295 with an attached
  type-`$8B` ROM data block is a less common combination than the Mega Drive
  version's YM2612 + SN76489, so treat playback as unverified. **[H]**
* The board's own test mode reportedly includes a sound test; it was not used,
  because writing the sound latch directly covers the whole code space and is
  simpler to automate. The code→sound mapping here is therefore the *driver's*,
  which is a superset of whatever any menu exposes.

## 10. Tools

| Tool | Use |
|---|---|
| MAME 0.289 (`strider`), headless | emulation, chip-write taps, WAV capture |
| `z80dis` 1.0.6 | `scripts/disz80.py --target arcade` |
| Python 3.10 venv at `.venv` | everything else |
