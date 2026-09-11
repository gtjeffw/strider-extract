#!/usr/bin/env python3
"""Z80 disassembler.

Two built-in targets plus a generic mode:

  --target genesis   Strider MD: the DAC player blob at ROM $0B6E86 ($170 bytes),
                     which the 68000 copies to Z80 RAM $0000.
  --target arcade    Strider CPS-1: the sound program ROM strider_arcade/09.12b.
  --file F --base B --offset O --length L   anything else
"""
import argparse, os, sys
from z80dis import z80

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

TARGETS = {
    "genesis": dict(file=os.path.join(ROOT, "roms/Strider (USA, Europe).md"),
                    offset=0x0B6E86, length=0x170, base=0x0000,
                    label="MD Z80 DAC player (uploaded to Z80 $0000)"),
    "arcade":  dict(file=os.path.join(ROOT, "roms/strider/09.12b"),
                    offset=0x0000, length=0x8000, base=0x0000,
                    label="CPS-1 Z80 sound program (direct-mapped $0000-$7FFF)"),
}


def disasm(data, base, out=sys.stdout, rom_off=None):
    pc = 0
    while pc < len(data):
        try:
            dec = z80.decode(data[pc:pc + 8], base + pc)
            n = dec.len
            if n == 0:
                raise ValueError
            txt = z80.decoded2str(dec)
        except Exception:
            n = 1
            txt = f"db ${data[pc]:02x}"
        raw = " ".join(f"{b:02x}" for b in data[pc:pc + n])
        pfx = f"{base+pc:04X}"
        if rom_off is not None:
            pfx += f" ({rom_off+pc:06X})"
        print(f"{pfx}: {raw:<14} {txt}", file=out)
        pc += n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=sorted(TARGETS))
    ap.add_argument("--file")
    ap.add_argument("--offset", default="0")
    ap.add_argument("--length", default=None)
    ap.add_argument("--base", default=None)
    a = ap.parse_args()
    if a.target:
        t = TARGETS[a.target]
        path, off, ln, base = t["file"], t["offset"], t["length"], t["base"]
        print(f"; {t['label']}")
    else:
        if not a.file:
            ap.error("need --target or --file")
        path = a.file
        off = int(a.offset, 16)
        ln = int(a.length, 16) if a.length else None
        base = int(a.base, 16) if a.base else off
    data = open(path, "rb").read()
    if ln is None:
        ln = len(data) - off
    disasm(data[off:off + ln], base, rom_off=off if off != base else None)


if __name__ == "__main__":
    main()
