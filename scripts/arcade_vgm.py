#!/usr/bin/env python3
"""Chip-write log -> VGM, and chip-usage analysis, for Strider CPS-1.

Log line format (see scripts/arcade_capture.lua):
    <seconds since poke> <ym|oki> <reg> <value>
`ym` reg is the YM2151 register; `oki` reg is always 0 (the chip has a single
data port).

VGM 1.61 is used because that is the first version with an OKIM6295 clock
field. Sounds that touch the OKI also get the 256 KiB sample ROM attached as a
type-$8B data block, without which a player has nothing to decode.
"""
import os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import arcade_info as A

VGM_RATE = 44100
DATA_OFF = 0x100          # VGM 1.61 header size used here


def parse_log(path):
    meta, events = {}, []
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                for tok in line[1:].split():
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        meta[k] = v
                continue
            line = line.strip()
            if not line:
                continue
            t, kind, reg, val = line.split()
            events.append((float(t), kind, int(reg), int(val)))
    return meta, events


def analyse(events):
    """Which YM2151 channels, which OKI voices/phrases, and is it music-like."""
    ym_keyon = set()          # YM2151 channels 0..7 keyed on
    ym_regs = set()
    oki_phrases = set()
    oki_voices = set()        # voices actually started
    oki_stops = set()
    oki_writes = 0
    pending_phrase = None
    music_writes = 0          # YM writes that are not timer housekeeping

    for t, kind, reg, val in events:
        if kind == "ym":
            ym_regs.add(reg)
            if reg not in A.YM_TIMER_REGS:
                music_writes += 1
            if reg == 0x08:                       # key on/off
                ch = val & 0x07
                if val & 0x78:                    # any slot enabled
                    ym_keyon.add(ch)
            continue
        # OKIM6295 protocol: a write with bit 7 set latches a phrase number;
        # the NEXT write then starts it, with the voice mask in bits 4-7 and
        # the attenuation in bits 0-3. A write with bit 7 clear and nothing
        # latched is instead a STOP, with the voice mask in bits 3-6. Treating
        # a stop as a start is what made $8D look like it used two voices.
        oki_writes += 1
        if val & 0x80:
            pending_phrase = val & 0x7F
        elif pending_phrase is not None:
            for v in range(4):
                if val & (0x10 << v):
                    oki_voices.add(v)
                    oki_phrases.add(pending_phrase)
            pending_phrase = None
        else:
            for v in range(4):
                if val & (0x08 << v):
                    oki_stops.add(v)

    return dict(
        ym_channels=sorted(ym_keyon),
        ym_reg_count=len(ym_regs),
        ym_music_writes=music_writes,
        oki_writes=oki_writes,
        oki_voices=sorted(oki_voices),
        oki_stops=sorted(oki_stops),
        oki_phrases=sorted(oki_phrases),
    )


def build_vgm(events, total_seconds=None, include_oki_rom=False):
    body = bytearray()
    cur = 0

    def wait(n):
        while n > 0:
            if n > 65535:
                body.extend(b"\x61" + struct.pack("<H", 65535)); n -= 65535
            elif n == 735:
                body.append(0x62); n = 0
            elif n == 882:
                body.append(0x63); n = 0
            elif n <= 16:
                body.append(0x70 + (n - 1)); n = 0
            else:
                body.extend(b"\x61" + struct.pack("<H", n)); n = 0

    if include_oki_rom:
        rom = A.oki_rom()
        # 0x67 0x66 <type> <size:4> then rom_size:4, start:4, data
        payload = struct.pack("<II", len(rom), 0) + rom
        body.extend(b"\x67\x66\x8b" + struct.pack("<I", len(payload)))
        body.extend(payload)
        # pin 7 high -> /132 divider
        body.extend(bytes((0xB8, 0x0C, 1)))

    for t, kind, reg, val in events:
        target = int(round(t * VGM_RATE))
        if target > cur:
            wait(target - cur)
            cur = target
        if kind == "ym":
            body.extend(bytes((0x54, reg, val)))
        else:
            body.extend(bytes((0xB8, 0x00, val)))

    if total_seconds is not None:
        target = int(round(total_seconds * VGM_RATE))
        if target > cur:
            wait(target - cur)
            cur = target
    body.append(0x66)

    hdr = bytearray(DATA_OFF)
    hdr[0x00:0x04] = b"Vgm "
    struct.pack_into("<I", hdr, 0x04, DATA_OFF + len(body) - 4)
    struct.pack_into("<I", hdr, 0x08, 0x00000161)
    struct.pack_into("<I", hdr, 0x18, cur)                    # total samples
    struct.pack_into("<I", hdr, 0x30, A.YM2151_CLOCK)
    struct.pack_into("<I", hdr, 0x34, DATA_OFF - 0x34)        # data offset
    struct.pack_into("<I", hdr, 0x98, A.OKI_CLOCK)
    return bytes(hdr) + bytes(body)


def validate_vgm(data):
    """Structural check: header fields sane, command stream walks cleanly to $66,
    and the accumulated waits equal the header's total-samples field."""
    errs = []
    if data[:4] != b"Vgm ":
        return ["bad magic"]
    ver = struct.unpack_from("<I", data, 0x08)[0]
    eof = struct.unpack_from("<I", data, 0x04)[0]
    total = struct.unpack_from("<I", data, 0x18)[0]
    doff = struct.unpack_from("<I", data, 0x34)[0]
    if eof + 4 != len(data):
        errs.append(f"EOF offset {eof}+4 != file size {len(data)}")
    start = 0x34 + doff
    p, acc, ended = start, 0, False
    while p < len(data):
        c = data[p]
        if c == 0x66:
            ended = True
            break
        elif c == 0x54 or c == 0x52 or c == 0x53:
            p += 3
        elif c == 0xB8:
            p += 3
        elif c == 0x50:
            p += 2
        elif c == 0x61:
            acc += struct.unpack_from("<H", data, p + 1)[0]; p += 3
        elif c == 0x62:
            acc += 735; p += 1
        elif c == 0x63:
            acc += 882; p += 1
        elif 0x70 <= c <= 0x7F:
            acc += (c & 0x0F) + 1; p += 1
        elif c == 0x67:
            sz = struct.unpack_from("<I", data, p + 3)[0]
            p += 7 + sz
        else:
            errs.append(f"unknown command ${c:02X} at ${p:X}")
            break
    if not ended:
        errs.append("no end-of-data ($66) command")
    if acc != total:
        errs.append(f"accumulated waits {acc} != header total_samples {total}")
    return errs, dict(version=f"{ver:#x}", total_samples=total, size=len(data))
