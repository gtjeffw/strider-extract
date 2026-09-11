#!/usr/bin/env python3
"""Shared constants and table parsing for the Strider sound system.

All addresses are both ROM file offsets and 68000 addresses (cart @ $000000).
Everything here is derived from the disassembly documented in NOTES.md.
"""
import struct

ROM_NAME = "roms/Strider (USA, Europe).md"
ROM_SHA1 = "26fe42d13a01c8789bbad722ebac05b8a829eb37"

# ---- sound bank ---------------------------------------------------------
BANK_BASE      = 0x0B0000   # hardcoded in the driver (movea.l #$B0000,a0)
HDR_PRIO_TBL   = 0x00       # +$00 -> priority table
HDR_SONG_TBL   = 0x08       # +$08 -> song pointer table
HDR_SFX_TBL    = 0x0C       # +$0C -> sfx pointer table
HDR_SFX_BASE   = 0x18       # +$18 -> first sfx id ($A0)
HDR_DRIVER     = 0x1C       # +$1C -> driver entry ($B00F8)

DRIVER_ENTRY   = 0x0B00F8

SONG_ID_FIRST  = 0x81
SONG_COUNT     = 31         # $81..$9F
SFX_ID_FIRST   = 0xA0
SFX_COUNT      = 48         # $A0..$CF
DAC_ID_FIRST   = 0xD0
DAC_COUNT      = 8          # $D0..$D7
CMD_ID_FIRST   = 0xD8       # $D8..$DB driver commands
CMD_STOP_ALL   = 0xD9       # full driver reset / silence  ($B08CE)
CMD_STOP_TRACKS= 0xDA       # clear track array only       ($B0904)

# ---- sound driver work RAM ---------------------------------------------
RAM_PRIO       = 0xFF9C00
RAM_CUR_ID     = 0xFF9C09
RAM_QUEUE      = 0xFF9C0A   # 3 bytes: $FF9C0A/0B/0C
RAM_TRACKS     = 0xFF9C40   # $30 bytes per track

# ---- game-side plumbing -------------------------------------------------
STUB_TABLE     = 0x020000   # "play sound N" stubs
SFX_PENDING    = 0xFFE188   # game-side pending SFX id
QUEUE_ALLOC    = 0x020322   # queue-slot allocator (entry points $20322/$20324)
MAIN_LOOP      = 0x000382   # mode dispatch loop
MODE_TABLE     = 0x000392   # 13 mode handlers
GAME_MODE      = 0xFFE100

# ---- Z80 DAC player -----------------------------------------------------
Z80_BLOB_ROM   = 0x0B6E86
Z80_BLOB_LEN   = 0x170
Z80_CMD_PORT   = 0xA01FFF   # 68k view of Z80 RAM $1FFF
DPCM_DELTA_ROM = 0x0B6EAE   # 16-byte delta table inside the blob
DAC_BANKS      = {          # z80 cmd range -> (rom bank base, entry count)
    "A": (0x0B8000, 6),     # cmds 0..5  -> ids $D0..$D5
    "B": (0x0D8000, 2),     # cmds 6..7  -> ids $D6..$D7
}
DAC_ENTRY_SIZE = 12
# Z80 window $8000..$FFFF maps to bank_base..bank_base+0x7FFF
def z80_to_rom(bank_base, z80_addr):
    return bank_base + (z80_addr - 0x8000)

# The YM2612 DAC is fed one byte per DPCM nibble; each output sample costs a
# fixed instruction sequence plus `rate` DJNZ iterations on a 3.579545 MHz Z80.
Z80_CLOCK = 3579545.0

def load(path=ROM_NAME):
    return open(path, "rb").read()

def be32(d, o): return struct.unpack_from(">I", d, o)[0]
def be16(d, o): return struct.unpack_from(">H", d, o)[0]
def le16(d, o): return struct.unpack_from("<H", d, o)[0]

def bank_header(d):
    return {k: be32(d, BANK_BASE + k) for k in (0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C)}

def song_table(d):
    base = be32(d, BANK_BASE + HDR_SONG_TBL)
    return [be32(d, base + 4 * i) for i in range(SONG_COUNT)]

def sfx_table(d):
    base = be32(d, BANK_BASE + HDR_SFX_TBL)
    return [be32(d, base + 4 * i) for i in range(SFX_COUNT)]

def priority(d, sid):
    base = be32(d, BANK_BASE + HDR_PRIO_TBL)
    return d[base + (sid - SONG_ID_FIRST)]

def dpcm_deltas(d):
    return [d[DPCM_DELTA_ROM + i] for i in range(16)]

def dac_entries(d):
    """Return list of dicts for all 8 DAC samples, in sound-id order."""
    out = []
    for key in ("A", "B"):
        base, n = DAC_BANKS[key]
        for i in range(n):
            e = base + i * DAC_ENTRY_SIZE
            start_z80 = le16(d, e)
            nbytes    = le16(d, e + 2)
            out.append(dict(
                bank=key, bank_base=base, entry_index=i,
                z80_cmd=len(out), sound_id=DAC_ID_FIRST + len(out),
                z80_start=start_z80, rom_start=z80_to_rom(base, start_z80),
                length=nbytes, samples=nbytes * 2,
                flag5=d[e + 5], uninterruptible=bool(d[e + 5] & 0x80),
                rate=d[e + 11],
                raw=d[e:e + DAC_ENTRY_SIZE].hex(),
            ))
    return out

def parse_stubs(d):
    """Parse the heterogeneous 'play sound N' stub table at $020000."""
    o = STUB_TABLE
    out = []
    while True:
        if d[o:o+2] == b"\x11\xfc" and d[o+4:o+6] == b"\x9c\x0a" and d[o+6:o+8] == b"\x4e\x75":
            out.append(dict(addr=o, sid=d[o+3], kind="direct", size=8))
            o += 8
        elif d[o:o+2] == b"\x11\xfc" and d[o+4:o+6] == b"\xe1\x88" and d[o+6:o+8] == b"\x60\x00":
            out.append(dict(addr=o, sid=d[o+3], kind="queued", size=10))
            o += 10
        elif d[o:o+2] == b"\x11\xfc" and d[o+4:o+6] == b"\xe1\x88":
            out.append(dict(addr=o, sid=d[o+3], kind="queued-fallthrough", size=6))
            o += 6
            break
        else:
            break
    return out, o

def classify(sid):
    if SONG_ID_FIRST <= sid < SONG_ID_FIRST + SONG_COUNT: return "music"
    if SFX_ID_FIRST  <= sid < SFX_ID_FIRST  + SFX_COUNT:  return "sfx"
    if DAC_ID_FIRST  <= sid < DAC_ID_FIRST  + DAC_COUNT:  return "dac"
    if sid >= CMD_ID_FIRST: return "command"
    return "unknown"

def all_sound_ids():
    return ([SONG_ID_FIRST + i for i in range(SONG_COUNT)]
            + [SFX_ID_FIRST + i for i in range(SFX_COUNT)]
            + [DAC_ID_FIRST + i for i in range(DAC_COUNT)])


# ---- built-in sound test ($00340A) --------------------------------------
# 00340A: lea    $fde6.w,a1        ; a1 -> selected index
# 00340E: moveq  #$0,d6            ; clamp min
# 003410: moveq  #$41,d7           ; clamp max  -> 66 entries
# 003412: btst.b #$0,$e109.w       ; "next"  -> jsr $12ea, then jsr $20138 ($D9 stop all)
# 003426: btst.b #$1,$e109.w       ; "prev"  -> jsr $12dc, then jsr $20138
# 003438: move.b $e109.w,d0 ; andi.b #$70,d0 ; beq -> buttons A/B/C = play
# 003442: move.w $fde6.w,d0
# 003446: move.b $344E(pc,d0.w),$9C0A.w   ; enqueue SOUNDTEST_TABLE[index]
SOUNDTEST_CODE   = 0x00340A
SOUNDTEST_TABLE  = 0x00344E
SOUNDTEST_COUNT  = 0x42          # moveq #$41,d7 -> indices 0..$41 inclusive
SOUNDTEST_INDEX  = 0xFFFDE6      # selected index (word)
SOUNDTEST_INPUT  = 0xFFE109      # bit0 = next, bit1 = prev, $70 = A/B/C = play

def soundtest_table(d):
    """index -> sound id, exactly as the menu dispatches it."""
    return list(d[SOUNDTEST_TABLE:SOUNDTEST_TABLE + SOUNDTEST_COUNT])

# Every `move.b <ea>,$FF9C0A/0B/0C.w` outside the $020000 stub table, i.e. the
# places the *game* (not the harness) can enqueue a sound. Found by an
# exhaustive opcode scan; see scripts/patch_rom.py.
EXTRA_QUEUE_WRITERS = [
    (0x02544, 4, "move.b (a2)+,$9C0A.w   - script/cutscene sound trigger"),
    (0x029BA, 6, "move.b #$D6,$9C0A.w    - plays speech sample $D6"),
    (0x03446, 6, "move.b $344E(pc,d0.w),$9C0A.w - THE SOUND TEST dispatch"),
    (0x034D0, 6, "move.b (a1,d0.w),$9C0A.w - stage BGM selector (table $0361E)"),
    (0x95EEA, 6, "move.b #$9B,$9C0A.w    - ending/staff roll"),
    (0x95EF2, 6, "move.b #$D7,$9C0A.w    - ending speech sample"),
    (0x95FC0, 6, "move.b #$D9,$9C0A.w    - ending stop-all"),
]


# ---- freeze patch (scripts/patch_rom.py --mode freeze) ------------------
# The main loop at $000382 is replaced by `jmp FREEZE_LOOP`, and a relocated
# copy of the loop is assembled into the $FF padding at $0B7800. The copy
# bounds-checks the mode index, so setting GAME_MODE >= 13 makes the 68000
# spin forever without ever calling a mode handler -- while VBlank interrupts
# (and therefore the sound driver tick) keep running normally.
FREEZE_LOOP   = 0x0B7800
FREEZE_PAD    = (0x0B6FF6, 0x0B8000)   # all $FF in the original ROM
FREEZE_VALUE  = 0x00FF                 # written to GAME_MODE to idle the game

# `--mode soundtest` keeps the game's own sound-test dispatch at $003446 intact
# and makes the frozen loop call the real sound-test handler at $00340A every
# frame, so a capture can be driven through the game's own code path
# (SOUNDTEST_INDEX + SOUNDTEST_INPUT) instead of by poking RAM_QUEUE directly.
# $003442 is the sound-test PLAY path on its own (no menu navigation):
#   003442: move.w $fde6.w,d0
#   003446: move.b $344E(pc,d0.w),$9C0A.w
#   00344C: rts
SOUNDTEST_PLAY   = 0x003442
# One-shot trigger flag for the soundtest ROM variant. $FFFDE8 sits just past
# the service-menu variables ($FFFDE0/E2/E4/E6) and no instruction in the ROM
# references it (verified by an operand scan); the game is frozen anyway.
SOUNDTEST_GO     = 0xFFFDE8
