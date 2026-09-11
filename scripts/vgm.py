#!/usr/bin/env python3
"""Turn a capture.lua chip-write log into a VGM file, and analyse chip usage.

Log line format (see scripts/capture.lua):
    <seconds since poke> <ym|psg> <offset 0..3> <byte value>
YM offsets: 0 = port0 addr, 1 = port0 data, 2 = port1 addr, 3 = port1 data.
"""
import struct

# Genesis NTSC clocks, matching MAME's megadriv: MASTER_CLOCK_NTSC = 53693175
MASTER_NTSC   = 53693175
YM2612_CLOCK  = MASTER_NTSC // 7    # 7670453
SN76489_CLOCK = MASTER_NTSC // 15   # 3579545
SN_FEEDBACK   = 0x0009
SN_SR_WIDTH   = 16
VGM_RATE      = 44100               # VGM timebase, always 44100


def parse_log(path):
    """-> (meta dict, [(t_seconds, kind, offset, value), ...])"""
    meta, events = {}, []
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                if line.startswith("# STRAY"):
                    meta.setdefault("stray_lines", []).append(line[8:].strip())
                else:
                    for tok in line[1:].split():
                        if "=" in tok:
                            k, v = tok.split("=", 1)
                            meta[k] = v
                continue
            line = line.strip()
            if not line:
                continue
            t, kind, off, val = line.split()
            events.append((float(t), kind, int(off), int(val)))
    return meta, events


def to_chip_writes(events):
    """Pair YM address/data writes. -> [(t, 'ym', port, reg, val) | (t,'psg',None,None,val)]"""
    latched = {0: None, 1: None}
    out = []
    for t, kind, off, val in events:
        if kind == "psg":
            out.append((t, "psg", None, None, val))
        else:
            port = off >> 1
            if off & 1:                       # data write
                reg = latched[port]
                if reg is not None:
                    out.append((t, "ym", port, reg, val))
            else:                             # address write
                latched[port] = val
    return out


def build_vgm(writes, total_seconds=None):
    """Assemble a VGM 1.50 body + header. Returns bytes."""
    body = bytearray()
    cur = 0                                    # current position in VGM samples

    def wait(n):
        nonlocal cur
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

    for t, kind, port, reg, val in writes:
        target = int(round(t * VGM_RATE))
        if target > cur:
            wait(target - cur)
            cur = target
        if kind == "psg":
            body.extend(bytes((0x50, val)))
        else:
            body.extend(bytes((0x52 + port, reg, val)))

    if total_seconds is not None:
        target = int(round(total_seconds * VGM_RATE))
        if target > cur:
            wait(target - cur)
            cur = target
    body.append(0x66)                           # end of sound data

    DATA_OFF = 0x40
    hdr = bytearray(DATA_OFF)
    hdr[0x00:0x04] = b"Vgm "
    struct.pack_into("<I", hdr, 0x04, DATA_OFF + len(body) - 4)   # EOF offset
    struct.pack_into("<I", hdr, 0x08, 0x00000150)                 # version 1.50
    struct.pack_into("<I", hdr, 0x0C, SN76489_CLOCK)
    struct.pack_into("<I", hdr, 0x18, cur)                        # total samples
    struct.pack_into("<H", hdr, 0x28, SN_FEEDBACK)
    hdr[0x2A] = SN_SR_WIDTH
    hdr[0x2B] = 0
    struct.pack_into("<I", hdr, 0x2C, YM2612_CLOCK)
    struct.pack_into("<I", hdr, 0x34, DATA_OFF - 0x34)            # data offset
    return bytes(hdr) + bytes(body)


def analyse(writes):
    """Which FM/PSG channels and DAC the sound actually uses."""
    fm_keyon   = set()          # 1..6
    fm_touched = set()
    psg_latched = set()         # 0..3 (3 = noise)
    psg_audible = set()
    dac_writes  = 0
    dac_enabled = False
    ym_regs    = set()
    psg_last_latch = None

    for t, kind, port, reg, val in writes:
        if kind == "psg":
            if val & 0x80:
                ch = (val >> 5) & 3
                psg_latched.add(ch)
                psg_last_latch = (ch, bool(val & 0x10))
                if (val & 0x10) and (val & 0x0F) != 0x0F:
                    psg_audible.add(ch)
            continue
        ym_regs.add((port, reg))
        if port == 0 and reg == 0x2A:
            dac_writes += 1
        elif port == 0 and reg == 0x2B:
            dac_enabled = dac_enabled or bool(val & 0x80)
        elif port == 0 and reg == 0x28:
            sel = val & 0x07
            if sel in (0, 1, 2):
                ch = sel + 1
            elif sel in (4, 5, 6):
                ch = sel
            else:
                continue
            fm_touched.add(ch)
            if val & 0xF0:
                fm_keyon.add(ch)
        elif 0x30 <= reg <= 0xB6:
            sub = reg & 3
            if sub != 3:
                fm_touched.add(sub + 1 + (3 if port else 0))

    return dict(
        fm_keyon=sorted(fm_keyon),
        fm_touched=sorted(fm_touched),
        psg_latched=sorted(psg_latched),
        psg_audible=sorted(psg_audible),
        dac_writes=dac_writes,
        dac_enabled=dac_enabled,
        ym_reg_count=len(ym_regs),
    )
