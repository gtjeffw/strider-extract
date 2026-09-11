# Strider (Mega Drive) — sound system

Reverse-engineering notes and extraction details for the Sega Mega Drive /
Genesis version. Shared methodology lives in [method.md](method.md); the
arcade version is in [arcade.md](arcade.md).

Status legend used throughout:

* **[C] Confirmed** — read out of the disassembly, and/or cross-checked against
  live emulator state or a structural invariant that could not hold by accident.
* **[H] Hypothesis** — plausible but not proven. Treated as such everywhere.

All addresses are simultaneously ROM file offsets and 68000 addresses, because
the cartridge is mapped at `$000000`.

---

## 1. ROM identity **[C]**

| Field | Value |
|---|---|
| File | `roms/Strider (USA, Europe).md` |
| Size | 1 048 576 bytes (1 MiB) |
| SHA-1 | `26fe42d13a01c8789bbad722ebac05b8a829eb37` |
| MD5 | `fcab622a6e56f7e9b1e0907ab5a630df` |
| Console name (`$100`) | `SEGA MEGA DRIVE ` |
| Copyright (`$110`) | `(C)SEGA 1990.SEP` |
| Overseas name (`$150`) | `STRIDER HIRYU` |
| Serial (`$180`) | `GM 00001112-00` |
| Header checksum (`$18E`) | `$B7AC` |
| I/O support (`$190`) | `J` (joypad) |
| ROM range (`$1A0`) | `$000000`–`$0FFFFF` |
| Region (`$1F0`) | `UE` |

### 1.1 Boot-time checksum **[C]**

`$000336`–`$000348` sums big-endian words from `$000200` to the ROM-end field at
`$0001A4` inclusive and compares the result with the word at `$00018E`. A
mismatch branches to `$0003C6`, which ends in the hang loop `$0003E4: bra $3E4`.
Verified: the algorithm reproduces `$B7AC` exactly on the untouched ROM.
**Any patched copy must have this word recomputed** — `scripts/patch_rom.py`
does so.

---

## 2. Architecture: who does what **[C]**

Strider is **not** a conventional Z80-driver Mega Drive game.

* The **68000 runs the entire music and SFX sequencer** — an SMPS-68k-family
  driver, Capcom-modified. It writes the YM2612 at `$A04000`–`$A04003` and the
  SN76489 at `$C00011` directly, bracketing accesses with Z80 bus requests via
  `$A11100`.
* The **Z80 runs only a 368-byte 4-bit-DPCM sample player**. The 68000 hands it
  a single command byte in Z80 RAM.
* Measured over all 87 playable sounds: **the SN76489 (PSG) is never written at
  all.** The driver contains PSG track support and Strider's sound data never
  uses it. (Zero PSG writes across 87 captures — see §12.)

---

## 3. Driver invocation **[C]**

The level-6 (VBlank) vector at `$000078` points at `$002616`. That handler ends
at `$002790` with `rte` and calls the driver entry either once or twice:

```
002760: addq.w  #$1, $f09e.w
002764: btst.b  #$6, $fd1b.w      ; bit 6 of the console version byte = PAL
00276A: beq.b   $2782
00277C: jsr     $b00f8.l          ; extra tick (PAL: 50 Hz -> 2 ticks/frame)
002782: jsr     $b00f8.l          ; normal tick
002788: addq.w  #$1, $e11a.w
002790: rte
```

`$FFFD1B` is loaded at `$000352` from `move.b d7,$fd1b.w`, where `d7` came from
the version register `$A10001`. Bit 6 of that register is the **PAL flag**, so:

* **NTSC: one driver tick per frame at 60 Hz.**
* PAL: two ticks per frame at 50 Hz.

This is why the extraction uses MAME's `genesis` (USA, NTSC) driver — it gives
the single-tick 60 Hz timing.

---

## 4. Driver work RAM **[C]**

| Address | Meaning |
|---|---|
| `$FF9C00` | priority of the currently-playing sound |
| `$FF9C01` | tempo counter (reloaded from song header `+$5`) |
| `$FF9C02` | tempo reload value (song header `+$5`) |
| `$FF9C03` | driver mode: `0` = run, non-zero = paused, negative = restore |
| `$FF9C08` | scratch |
| `$FF9C09` | sound id currently being started |
| **`$FF9C0A`** | **sound request queue slot 0** |
| **`$FF9C0B`** | **sound request queue slot 1** |
| **`$FF9C0C`** | **sound request queue slot 2** |
| `$FF9C0E` | group select (`$00` = FM tracks, `$80` = PSG tracks) |
| `$FF9C0F` | flag set by the `$B10B6` handler |
| `$FF9C18` | pointer to the current **song** FM voice table |
| `$FF9C1C` | pointer to the current **SFX** FM voice table |
| `$FF9C40` | base of the track state array, **`$30` bytes per track** |

`$D9` (§7) zeroes `$FF9C00` for `$E4 * 4 = 912` bytes, i.e. the driver owns
`$FF9C00`–`$FF9F8F`.

### 4.1 Track structure (`$30` bytes) **[C]**

| Offset | Meaning |
|---|---|
| `+$00` | flags: bit7 = active, bit4 = gate active, bit2 = channel overridden by SFX, bit1 = ? |
| `+$01` | channel byte (see §8.3) |
| `+$02..3` | word copied from the header |
| `+$04` | current sequence pointer (long) |
| `+$08..9` | word from the track header entry |
| `+$0A` | last value written to YM `$B4+ch` (panning / AMS / FMS); initialised `$C0` |
| `+$0B` | ? |
| `+$0D` | initialised `$30` |
| `+$0E` | duration counter; decremented each tick, 0 → advance the sequence |
| `+$1F` | note gate / length counter |
| `+$20` | loop / repeat counter |
| `+$21` | flag set by sequence byte `$06` |
| `+$27..$2E` | 8 scratch bytes, zeroed on init |

### 4.2 Track array layout **[C]**

Music tracks start at `$FF9C40`; SFX tracks are allocated from two parallel
pointer tables which the driver indexes by `(channel_byte - 2) * 4`:

`$0B06CC` (primary) and `$0B06EC` (secondary):

| channel byte | `$0B06CC` entry | `$0B06EC` entry |
|---|---|---|
| `$02` | `$FF9CD0` | `$FF9E20` |
| `$03` | `$FF9CD0` | `$FF9E20` |
| `$04` | `$FF9D00` | `$FF9E50` |
| `$05` | `$FF9D30` | `$FF9E80` |
| `$06` | `$FF9D90` | `$FF9EB0` |
| `$07` | `$FF9DC0` | `$FF9EE0` |
| `$08` | `$FF9DF0` | `$FF9F10` |
| `$09` | `$FF9DF0` | `$FF9F10` |

The SFX start routine sets bit 2 of `+$00` in the `$0B06CC` entry (marking the
music track whose channel is being stolen) and builds the new SFX track in the
`$0B06EC` entry. The exact reason `$02`/`$03` and `$08`/`$09` alias is not
established. **[H]**

---

## 5. Sound bank descriptor at `$0B0000` **[C]**

The driver hardcodes `movea.l #$B0000,a0` (at `$B0502`, `$B0620`, `$B0710`,
`$B07A0`) and dereferences it through `$B08C6`:

```
0B08C6: lsl.w   #$2, d0
0B08C8: movea.l (a0, d0.w), a0
0B08CC: rts
```

| Offset | Value | Meaning |
|---|---|---|
| `+$00` | `$0B009C` | priority table, index `id - $81` |
| `+$04` | `$0B644C` | end of the SFX pointer table / start of SFX data |
| `+$08` | `$0B0020` | **song pointer table**, index `id - $81` |
| `+$0C` | `$0B638C` | **SFX pointer table**, index `id - $A0` |
| `+$10` | `$0B009C` | priority table (duplicate) |
| `+$14` | `$0B0020` | song pointer table (duplicate) |
| `+$18` | `$0000A0` | **SFX id base** = `$A0` |
| `+$1C` | `$0B00F8` | driver entry point |

---

## 6. Sound id space **[C]**

From the queue dispatcher (`$B0502`) and the start dispatcher (`$B05D6`):

```
$B05E4: cmpi.b #$a0,d0 ; bcs $b0790     -> song   ($81..$9F)
$B05EC: cmpi.b #$d0,d0 ; bcs $b061e     -> SFX    ($A0..$CF)
$B05F2: cmpi.b #$dc,d0 ; bcs $b0606     -> driver command ($D8..$DB)
$B05B0: subi.b #$50,d0 ; move.b d0,$A01FFF  -> DAC ($D0..$D7 -> Z80 $80..$87)
```

| Range | Count | Kind | Lookup |
|---|---|---|---|
| `$81`–`$9F` | **31** | Music sequence | `*($0B0020 + 4*(id-$81))` |
| `$A0`–`$CF` | **48** | SFX sequence | `*($0B638C + 4*(id-$A0))` |
| `$D0`–`$D7` | **8** | DPCM sample (Z80) | `move.b #(id-$50),$A01FFF` |
| `$D8`–`$DB` | 4 | Driver commands | jump tables `$B05A4` / `$B0612` |

**31 + 48 + 8 = 87 playable sound ids.**

Structural corroboration: the song table occupies exactly `$0B0020`–`$0B009B`
(31 × 4 bytes) and is immediately followed by the priority table at `$0B009C`;
the SFX table occupies exactly `$0B638C`–`$0B644B` (48 × 4) and is immediately
followed by the first SFX header at `$0B644C`. Both are strictly increasing.
The counts cannot be off by one.

---

## 7. Driver commands `$D8`–`$DB` **[C]**

Dispatched without a priority check through 4-byte `bra.w` slots:

```
$B0612: bra.w $b0a12   ; $D8
$B0616: bra.w $b08ce   ; $D9
$B061A: bra.w $b0904   ; $DA
```

| Id | Handler | Effect |
|---|---|---|
| `$D8` | `$0B0A12` | fade / stop variant (not fully decoded) **[H]** |
| **`$D9`** | `$0B08CE` | **full reset**: YM `$2B`=`$80`, YM `$27`=`$00`, zero `$FF9C00`+912 bytes, silence FM (`$B0B22`) and PSG (`$B0B6C`) |
| `$DA` | `$0B0904` | clear the track array only: YM `$2B`=`$80`, `$27`=`$00`, zero `$FF9C40`+336 bytes |

`$D9` is the reliable "stop everything" command.

---

## 8. Sequence data formats

### 8.1 Requesting a sound **[C]**

`$B0502` walks the three queue bytes `$FF9C0A/0B/0C`. For each non-zero byte it
looks up `priority_table[id-$81]`, compares it against `$FF9C00`, and if it wins
stores the id in `$FF9C09` and clears the slot. Ids `>= $D0` bypass the priority
comparison.

### 8.2 Header layouts **[C]**

Songs and SFX differ. Both are read straight out of the driver's start routines
(`$0B07A0` for songs, `$0B061E` for SFX).

**SFX header** (`$0B061E`):

| Offset | Size | Meaning |
|---|---|---|
| `+$00` | word | FM voice table offset, **relative to the header** |
| `+$02` | byte | always `$01` in this ROM |
| `+$03` | byte | track count |
| `+$04` | 6 × n | track entries |

SFX track entry (6 bytes): `[flags] [channel byte] [signed word: data offset relative to the header] [word: copied to track +$08]`

**Song header** (`$0B07A0`):

| Offset | Size | Meaning |
|---|---|---|
| `+$00` | word | FM voice table offset, relative to the header |
| `+$02` | byte | track count |
| `+$04` | byte | initial track flag bits (OR'd with `$80000000`) |
| `+$05` | byte | tempo (copied to both `$FF9C01` and `$FF9C02`) |
| `+$06` | 4 × n | track entries |

Song track entry (4 bytes): `[signed word: data offset relative to the header] [word: copied to track +$08]`.
Song tracks carry **no** channel byte; the driver accumulates the eight signed
deltas at `$0B08BE` (`06 FA 01 01 02 01 01 00`) instead, one per track, so the
channel bytes come out as `$06, $00, $01, $02, $04, $05, $06`. Every one of the
31 songs has 7 tracks and this same channel assignment — the six FM channels
plus one extra `$06` track.

### 8.3 Channel byte **[C]**

For FM tracks the channel byte is the **YM2612 key-on channel selector** used
directly in register `$28`: `0,1,2` = FM channels 1–3 (port 0), `4,5,6` = FM
channels 4–6 (port 1). Bit 7 set means a PSG track (`byte >> 3` selects it) —
never used by this ROM's data. Confirmed empirically: SFX `$A0` has channel byte
`$05` and its capture keys on FM channel 5; `$A2` has `$02` and keys on FM
channel 3; `$B3` has `$04` and keys on FM channel 4.

### 8.4 Worked example, SFX `$A0` **[C]**

Header at `$0B644C` = `0017 0101 8005 000A 0006 …`

* voice table at `$0B644C + $17` = `$0B6463`
* `+$03` = `$01` → one track
* track entry `80 05 000A 0006` → flags `$80`, channel byte `$05` (FM channel 5),
  sequence data at `$0B644C + $0A` = `$0B6456`, extra word `$0006`
* sequence data `$0B6456`–`$0B6462` (13 bytes)
* voice table `$0B6463`: `C6 0F 3A 01 03 04 03 D8 4E 0C 4E 00 00 00 00 12 C7 07 D2 0F 0F 0F 0F 00 00`
  — exactly **25 bytes**, the standard SMPS FM voice size
* the next SFX header is `$0B647C` = `$0B6463 + $19`, perfectly contiguous

The capture of `$A0` writes port-1 registers `$B1`=`$3A`, `$31/$35/$39/$3D` =
`01/03/04/03`, `$51/$55/$59/$5D` = `D8/4E/0C/4E`, `$61/$65/$69/$6D` = `00`,
`$71/$75/$79/$7D` = `12/C7/07/D2`, `$81/$85/$89/$8D` = `0F`. Those are voice
bytes `[2]`, `[3..6]`, `[7..10]`, `[11..14]`, `[15..18]`, `[19..22]` in order —
an independent confirmation of both the header layout and the voice format.

### 8.5 Sequence byte semantics (partial) **[C]/[H]**

From `$0B01C8`:

| Byte | Meaning |
|---|---|
| `>= $40` | note + duration (handlers `$B0BA4`, `$B0218`, `$B0252`, `$B02CE`) **[C]** |
| `$30`–`$3F` | `& $0F` → track `+$20` (loop / repeat count) **[C]** |
| `$20`–`$2F` | `& $0F` → track `+$1F` (gate length) **[C]** |
| `$06` | set track `+$21` = 1 **[C]** |
| other `< $40` | coordination-flag dispatch at `$0B0C8A` **[C]**, individual flags not fully decoded **[H]** |

One decoded coordination flag worth noting: `$0B0E20` does
`move.b (a4)+,d0 ; add.b d0,$FF9C0A` — **a sequence can enqueue another sound**.

---

## 9. Game-side plumbing

### 9.1 "Play sound N" stub table at `$020000` **[C]**

89 stubs in two shapes:

```
020000: move.b #$81, $9c0a.w ; rts            (8 bytes, 42 of them)
020148: move.b #$a0, $e188.w ; bra.w $20324   (10 bytes, 46 of them)
02031C: move.b #$cf, $e188.w                  (6 bytes, falls through)
```

Order: `$81`–`$9F`, then `$D0`–`$D6`, `$D8`, `$D9`, `$D7`, then `$A0`–`$CF`.
Note `$AA` uses the direct shape even though it is an SFX.

The "queued" shape stashes the id in `$FFE188` and jumps to the queue-slot
allocator at `$020322`, which writes it into the first free of
`$FF9C0A`/`0B`/`0C`:

```
020322: tst.b $9c0a.w ; bne $20330 ; move.b $e188.w,$9c0a.w ; rts
020330: tst.b $9c0b.w ; bne $2033e ; move.b $e188.w,$9c0b.w ; rts
02033E: tst.b $9c0c.w ; bne $2034a ; move.b $e188.w,$9c0c.w ; rts
```

The game calls these stubs 120 times by absolute `jsr`, hitting 63 distinct
stubs.

### 9.2 Every place the game can enqueue a sound **[C]**

Found by an exhaustive opcode scan for `move.b <ea>,$FF9C0A/0B/0C.w`
(`$11 $C0..$FF … $9C0A/0B/0C`) — see `scan_queue_writers()` in
`scripts/patch_rom.py`. Outside the stub table there are exactly seven:

| Address | Instruction | Role |
|---|---|---|
| `$002544` | `move.b (a2)+,$9C0A.w` | script / cutscene sound trigger |
| `$0029BA` | `move.b #$D6,$9C0A.w` | plays speech sample `$D6` |
| `$003446` | `move.b $344E(pc,d0.w),$9C0A.w` | **the sound test** |
| `$0034D0` | `move.b (a1,d0.w),$9C0A.w` | stage BGM selector (table `$00361E`) |
| `$095EEA` | `move.b #$9B,$9C0A.w` | ending / staff roll |
| `$095EF2` | `move.b #$D7,$9C0A.w` | ending speech |
| `$095FC0` | `move.b #$D9,$9C0A.w` | ending stop-all |

Three of these (`$095EEA`, `$095EF2`, `$095FC0`) are not reachable from the stub
table and would have been missed by anything less than the full scan.

### 9.3 Main loop **[C]**

```
000382: move.w  $e100.w, d0        ; $FFE100 = game mode
000386: add.w   d0, d0
000388: add.w   d0, d0
00038A: movea.l $392(pc, d0.w), a0 ; 13-entry mode jump table at $000392
00038E: jsr     (a0)
000390: bra.b   $382
```

Mode handlers at `$2826, $289C, $297E, $2B2C, $2CD6, $31F4, $2EE0, $2F58,
$2FD6, $2DBC, $3490, $28C8, $2CA2`.

**Important behavioural detail [C]:** several handlers (notably mode 4, the
attract demo) run their whole sequence internally and only return to the main
loop occasionally. Forcing `$FFE100` out of range therefore does **not** freeze
the game immediately — `scripts/make_state.lua` keeps asserting it and waits
for proof that the relocated loop is actually spinning (thousands of reads of
`$FFE100` issued from inside the loop, for 60 consecutive frames) before saving
the state. Measured: the loop is re-entered at about frame 945.

Sound driver initialisation all happens **before** the main loop, at
`$000370`–`$00037C`: `jsr $11BE` (VDP), `jsr $20352`, `jsr $22EEC` (Z80 blob
upload). Interrupts are *not* enabled at that point — `$00029E` sets
`SR = $2700` and a mode handler lowers it. A frozen-from-boot patch therefore
produces a silent machine (measured `SR = $2704`, driver never ticks); the game
must be allowed to boot normally first.

---

## 10. The built-in sound test **[C]**

The sound test is line 2 of a three-line service menu.

```
00334C..003386   draw routine: renders $FFFDE0, $FFFDE2, $FFFDE4, $FFFDE6
                 using 10-byte VDP display descriptors at $0240AC, $0240CA,
                 $0240E8 and $024106 (mulu #$A), via $023F8E with d0=$A000
00338C: lea     $fde0.w,a1 ; moveq #0,d6 ; moveq #2,d7   ; menu line 0..2
003394: btst.b  #$2,$e109.w -> jsr $12ea (inc, clamped)
0033A2: btst.b  #$3,$e109.w -> jsr $12dc (dec, clamped)
0033AE: move.w  $fde0.w,d0 ; add.w d0,d0
0033B4: move.w  $33bc(pc,d0.w),d0
0033B8: jmp     $33bc(pc,d0.w)            ; offsets $0006, $002A, $004E
```

so line 0 → `$0033C2`, line 1 → `$0033E6`, line 2 → **`$00340A`, the sound
test**:

```
00340A: lea     $fde6.w, a1        ; selected index
00340E: moveq   #$0, d6            ; clamp min = 0
003410: moveq   #$41, d7           ; clamp max = $41  -> 66 entries
003412: btst.b  #$0, $e109.w       ; "next" -> jsr $12ea ; jsr $20138 ($D9 stop)
003426: btst.b  #$1, $e109.w       ; "prev" -> jsr $12dc ; jsr $20138 ($D9 stop)
003438: move.b  $e109.w, d0
00343C: andi.b  #$70, d0           ; A / B / C
003440: beq.b   $344c
003442: move.w  $fde6.w, d0
003446: move.b  $344E(pc,d0.w), $9C0A.w    ; enqueue SOUNDTEST_TABLE[index]
00344C: rts
```

The `$11FB $0006` extension word is the brief-format index `(d0.w, PC, disp 6)`;
the extension word sits at `$003448`, so the base is `$003448 + 6 = $00344E`.

### 10.1 The table: `$00344E`, 66 bytes **[C]**

```
idx  0: 81 82 83 84 85 86 87 88 89 8A 8B 95 96 97 8C 8D
idx 16: 8E 8F 90 91 9A 9C 98 9D 99 9E 93 9F 9B 92 94 A0
idx 32: A2 A4 A8 A9 AA AB AC B0 B2 B3 B4 B5 B6 B9 BA BB
idx 48: BD BE C0 C1 C3 C4 C6 C7 CA CD CE CF D1 D2 D3 D4
idx 64: D5 D6
```

* it ends exactly at `$00348F`; `$003490` is the next instruction
  (`bclr.b #$6,$fd1f.w`), so 66 entries is exact and matches the `moveq #$41`
  clamp independently
* **indices 0–30: all 31 music ids**, each exactly once, in a deliberate
  non-sequential order
* **indices 31–59: 29 sequence SFX**
* **indices 60–65: 6 DPCM samples** (`$D1`–`$D6`)

So the menu offers **66 entries = 31 music + 35 sound effects**, not the ~77 the
available documentation suggested. The complete extraction covers all 87 ids, a
strict superset.

Ids reachable only from gameplay, never from the menu:

* SFX (19): `$A1 $A3 $A5 $A6 $A7 $AD $AE $AF $B1 $B7 $B8 $BC $BF $C2 $C5 $C8 $C9 $CB $CC`
* DAC (2): `$D0`, `$D7`

### 10.2 Validation of the mapping **[C]**

Rather than trust the static read, `scripts/validate.py` boots a ROM variant
whose frozen main loop calls the game's own dispatch at `$003442`, sets
`$FFFDE6` to each index in turn, and records which id the game enqueues. All 66
entries resolve to exactly the statically-read table, and the resulting
chip-write sequences are identical to the ones the extraction harness produces
by poking `$FF9C0A`. See `output/megadrive/validation.json`.

### 10.3 Menu labels **[H]**

The 10-byte descriptors at `$024106` point into VDP tile/nametable data, not
ASCII — the third field walks `$45AE, $45BB, $45C1, …` with a constant fourth
field `$6696`, and those are not ROM text addresses. No ASCII sound names exist
in the ROM. The menu's on-screen labels were therefore not recovered, and the
outputs are named by id.

---

## 11. Z80 DPCM / DAC player **[C]**

### 11.1 Upload

```
022F06: lea     $a00000.l, a1
022F0C: lea     $b6e86.l, a2
022F12: move.w  #$16f, d2
022F16: move.b  (a2)+, (a1)+
022F18: dbra    d2, $22f16
```

368 bytes (`$170`) from **ROM `$0B6E86`** to Z80 RAM `$0000`. Code ends at Z80
`$0142`; the rest is `$FF` padding. Called from `$00037C` during boot.

Verified live in MAME: Z80 RAM `$0000` reads back
`f3 f3 31 f4 1f af 21 f4 1f 06 0c 77 23 10 fc 3e`, byte-identical to ROM
`$0B6E86`. Full disassembly: `analysis/megadrive/disassembly/z80_dac_driver.asm`.

### 11.2 Z80 RAM variables

| Z80 | Meaning |
|---|---|
| `$1FF6` | playback rate = inner `DJNZ` count (sample entry `+$0B`) |
| `$1FF7` | saved raw command |
| `$1FF8` | `$80`, set at init |
| `$1FFA` | bank register bits A16–A23 |
| `$1FFB` | bank register bit A15 |
| `$1FFD` | semaphore set `$80` around YM accesses |
| `$1FFE` | "alternate bank in use" flag |
| **`$1FFF`** | **command byte from the 68000**; bit 7 set = pending |

The 68000 writes `$A01FFF` (= Z80 `$1FFF`) at `$0B05B0`:
`subi.b #$50,d0` maps id `$D0`→`$80` … `$D7`→`$87`. The Z80 spins on bit 7 at
`$004B`, then `sub $80` to get a 0–7 index.

### 11.3 Bank switching

`$0038` writes 9 bits to `$6000`, first the bit from `$1FFB` (`rlca`, i.e. bit 7)
then the 8 bits of `$1FFA` LSB-first. Bank base = value `<< 15`.

| `$1FFB` | `$1FFA` | 9-bit value | Bank base | Used by |
|---|---|---|---|---|
| `$80` | `$0B` | `$17` | **`$0B8000`** | Z80 cmd 0–5 (`$D0`–`$D5`) |
| `$80` | `$0D` | `$1B` | **`$0D8000`** | Z80 cmd 6–7 (`$D6`–`$D7`) |

`IY` is always `$8000`, i.e. the sample table at the base of the current bank.
For cmd ≥ 6 the driver switches bank first and then does `sub 6`, so it indexes
slot 0/1 of the other bank.

### 11.4 Sample table entry: 12 bytes

`IY + index*12` (`add a,a; add a,a; ld c,a; add a,a; add a,c`).

| Offset | Meaning |
|---|---|
| `+$00..1` | start address, little-endian, in the Z80 `$8000`–`$FFFF` window |
| `+$02..3` | length in **source bytes**, little-endian (2 samples per byte) |
| `+$05` | bit 7 = uninterruptible |
| `+$0B` | rate: inner `DJNZ` count per output sample |

### 11.5 Codec: 4-bit DPCM

Per source byte: high nibble first, then low nibble. For each nibble `n`:

```
acc = (acc + delta[n]) & 0xFF        ; acc starts at $80
write acc to YM2612 register $2A     ; (register $2B = $80 enables the DAC)
```

`delta[]` lives at Z80 `$0028`–`$0037`, i.e. inside the uploaded blob at ROM
`$0B6EAE`–`$0B6EBD`:

```
00 01 02 04 08 10 20 40 80 FF FD FC F8 F0 E0 C0
=  0  +1  +2  +4  +8 +16 +32 +64 -128  -1  -3  -4  -8 -16 -32 -64
```

Verified live: Z80 RAM `$0028` reads back exactly those 16 bytes.

The two nibble paths are separate unrolled code (`$00B5` and `$00DE`); each
waits on the YM2612 busy flag (`BIT 7,(HL)` with `HL' = $4000`) before writing.
Register `HL' = $4000` is kept in the alternate register set across `EXX`, which
is what makes the loop read as writing to `$1FFF` on a naive disassembly.

### 11.6 Interruption

`BIT 7,(IY+5)` — if set, the sample ignores new commands; otherwise
`BIT 7,(HL)` on `$1FFF` aborts playback and jumps back to `$004E` when the
68000 posts a new command.

---

## 12. DAC sample inventory **[C]**

### Bank `$0B8000` (table `$0B8000`–`$0B8047`)

| Id | Z80 cmd | Z80 start | ROM start | Bytes | Samples | Rate byte | Measured Hz | Duration | Uninterruptible |
|---|---|---|---|---|---|---|---|---|---|
| `$D0` | 0 | `$8048` | `$0B8048` | 1224 | 2448 | `$0B` | 10 513 | 0.233 s | no |
| `$D1` | 1 | `$8510` | `$0B8510` | 5116 | 10 232 | `$07` | 12 408 | 0.825 s | no |
| `$D2` | 2 | `$990C` | `$0B990C` | 4500 | 9000 | `$0B` | 10 513 | 0.856 s | no |
| `$D3` | 3 | `$AAA0` | `$0BAAA0` | 4736 | 9472 | `$0B` | 10 513 | 0.901 s | no |
| `$D4` | 4 | `$BD20` | `$0BBD20` | 3240 | 6480 | `$0B` | 10 513 | 0.616 s | no |
| `$D5` | 5 | `$C9C8` | `$0BC9C8` | 3816 | 7632 | `$0B` | 10 513 | 0.726 s | no |

### Bank `$0D8000` (table `$0D8000`–`$0D8017`)

| Id | Z80 cmd | Z80 start | ROM start | Bytes | Samples | Rate byte | Measured Hz | Duration | Uninterruptible |
|---|---|---|---|---|---|---|---|---|---|
| `$D6` | 6 | `$8018` | `$0D8018` | 20 344 | 40 688 | `$11` | 8 731 | 4.660 s | **yes** |
| `$D7` | 7 | `$CF90` | `$0DCF90` | 12 396 | 24 792 | `$25` | 5 343 | 4.640 s | **yes** |

### Corroboration **[C]**

Both tables chain with no gaps or overlaps, and in each bank the first sample
starts immediately after the last table entry:

```
bank $B8000: $8048+1224=$8510 ✓ +5116=$990C ✓ +4500=$AAA0 ✓
             +4736=$BD20 ✓ +3240=$C9C8 ✓ +3816=$D8B0 (end)
             table 6*12=$48 -> data starts $8048 ✓
bank $D8000: $8018+20344=$CF90 ✓ +12396=$FFFC (exactly fills the 32 KiB window) ✓
             table 2*12=$18 -> data starts $8018 ✓
```

Stronger still: each capture produced **exactly** `2 × length` writes to YM
register `$2A` — e.g. `$D1` produced 10 232 and `$D6` produced 40 688, matching
the table to the sample. The DPCM interpretation is therefore not merely
plausible, it is exact.

`$D6` and `$D7` are long, slow, uninterruptible and have a low zero-crossing
rate (~3 kHz) — almost certainly speech. **[H]**

### 12.1 Which samples are voice clips **[C]** / whose voice **[H]**

Call-site analysis of the `$020000` stub table (absolute `jsr`/`jmp` targets)
groups the samples by the game module that plays them:

| Id | Call sites | Module |
|---|---|---|
| `$D0` | none | never played by the game; sound-test-unreachable too |
| `$D1` | `$039F82` | with `$D6` at `$039C78`/`$03A166` |
| `$D2` | 9 sites, `$0160D6`–`$0167FA` | one module |
| **`$D3`** | `$01DD48`, `$01DEF2`, `$01E472` | **same module as `$D4`/`$D5`** |
| **`$D4`** | `$01DF7C`, `$01DFBA`, `$01E3BA` | **same module** |
| **`$D5`** | `$01DB04`, `$01DBDE` | **same module** |
| `$D6` | `$00DE6E`, `$026A5E`, `$039C78`, `$03A166` | scattered (title / intro?) |
| `$D7` | none | never played by the game (ending code at `$095EF2` writes the queue directly) |

`$D3`, `$D4` and `$D5` are played by **one tight code region**
(`$01DB04`–`$01E472`, about 2.4 KiB) which plays **no other sound at all** — no
FM SFX, no music. That makes it a dedicated voice controller for a single
character, with three alternating clips. They are also adjacent in the sound
test (indices 62, 63, 64).

A listener reports `$D3` and `$D4` as short spoken phrases in a non-English
language, attributed to the **Amazoness** (stage 4) enemies. **[H]** — the
character attribution is not established from the ROM; what the ROM shows is
only that the three clips form one voice set. If the attribution is right, `$D5`
belongs to the same set.

**There is nothing to translate.** Per an interview with Strider's director
Kouichi Yotsui, the Amazoness deliberately speak gibberish: they were originally
meant to speak Swahili and Capcom scrapped it, objecting that depicting the
characters as primitive looked like discrimination. (Gameside #16, Feb 2009,
tr. Gaijin Punch for Gamengai; cited on the Strider Wiki "Amazoness" page.)
So the clips are intentionally not any real language.

Measured acoustic detail (windowed RMS envelope, 20 ms window / 5 ms hop, on
`analysis/megadrive/speech/*_16k.wav`):

| Id | Duration | Energy peaks (possible syllable nuclei) |
|---|---|---|
| `$D3` | 0.80 s | 5, at 0.04 0.165 0.28 0.57 0.695 s (clear gap ~0.40–0.55 s) |
| `$D4` | 0.55 s | 3, at 0.08 0.17 0.285 s, front-loaded then decaying |
| `$D6` | 4.68 s | 35 |
| `$D7` | 4.43 s | 35 |

The `$D6`/`$D7` peak counts are consistent with full sentences; `$D3`/`$D4` with
brief exclamations. (An earlier spectral-band breakdown was discarded: the
decimated DFT that produced it was aliasing, which is why every clip reported
exactly 50 % in the top band.)

### Playback rate **[C]**

Rates above are **measured** from the real inter-write spacing of YM register
`$2A` in the emulated machine, averaged over each whole sample. Within a run
they reproduce to +-0.5 Hz, and they do not depend on what the 68000 is doing: a
controlled A/B with the 68000 parked in its spin loop versus running the attract
demo gave an identical 10512.8 Hz for `$D2`.

The measurements are fully explained by the Z80 code, with one constant per code
path and no other free parameters:

```
period_per_sample = fixed + rate_byte x DJNZ
DJNZ  = 13 T-states @ 3.579545 MHz = 3.6317 us
fixed = 55.17 us   interruptible samples   ($D0-$D5)
fixed = 52.80 us   uninterruptible samples ($D6, $D7)
```

That reproduces all eight measured rates to within **0.004 %**:

| Id | Rate byte | Predicted Hz | Measured Hz | Error |
|---|---|---|---|---|
| `$D0` | `$0B` | 10512.9 | 10513.3 | -0.004 % |
| `$D1` | `$07` | 12407.8 | 12407.7 | +0.001 % |
| `$D2`-`$D5` | `$0B` | 10512.9 | 10512.8 | +0.001 % |
| `$D6` | `$11` | 8730.6 | 8730.6 | -0.000 % |
| `$D7` | `$25` | 5342.6 | 5342.6 | +0.000 % |

The two `fixed` constants differ by **2.372 us**, and that is not a fudge
factor. The uninterruptible path skips the "has the 68000 posted a new
command?" test, which is `BIT 7,(HL)` + `JP nz,nn` = 22 T-states, replaced by a
taken `JR` at 5 T-states. That test runs once per **source byte**, i.e. once per
two samples, so the saving per sample is `(22 - 5) / 2 = 8.5` T-states =
**2.375 us**. Predicted 2.375, observed 2.372.

Correction worth recording: an earlier version of these notes gave 11 869 Hz for
the `$0B` samples and called it "measured". It was not - it was
`scripts/extract_pcm.py`'s cycle-estimate fallback, which fired because the
script had been run before the capture logs for those ids existed. The fallback
now uses the model above, and the script reports `rate_source` per sample so an
estimate can never again be mistaken for a measurement.

## 13. ROM map of the sound area **[C]**

| Range | Contents |
|---|---|
| `$000382`–`$00039F` | main loop + 13-entry mode jump table |
| `$00340A`–`$00348F` | sound test handler + index→id table |
| `$020000`–`$020321` | 89 "play sound N" stubs |
| `$020322`–`$02034B` | queue-slot allocator |
| `$022EEC`–`$022F36` | Z80 reset + DAC blob upload |
| `$0B0000`–`$0B001F` | sound bank descriptor |
| `$0B0020`–`$0B009B` | song pointer table (31) |
| `$0B009C`–`$0B00F7` | priority table (92 bytes, index `id-$81`) |
| `$0B00F8`–`$0B10E9` | **68000 sound driver code** (~4 KiB) |
| `$0B10EA`–`$0B638B` | song sequence + FM voice data |
| `$0B638C`–`$0B644B` | SFX pointer table (48) |
| `$0B644C`–`$0B6E85` | SFX sequence + FM voice data |
| `$0B6E86`–`$0B6FF5` | Z80 DAC driver blob (`$170` used) |
| `$0B6FF6`–`$0B7FFF` | `$FF` padding (4106 bytes — used by the freeze patch) |
| `$0B8000`–`$0B8047` | DAC sample table, bank `$17` |
| `$0B8048`–`$0BD8AF` | DPCM data (6 samples) |
| `$0D8000`–`$0D8017` | DAC sample table, bank `$1B` |
| `$0D8018`–`$0DFFFB` | DPCM data (2 samples) |

---

## 14. Extraction method

### 14.1 Why the ROM is patched

Capturing a sound in isolation requires the game not to play anything of its
own. Three approaches were tried:

1. **Freeze from boot** (point every mode-table entry at an `rts`). *Failed*:
   interrupts are still masked at that point (measured `SR = $2704`), so the
   VBlank driver tick never runs and nothing is audible at all.
2. **Mute only** (neutralise every game-side queue write). Works — measured 0
   non-zero queue writes over 4000 frames — but the mode handlers still emit
   ~66 "silence all channels" YM writes on each attract-mode transition, which
   could truncate a capture.
3. **Mute + freeze after boot** (what is used). The game boots normally so that
   VDP, interrupts and the Z80 blob are all initialised, then the relocated main
   loop is parked. Measured: **0 YM and 0 PSG writes over 30 s of idle**.

### 14.2 Patches applied by `scripts/patch_rom.py --mode freeze`

`build/strider_capture.md`, 54 patches, none touching sound data:

1. 42 direct stubs: `move.b #id,$9C0A.w` → three `nop`s.
2. Queue-slot allocator entries `$020322` and `$020324` → `rts` (the second is
   the `bra.w` target of the 46 queued stubs).
3. The 7 non-stub queue writers of §9.2 → `nop`s. `$002544` becomes
   `move.b (a2)+,d0` + `nop` so its caller's `(a2)+` side effect survives.
4. Main loop `$000382` → `jmp $0B7800.l`; a 26-byte relocated loop assembled
   into the `$FF` padding at `$0B7800` bounds-checks the mode index, so a mode
   `>= 13` spins forever while VBlank keeps ticking the driver.
5. Header checksum recomputed.

`--mode soundtest` is the same but leaves `$003446` intact and makes the frozen
branch poll `$FFFDE8` and call `$003442`, so captures can be driven through the
game's own dispatch. `--mode mute` omits the freeze.

### 14.3 Pipeline

```
patched ROM + frozen save state
  -> MAME (headless, genesis/NTSC) pokes the id into $FF9C0A
  -> write taps on the YM2612 and SN76489 ports of BOTH CPUs
       -> chip-write log -> output/vgm/*.vgm + per-sound channel metadata
  -> MAME -wavwrite 48 kHz/16-bit stereo
       -> DC removal + conservative trim -> output/megadrive/wav/*.wav
```

The capture ends automatically when the chips have been quiet for 90 frames
(1.5 s) for SFX/DAC or 600 frames for music, capped at 900 / 3600 frames.

### 14.4 Emulator specifics

The MAME write-tap semantics, subscription lifetime, `-seconds_to_run` and
display settings that this pipeline depends on are documented once in
[method.md §3](method.md#3-mame-specifics-worth-writing-down).

### 14.5 DC offset **[C]**

With YM2612 `$2B` (DAC enable) set and a value latched in `$2A`, MAME's idle
output sits at a constant `+252` on both channels rather than at zero (measured:
the idle baseline takes only the values 251 and 252). `scripts/wavutil.py`
subtracts the offset measured from the guaranteed-silent lead-in — see
[method.md §8](method.md#8-wav-post-processing) for the full output policy.

## 15. Results

| Metric | Value |
|---|---|
| Sounds rendered | **87** (31 music + 48 sequence SFX + 8 DPCM) |
| Sound-test entries covered | 66 / 66 |
| Silent outputs | 0 |
| SFX hitting the capture window | 0 |
| Stray game-initiated queue writes during capture | 0 |
| Identical-output groups (aliases/duplicates) | 0 |
| **PSG (SN76489) writes across all 87 sounds** | **0** |
| SFX duration range | 0.258 s – 4.98 s |
| FM channels used by SFX | a single channel each: 3, 4 or 5 |
| DPCM sample audio | 8 samples, 13.46 s total |
| FM channels used by music | all six |

---

## 16. Open questions / not established

* Driver command `$D8` (`$0B0A12`) — exact effect. **[H]**
* The full coordination-flag table dispatched from `$0B0C8A`. **[H]**
* Why the SFX track-pointer tables alias channel bytes `$02`/`$03` and
  `$08`/`$09`. **[H]**
* The purpose of the 7th (extra `$06`) song track — probably a master/tempo
  track since all 31 songs have it. **[H]**
* The on-screen sound-test labels (VDP tile data, §10.3). **[H]**
* Which character each voice clip belongs to. Call-site grouping is solid
  (§12.1) but the ROM does not name the speakers, and the extraction harness
  cannot listen. `$D0` and `$D7` have no call sites at all in the stub table.
* The 24 looping BGM tracks are captured as 60 s excerpts from the start rather
  than one exact loop; the SMPS loop coordination flag was not decoded, so loop
  points are not marked in the output.

---

## 17. Tools

| Tool | Use |
|---|---|
| MAME 0.289 (`genesis`, USA/NTSC), headless | emulation, chip-write taps, WAV capture |
| `capstone` 5.0.7 (M68K) | `scripts/dis68k.py` (linear-with-resync and recursive-descent) |
| `z80dis` 1.0.6 | `scripts/disz80.py` |
| Python 3.10 venv at `.venv` | everything else |

Reproduction instructions are in [../README.md](../README.md).
