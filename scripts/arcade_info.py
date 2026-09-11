#!/usr/bin/env python3
"""Constants and table parsers for Strider CPS-1 (MAME set `strider`).

Everything here is derived from the Z80 sound program `09.12b` and the OKIM6295
sample ROMs; see NOTES-ARCADE.md.

Hardware (from `mame -listxml strider`):
    68000     10.000000 MHz   main
    Z80        3.579545 MHz   audiocpu, program = 09.12b (64 KiB)
    YM2151     3.579545 MHz
    OKIM6295   1.000000 MHz   samples = 18.11c @ $00000 + 19.12c @ $20000
    output: MONO
"""
import os, struct

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAME_SET = "strider"          # Strider (USA, B-Board 89624B-2)
ROMDIR = os.path.join(ROOT, "roms", MAME_SET)   # MAME needs the set name

# ---- ROM files ----------------------------------------------------------
Z80_ROM  = "09.12b"           # 64 KiB sound program
OKI_ROMS = ["18.11c", "19.12c"]   # concatenated -> 256 KiB `oki` region

# CRC32s from MAME's own `strider` manifest. Other Strider sets (striderua,
# striderj, striderjr, strideruc) have DIFFERENT sound ROMs, so every address in
# docs/arcade.md would be wrong for them - hence the check.
ROM_CRC32 = {
    "09.12b": 0x2ED403BC,
    "18.11c": 0x4386BC80,
    "19.12c": 0x444536D7,
}

# ---- Z80 memory map (CPS-1 standard, bank mapping verified live) --------
# $0000-$7FFF  ROM, direct  -> ROM $0000-$7FFF
# $8000-$BFFF  ROM, banked  -> bank 0 = ROM $8000 (all $FF here), bank 1 = ROM $C000
# $D000-$D7FF  RAM
# $F000/$F001  YM2151 address / data
# $F002        OKIM6295
# $F004        ROM bank select
# $F006        OKI pin 7 / sample bank
# $F008        soundlatch  (sound code from the 68000)
# $F00A        soundlatch2 (fade)
Z80_BANK_ROM   = {0: 0x8000, 1: 0xC000}
Z80_BANK_BASE  = 0x8000
YM_ADDR, YM_DATA = 0xF000, 0xF001
OKI_PORT       = 0xF002
BANK_PORT      = 0xF004
LATCH_PORT     = 0xF008

# ---- 68000 side ---------------------------------------------------------
SND_CMD_ADDR   = 0x800180     # byte: sound code
SND_FADE_ADDR  = 0x800188     # byte: fade
IDLE_CMD       = 0xFF         # what the game writes when it wants nothing

# ---- sound command dispatch ($0137) ------------------------------------
# $00-$7F -> FM sequence, via a two-part table searched in a loop
# $80-$EF -> OKIM6295 sample, index taken modulo the table length
# $F0-$FF -> driver command jump table at $02D6
FM_TBL1_COUNT  = 0x0DCD       # byte; entries = value + 1
FM_TBL1        = 0x0DD0       # little-endian words, Z80 addresses
FM_TBL2_COUNT  = 0xC000       # in ROM (Z80 $8000 with bank 1)
FM_TBL2        = 0xC004
OKI_TBL_COUNT  = 0x0DAF       # byte; entries = value + 1
OKI_TBL        = 0x0DB0       # one byte per code: high nibble -> OKI channel

CMD_JUMP_TBL   = 0x02D6       # 16 words, for $F0..$FF
CMD_STOP_ALL   = 0xF0         # -> $02F6, full stop/reset
CMD_NOP        = 0xFA         # $FA..$FF -> $0394 = RET

# YM2151 registers the driver writes continuously as timer housekeeping even
# when nothing is playing. Measured over 900 idle frames in test mode: these
# four and nothing else.
YM_TIMER_REGS  = {0x10, 0x11, 0x12, 0x14}

# ---- OKIM6295 -----------------------------------------------------------
OKI_PHRASE_ENTRIES = 128      # 8 bytes each, at the start of the sample ROM
OKI_PHRASE_SIZE    = 8
OKI_CLOCK          = 1000000
# pin 7 high -> /132, pin 7 low -> /165
OKI_RATE_PIN7_HIGH = OKI_CLOCK / 132.0    # 7575.76 Hz
OKI_RATE_PIN7_LOW  = OKI_CLOCK / 165.0    # 6060.61 Hz
YM2151_CLOCK       = 3579545

# ---- dipswitch used to reach the idle test screen ----------------------
DIP_GAME_MODE      = ("Game Mode", 0)     # field name, value for "Test"


def load(name):
    return open(os.path.join(ROMDIR, name), "rb").read()


def z80_rom():
    return load(Z80_ROM)


def oki_rom():
    return b"".join(load(n) for n in OKI_ROMS)


def z80_read(d, addr, bank=1):
    """Read through the Z80 memory map into the sound ROM image."""
    if addr < 0x8000:
        return d[addr]
    if addr < 0xC000:
        return d[Z80_BANK_ROM[bank] + (addr - Z80_BANK_BASE)]
    raise ValueError(f"Z80 ${addr:04X} is not ROM")


def fm_sequence_table(d):
    """-> list of (sound_code, z80_addr, bank) for every FM sequence code.

    $0137 searches two tables in a loop, subtracting each table's length until
    the index falls inside one, so the code space wraps: with 48 + 72 entries
    the 120 distinct codes are $00-$77 and $78-$7F alias back to $00-$07.
    """
    n1 = d[FM_TBL1_COUNT] + 1
    n2 = d[FM_TBL2_COUNT] + 1
    out = []
    for i in range(n1):
        out.append((i, struct.unpack_from("<H", d, FM_TBL1 + 2 * i)[0], None))
    for i in range(n2):
        out.append((n1 + i, struct.unpack_from("<H", d, FM_TBL2 + 2 * i)[0], 1))
    return out, n1, n2


def oki_code_table(d):
    """-> list of (sound_code, table_byte, oki_channel_stop_value)."""
    n = d[OKI_TBL_COUNT] + 1
    out = []
    for i in range(n):
        b = d[OKI_TBL + i]
        out.append((0x80 + i, b, (b & 0xF0) >> 1))
    return out, n


def oki_phrases(rom):
    """Parse the OKIM6295 phrase table: 128 x 8 bytes at the ROM start.

    entry: [0..2] start address (24-bit big-endian), [3..5] end address,
           [6..7] unused
    """
    out = []
    for i in range(OKI_PHRASE_ENTRIES):
        e = i * OKI_PHRASE_SIZE
        start = (rom[e] << 16) | (rom[e + 1] << 8) | rom[e + 2]
        end = (rom[e + 3] << 16) | (rom[e + 4] << 8) | rom[e + 5]
        out.append(dict(index=i, start=start, end=end,
                        length=(end - start + 1) if end >= start else 0,
                        empty=(start == 0 and end == 0)))
    return out


# ---- OKI ADPCM (Dialogic/OKI 4-bit) decoder ----------------------------
_INDEX_SHIFT = [-1, -1, -1, -1, 2, 4, 6, 8]
_NBL2BIT = [
    (1, 0, 0, 0), (1, 0, 0, 1), (1, 0, 1, 0), (1, 0, 1, 1),
    (1, 1, 0, 0), (1, 1, 0, 1), (1, 1, 1, 0), (1, 1, 1, 1),
    (-1, 0, 0, 0), (-1, 0, 0, 1), (-1, 0, 1, 0), (-1, 0, 1, 1),
    (-1, 1, 0, 0), (-1, 1, 0, 1), (-1, 1, 1, 0), (-1, 1, 1, 1),
]


def _build_diff_lookup():
    """Same construction as MAME's oki_adpcm_state::init()."""
    tbl = []
    for step in range(49):
        stepval = int(16.0 * pow(11.0 / 10.0, step))
        for nib in range(16):
            s, b2, b1, b0 = _NBL2BIT[nib]
            tbl.append(s * (stepval * b2 + stepval // 2 * b1
                            + stepval // 4 * b0 + stepval // 8))
    return tbl


_DIFF_LOOKUP = _build_diff_lookup()


def decode_oki_adpcm(data):
    """4-bit OKI ADPCM -> list of 12-bit signed samples (high nibble first)."""
    signal, step = -2, 0
    out = []
    for b in data:
        for nib in (b >> 4, b & 0x0F):
            signal += _DIFF_LOOKUP[step * 16 + nib]
            signal = -2048 if signal < -2048 else (2047 if signal > 2047 else signal)
            step += _INDEX_SHIFT[nib & 7]
            step = 0 if step < 0 else (48 if step > 48 else step)
            out.append(signal)
    return out


def identity_problems():
    """CRC32 mismatches against MAME's `strider` set. Advisory, not fatal."""
    import zlib
    bad = []
    for name, want in ROM_CRC32.items():
        got = zlib.crc32(load(name)) & 0xFFFFFFFF
        if got != want:
            bad.append((name, want, got))
    return bad


def structure_problems():
    """Do the baked-in addresses still land on structures of the right shape?

    Deliberately shape-based rather than content-based, so a revision that
    happens to lay its sound data out the same way still works.
    """
    d = z80_rom()
    rom = oki_rom()
    bad = []

    # Z80 reset: DI then IM 1, and a stack pointer into the RAM window.
    if d[0] != 0xF3 or d[1:3] != b"\xed\x56":
        bad.append("Z80 $0000 is not `DI / IM 1` - not a CPS-1 sound program?")
    if d[3] != 0x31 or not (0xD000 <= struct.unpack_from("<H", d, 4)[0] <= 0xD800):
        bad.append("Z80 $0003 does not load a stack pointer into $D000-$D800")

    # Sound command handler must begin by reading the last-command byte.
    if d[0x0137] != 0x3A:
        bad.append(f"$0137 is ${d[0x0137]:02X}, not `LD A,(nn)` - the sound "
                   f"command handler is not where expected")

    # The two FM sequence pointer tables: sane counts, and every pointer inside
    # the region that table is read from.
    try:
        fm, n1, n2 = fm_sequence_table(d)
    except Exception as e:
        return bad + [f"cannot parse the FM sequence tables: {e}"]
    if not 1 <= n1 <= 128:
        bad.append(f"fixed FM table count is {n1}, expected 1..128")
    if not 1 <= n2 <= 128:
        bad.append(f"bank-1 FM table count is {n2}, expected 1..128")
    for code, addr, bank in fm:
        lo, hi = (0x8000, 0xC000) if bank == 1 else (0x1000, 0x8000)
        if not lo <= addr < hi:
            bad.append(f"FM sequence for code ${code:02X} points to ${addr:04X}, "
                       f"outside ${lo:04X}-${hi:04X}")
            break

    # OKI code table: each play byte must select exactly one of the 4 voices.
    ok, n = oki_code_table(d)
    if not 1 <= n <= 128:
        bad.append(f"OKI code table count is {n}, expected 1..128")
    for code, b, _stop in ok:
        mask = b & 0xF0
        if mask == 0 or mask & (mask - 1):
            bad.append(f"OKI code ${code:02X} play byte ${b:02X} does not select "
                       f"exactly one voice")
            break

    # OKI phrase table: populated entries must be monotonic and contiguous,
    # start past the table area, and end inside the ROM.
    ph = [p for p in oki_phrases(rom)
          if not p["empty"] and p["start"] != 0xFFFFFF and p["end"] >= p["start"]]
    if not ph:
        bad.append("OKI phrase table has no populated entries")
    else:
        tbl_bytes = OKI_PHRASE_ENTRIES * OKI_PHRASE_SIZE
        if ph[0]["start"] < tbl_bytes:
            bad.append(f"first OKI sample starts ${ph[0]['start']:06X}, inside the "
                       f"${tbl_bytes:04X}-byte phrase table")
        if ph[-1]["end"] >= len(rom):
            bad.append(f"last OKI sample ends ${ph[-1]['end']:06X}, past the "
                       f"{len(rom)}-byte sample ROM")
        for a, b in zip(ph, ph[1:]):
            if b["start"] != a["end"] + 1:
                bad.append(f"OKI phrases not contiguous: {a['index']} ends "
                           f"${a['end']:06X}, {b['index']} starts ${b['start']:06X}")
                break
    return bad


def verify(warn=None):
    """Identity is advisory, structure is decisive.

    A CRC32 mismatch only warns: if the layout still checks out, the pipeline can
    proceed and tell you it is working on something other than the documented
    set. A structural failure raises, because then the addresses really are wrong.
    """
    import sys
    if warn is None:
        def warn(msg):
            print(msg, file=sys.stderr)

    ident = identity_problems()
    if ident:
        warn("WARNING: arcade sound ROMs are not MAME's `strider` set:\n"
             + "\n".join(f"  {n}: expected CRC32 {w:08x}, got {g:08x}"
                          for n, w, g in ident)
             + "\n  Proceeding because the structural checks pass, but the"
               " addresses in\n  docs/arcade.md may not describe this revision."
               " See roms/README.md.")
    struct = structure_problems()
    if struct:
        raise SystemExit(
            "arcade ROM structure does not match the documented layout:\n"
            + "\n".join("  " + p for p in struct)
            + "\nThe baked-in addresses do not fit this ROM. See roms/README.md.")
    return ident, struct


# Kept for callers that want the old name.
def verify_roms(strict=True):
    return verify()[0]
