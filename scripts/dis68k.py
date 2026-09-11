#!/usr/bin/env python3
"""68000 disassembler for the Strider ROM.

Two modes:
  linear    sweep with 2-byte resync over undecodable bytes (default)
  trace     recursive descent following bra/bcc/bsr/jmp/jsr

ROM file offset == 68k address for this cartridge (mapped at $000000).
"""
import argparse, re, sys
from capstone import Cs, CS_ARCH_M68K, CS_MODE_M68K_000

ROM_DEFAULT = "roms/Strider (USA, Europe).md"
ROM_END = 0x100000

BRANCH_RE = re.compile(r"\$([0-9a-f]+)")

def load(rom):
    return open(rom, "rb").read()

def mkmd():
    md = Cs(CS_ARCH_M68K, CS_MODE_M68K_000)
    md.detail = False
    return md

def fmt(i):
    raw = " ".join(f"{b:02x}" for b in i.bytes)
    return f"{i.address:06X}: {raw:<26} {i.mnemonic:<10} {i.op_str}"

def linear(data, start, length, out):
    md = mkmd()
    pc = start
    end = start + length
    while pc < end:
        chunk = data[pc:end]
        got = False
        for i in md.disasm(chunk, pc):
            out.append(fmt(i))
            pc = i.address + i.size
            got = True
        if not got or pc < end:
            if pc < end:
                w = int.from_bytes(data[pc:pc+2], "big")
                out.append(f"{pc:06X}: {data[pc]:02x} {data[pc+1]:02x}"
                           f"{'':<20} dc.w       ${w:04X}")
                pc += 2

TERMINAL = {"rts", "rte", "rtr", "jmp", "bra.b", "bra.w", "bra.s", "bra"}

def trace(data, entries, out, limit=0x20000):
    md = mkmd()
    seen = {}
    todo = list(entries)
    while todo:
        pc = todo.pop()
        while True:
            if pc in seen or pc >= ROM_END or pc < 0:
                break
            chunk = data[pc:pc+16]
            ins = list(md.disasm(chunk, pc, count=1))
            if not ins:
                break
            i = ins[0]
            seen[pc] = fmt(i)
            m = i.mnemonic
            # collect branch/call targets
            if m.startswith(("bra", "bsr", "b")) or m in ("jmp", "jsr"):
                mm = BRANCH_RE.search(i.op_str or "")
                if mm and "(" not in (i.op_str or ""):
                    t = int(mm.group(1), 16)
                    if t < ROM_END:
                        todo.append(t)
            if m in ("rts", "rte", "rtr") or m.startswith("bra") or m == "jmp":
                break
            pc = i.address + i.size
            if len(seen) > limit:
                break
    for a in sorted(seen):
        out.append(seen[a])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("start")
    ap.add_argument("length", nargs="?", default="100")
    ap.add_argument("--rom", default=ROM_DEFAULT)
    ap.add_argument("--mode", choices=["linear", "trace"], default="linear")
    ap.add_argument("--entries", default=None,
                    help="comma separated hex entry points for trace mode")
    a = ap.parse_args()
    data = load(a.rom)
    out = []
    if a.mode == "trace":
        ents = [int(x, 16) for x in (a.entries or a.start).split(",")]
        trace(data, ents, out)
    else:
        linear(data, int(a.start, 16), int(a.length, 16), out)
    print("\n".join(out))

if __name__ == "__main__":
    main()
