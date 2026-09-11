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


# ---- on-screen menu labels ----------------------------------------------
# The service menu draws each line through $023F8E, which takes a 10-byte
# display descriptor:
#
#   +$00 word  number of runs, minus 1
#   +$02 long  pointer to the text (NOT two 16-bit fields - getting this wrong
#              is what made the labels look unrecoverable at first)
#   +$06 long  VDP control longword: the nametable address to write
#
# $023F8E then reads bytes from the text pointer until $FF, adds the caller's
# tile base (d0, $A000 here) to each and writes it to the VDP data port. So the
# "text" is a run of tile indices, and the font happens to be laid out in a
# linear order that decodes trivially.
MENU_DESCRIPTORS = {          # which variable each descriptor table drives
    0xFFFDE0: 0x0240AC,      # menu line: LEVEL SELECT / PLAYERS / SOUND SELECT
    0xFFFDE2: 0x0240CA,      # difficulty: EASY / NORMAL / HARD
    0xFFFDE4: 0x0240E8,      # lives: 3 / 4 / 5
    SOUNDTEST_INDEX: 0x024106,   # the 66 sound-test labels
}
MENU_DESCRIPTOR_SIZE = 10
MENU_TEXT_END = 0xFF

# Tile index -> character. Derived by spotting "THE" as $1F $13 $10 in
# "MOSQUE THE COLD-HEARTED", which fixes $0C = 'A'; the rest follows and leaves
# no unresolved codes across all 75 menu strings.
MENU_CHARSET = {0x00: " ", 0x28: "-", 0x2A: ".", 0x2B: "!"}
MENU_CHARSET.update({0x02 + i: chr(ord("0") + i) for i in range(10)})
MENU_CHARSET.update({0x0C + i: chr(ord("A") + i) for i in range(26)})


def menu_text(d, ptr):
    """Decode one $FF-terminated run of tile indices to a string."""
    out = []
    while d[ptr] != MENU_TEXT_END:
        out.append(MENU_CHARSET.get(d[ptr], f"<{d[ptr]:02X}>"))
        ptr += 1
    return "".join(out)


def menu_labels(d, table, count):
    """Decode `count` display descriptors starting at `table`."""
    return [menu_text(d, be32(d, table + MENU_DESCRIPTOR_SIZE * i + 2))
            for i in range(count)]


def soundtest_labels(d):
    """The 66 labels the sound test shows, in menu-index order.

    Indices 0-30 are the real music titles; 31-65 are the game's own effect
    numbering, S.E.00 to S.E.34.
    """
    return menu_labels(d, MENU_DESCRIPTORS[SOUNDTEST_INDEX], SOUNDTEST_COUNT)


def label_slug(text):
    """Filename-safe form of a menu label: DEFENSE LINE -> DEFENSE_LINE,
    S.E.07 -> SE07."""
    out = []
    for c in text:
        if c.isalnum():
            out.append(c)
        elif c in " -":
            out.append("_")
        # '.' and '!' are dropped
    return "_".join(filter(None, "".join(out).split("_")))

# Human-readable labels for the `move.b <ea>,$FF9C0A/0B/0C.w` sites outside the
# $020000 stub table, i.e. the places the *game* (not the harness) can enqueue a
# sound.
#
# These are annotations only. scripts/patch_rom.py finds the sites itself by an
# exhaustive opcode scan of whichever ROM you supply, and patches what it finds;
# an address missing from this dict just prints as "(unannotated)". So a
# different revision with different addresses still gets patched correctly.
QUEUE_WRITER_NOTES = {
    0x02544: "script/cutscene sound trigger",
    0x029BA: "plays speech sample $D6",
    0x03446: "THE SOUND TEST dispatch",
    0x034D0: "stage BGM selector (table $0361E)",
    0x95EEA: "ending/staff roll",
    0x95EF2: "ending speech sample",
    0x95FC0: "ending stop-all",
}
# The one site the soundtest ROM variant must leave intact.
SOUNDTEST_DISPATCH = 0x03446


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


def identity_problems(d):
    """SHA-1 mismatch against the documented revision. Advisory, not fatal."""
    import hashlib
    got = hashlib.sha1(d).hexdigest()
    return [] if got == ROM_SHA1 else [(ROM_NAME, ROM_SHA1, got)]


def verify(d, warn=None):
    """Identity is advisory, structure is decisive.

    Mirrors arcade_info.verify(): a hash mismatch only warns, because if the
    layout still checks out the pipeline can proceed and say what it is working
    on. A structural failure raises, because then the addresses are wrong.
    """
    import sys
    if warn is None:
        def warn(msg):
            print(msg, file=sys.stderr)

    ident = identity_problems(d)
    if ident:
        warn("WARNING: Mega Drive ROM is not the documented revision:\n"
             + "\n".join(f"  {n}: expected SHA-1 {w}\n  {'':>{len(n)}}  got      {g}"
                          for n, w, g in ident)
             + "\n  Proceeding because the structural checks pass, but the"
               " addresses in\n  docs/megadrive.md may not describe this"
               " revision. See roms/README.md.")
    struct = verify_tables(d, strict=True)
    return ident, struct


def verify_tables(d, strict=True):
    """Structural sanity checks on the pointer tables.

    These are the invariants that confirmed the song and SFX counts in the first
    place: both tables must be strictly increasing, and each must abut the data
    it points into. If the baked-in addresses do not fit the ROM you supplied,
    this fails loudly instead of letting the analysis print nonsense.
    """
    problems = []

    songs = song_table(d)
    sfxs = sfx_table(d)
    prio = be32(d, BANK_BASE + HDR_PRIO_TBL)
    sfx_tbl = be32(d, BANK_BASE + HDR_SFX_TBL)

    if songs != sorted(songs) or len(set(songs)) != len(songs):
        problems.append("song pointer table is not strictly increasing")
    if sfxs != sorted(sfxs) or len(set(sfxs)) != len(sfxs):
        problems.append("sfx pointer table is not strictly increasing")

    song_tbl_end = be32(d, BANK_BASE + HDR_SONG_TBL) + 4 * SONG_COUNT
    if song_tbl_end != prio:
        problems.append(
            f"song table ends ${song_tbl_end:06X} but the priority table starts "
            f"${prio:06X} - SONG_COUNT ({SONG_COUNT}) looks wrong")
    sfx_tbl_end = sfx_tbl + 4 * SFX_COUNT
    if sfx_tbl_end != sfxs[0]:
        problems.append(
            f"sfx table ends ${sfx_tbl_end:06X} but the first sfx header is at "
            f"${sfxs[0]:06X} - SFX_COUNT ({SFX_COUNT}) looks wrong")
    if not (BANK_BASE < songs[0] < sfx_tbl):
        problems.append("song data does not lie between the bank header and "
                        "the sfx pointer table")

    # DAC tables must chain exactly, with the first sample right after the table
    for key, (base, n) in DAC_BANKS.items():
        ents = [e for e in dac_entries(d) if e["bank"] == key]
        if ents[0]["z80_start"] != 0x8000 + n * DAC_ENTRY_SIZE:
            problems.append(f"DAC bank {key}: first sample does not follow the "
                            f"{n}-entry table")
        for a, b in zip(ents, ents[1:]):
            if a["z80_start"] + a["length"] != b["z80_start"]:
                problems.append(f"DAC bank {key}: samples do not chain at "
                                f"${a['rom_start']:06X}")

    if problems and strict:
        raise SystemExit("ROM structure does not match the documented layout:\n"
                         + "\n".join("  " + p for p in problems)
                         + "\nIs this the expected revision? See roms/README.md.")
    return problems
