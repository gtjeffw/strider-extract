#!/usr/bin/env python3
"""Build patched copies of the Strider ROM. The original is never modified.

`--mode mute` (default) produces a ROM in which the *game* can never enqueue a
sound, so the only sound requests are the ones the capture harness pokes into
$FF9C0A. Everything else - boot, VDP setup, Z80 DAC blob upload, the VBlank
driver tick - runs untouched, so the sound driver stays fully live and
correctly initialised.

Patches:
  1. Each of the 42 "direct" stubs: `move.b #id,$9C0A.w` -> 3x NOP.
  2. Both entry points of the queue-slot allocator ($20322 and $20324, the
     latter being the bra.w target of the 46 "queued" stubs) -> RTS.
  3. The 7 remaining `move.b <ea>,$9C0A.w` sites found by opcode scan.
     $02544 is rewritten as `move.b (a2)+,d0` + NOP so the (a2)+ side effect
     that its caller depends on is preserved.
  4. Header checksum at $18E recomputed, otherwise the boot check at $000336
     drops the game into the hang loop at $0003E4.
"""
import argparse, hashlib, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rominfo as R

NOP = b"\x4e\x71"
RTS = b"\x4e\x75"

HDR_CHECKSUM = 0x18E
HDR_ROM_END  = 0x1A4


def sega_checksum(buf):
    """Sum of big-endian words from $000200 to the ROM-end field, inclusive.

    Exactly what the boot code at $000336 computes.
    """
    end = struct.unpack_from(">I", buf, HDR_ROM_END)[0]
    s, a = 0, 0x200
    while a <= end:
        s = (s + struct.unpack_from(">H", buf, a)[0]) & 0xFFFF
        a += 2
    return s


def scan_queue_writers(data):
    """Exhaustively find `move.b <ea>,$FF9C0A/0B/0C.w` instructions.

    Encoding: 0x11 0xC0..0xFF <0..2 extension words> 0x9C0A/0B/0C
    """
    found = []
    for lo in (0x0A, 0x0B, 0x0C):
        ext = bytes([0x9C, lo])
        s = 0
        while True:
            i = data.find(ext, s)
            if i < 0:
                break
            s = i + 1
            if i % 2:
                continue
            for back in (2, 4, 6):
                j = i - back
                if j >= 0 and data[j] == 0x11 and data[j + 1] >= 0xC0:
                    found.append((j, back + 2, 0x9C00 | lo))
                    break
    return sorted(set(found))


def assemble_freeze_loop(base):
    """Relocated main loop with a mode bounds check.

    base+00  move.w  $E100.w,d0
    base+04  cmpi.w  #13,d0
    base+08  bcc.b   base+00        ; mode >= 13 -> spin forever (frozen)
    base+0A  lsl.w   #2,d0
    base+0C  lea     $000392.l,a1   ; original mode jump table
    base+12  movea.l (a1,d0.w),a0
    base+16  jsr     (a0)
    base+18  bra.b   base+00
    """
    code = bytearray()
    code += b"\x30\x38\xe1\x00"                  # move.w $e100.w,d0
    code += b"\x0c\x40\x00\x0d"                  # cmpi.w #13,d0
    code += b"\x64" + bytes([(0x00 - 0x0A) & 0xFF])  # bcc.b  base+0 (from base+0A)
    code += b"\xe5\x48"                           # lsl.w #2,d0
    code += b"\x43\xf9" + struct.pack(">I", R.MODE_TABLE)   # lea $392.l,a1
    code += b"\x20\x71\x00\x00"                  # movea.l (a1,d0.w),a0
    code += b"\x4e\x90"                           # jsr (a0)
    code += b"\x60" + bytes([(0x00 - 0x1A) & 0xFF])  # bra.b base+0 (from base+1A)
    assert len(code) == 0x1A, len(code)
    return bytes(code)


def assemble_soundtest_loop(base):
    """As assemble_freeze_loop, but the frozen branch polls a one-shot flag and,
    when set, calls the game's own sound-test PLAY path at $003442.

    That path is `move.w $fde6.w,d0 / move.b $344E(pc,d0.w),$9C0A.w`, i.e. the
    real menu-index -> sound-id lookup, so a capture driven this way exercises
    the game's code rather than the harness's own idea of the mapping.

    base+00  move.w  $E100.w,d0
    base+04  cmpi.w  #13,d0
    base+08  bcc.b   base+1A         ; frozen -> sound-test polling
    base+0A  lsl.w   #2,d0
    base+0C  lea     $000392.l,a1
    base+12  movea.l (a1,d0.w),a0
    base+16  jsr     (a0)
    base+18  bra.b   base+00
    base+1A  tst.b   $FDE8.w         ; harness "play now" flag
    base+1E  beq.b   base+00
    base+20  clr.b   $FDE8.w         ; consume it: exactly one trigger
    base+24  jsr     $003442.l       ; the game's own sound-test dispatch
    base+2A  bra.b   base+00
    """
    go = R.SOUNDTEST_GO & 0xFFFF
    code = bytearray()
    code += b"\x30\x38\xe1\x00"
    code += b"\x0c\x40\x00\x0d"
    code += b"\x64" + bytes([(0x1A - 0x0A) & 0xFF])
    code += b"\xe5\x48"
    code += b"\x43\xf9" + struct.pack(">I", R.MODE_TABLE)
    code += b"\x20\x71\x00\x00"
    code += b"\x4e\x90"
    code += b"\x60" + bytes([(0x00 - 0x1A) & 0xFF])
    assert len(code) == 0x1A, len(code)
    code += b"\x4a\x38" + struct.pack(">H", go)          # tst.b $FDE8.w
    code += b"\x67" + bytes([(0x00 - 0x20) & 0xFF])       # beq.b base+00
    code += b"\x42\x38" + struct.pack(">H", go)          # clr.b $FDE8.w
    code += b"\x4e\xb9" + struct.pack(">I", R.SOUNDTEST_PLAY)
    code += b"\x60" + bytes([(0x00 - 0x2C) & 0xFF])       # bra.b base+00
    assert len(code) == 0x2C, len(code)
    return bytes(code)


def build_mute(data, freeze=False, soundtest=False):
    buf = bytearray(data)
    log = []

    stubs, stub_end = R.parse_stubs(data)
    for s in stubs:
        if s["kind"] != "direct":
            continue
        a = s["addr"]
        assert buf[a:a+2] == b"\x11\xfc" and buf[a+4:a+6] == b"\x9c\x0a", hex(a)
        buf[a:a+6] = NOP * 3
        log.append(f"0x{a:06X}  stub id ${s['sid']:02X}: move.b #${s['sid']:02X},$9C0A.w -> 3x nop")

    for a in (R.QUEUE_ALLOC, R.QUEUE_ALLOC + 2):
        buf[a:a+2] = RTS
        log.append(f"0x{a:06X}  queue-slot allocator entry -> rts")

    # Discover the game-side queue writers in THIS rom rather than trusting a
    # baked-in list, then neutralise each one in place.
    for addr, size, dst in scan_queue_writers(data):
        if R.STUB_TABLE <= addr < R.QUEUE_ALLOC + 0x30:
            continue                      # the stub table, handled above
        note = R.QUEUE_WRITER_NOTES.get(addr, "(unannotated)")
        if soundtest and addr == R.SOUNDTEST_DISPATCH:
            log.append(f"0x{addr:06X}  {note} -> LEFT INTACT (soundtest mode)")
            continue
        src_mode = (buf[addr + 1] >> 3) & 7
        src_reg = buf[addr + 1] & 7
        if src_mode == 3:
            # (An)+ : the caller may depend on the post-increment, so keep it
            # and throw the byte into d0 instead of removing the instruction.
            repl = bytes((0x10, 0x18 | src_reg)) + NOP * ((size - 2) // 2)
            how = f"move.b (a{src_reg})+,d0 + {(size-2)//2}x nop"
        elif src_mode == 4:
            repl = bytes((0x10, 0x20 | src_reg)) + NOP * ((size - 2) // 2)
            how = f"move.b -(a{src_reg}),d0 + {(size-2)//2}x nop"
        else:
            assert size % 2 == 0, (hex(addr), size)
            repl = NOP * (size // 2)
            how = f"{size//2}x nop"
        assert len(repl) == size, (hex(addr), size, len(repl))
        buf[addr:addr + size] = repl
        log.append(f"0x{addr:06X}  ${dst:04X}.w  {note} -> {how}")

    if freeze or soundtest:
        lo, hi = R.FREEZE_PAD
        assert R.FREEZE_LOOP >= lo and R.FREEZE_LOOP + 0x30 < hi
        assert set(buf[R.FREEZE_LOOP:R.FREEZE_LOOP + 0x30]) == {0xFF}, "pad not free"
        code = (assemble_soundtest_loop(R.FREEZE_LOOP) if soundtest
                else assemble_freeze_loop(R.FREEZE_LOOP))
        buf[R.FREEZE_LOOP:R.FREEZE_LOOP + len(code)] = code
        log.append(f"0x{R.FREEZE_LOOP:06X}  relocated main loop ({len(code)} bytes)"
                   + (" + sound-test call" if soundtest else " with mode bounds check"))
        buf[R.MAIN_LOOP:R.MAIN_LOOP + 6] = b"\x4e\xf9" + struct.pack(">I", R.FREEZE_LOOP)
        log.append(f"0x{R.MAIN_LOOP:06X}  main loop -> jmp $%06X.l" % R.FREEZE_LOOP)

    # sanity: nothing outside the (now-dead) allocator may still target the queue
    allowed = {R.QUEUE_ALLOC, R.QUEUE_ALLOC + 2}
    if soundtest:
        allowed.add(R.SOUNDTEST_DISPATCH)
    live = [f"0x{a:06X}" for a, _, _ in scan_queue_writers(bytes(buf))
            if a not in allowed and not (R.QUEUE_ALLOC <= a <= R.QUEUE_ALLOC + 0x30)]
    assert not live, f"queue writers still live: {live}"

    old = struct.unpack_from(">H", buf, HDR_CHECKSUM)[0]
    new = sega_checksum(buf)
    struct.pack_into(">H", buf, HDR_CHECKSUM, new)
    log.append(f"0x{HDR_CHECKSUM:06X}  header checksum ${old:04X} -> ${new:04X}")
    return bytes(buf), log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default=R.ROM_NAME)
    ap.add_argument("--out", default="build/strider_capture.md")
    ap.add_argument("--mode", default="freeze",
                    choices=["mute", "freeze", "soundtest"])
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    data = R.load(a.rom)
    got = hashlib.sha1(data).hexdigest()
    if got != R.ROM_SHA1:
        print(f"WARNING: ROM sha1 {got} != expected {R.ROM_SHA1}", file=sys.stderr)

    print("exhaustive scan of the ORIGINAL rom for queue writers:")
    for addr, size, dst in scan_queue_writers(data):
        where = "stub table" if R.STUB_TABLE <= addr < 0x20400 else "GAME CODE"
        print(f"  0x{addr:06X} ({size}b) -> ${dst:04X}.w   [{where}]")

    out, log = build_mute(data, freeze=(a.mode == "freeze"),
                          soundtest=(a.mode == "soundtest"))
    assert len(out) == len(data)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "wb") as f:
        f.write(out)
    print(f"\nwrote {a.out} ({len(out)} bytes), {len(log)} patches")
    print(f"  sha1 {hashlib.sha1(out).hexdigest()}")
    if not a.quiet:
        for l in log:
            print("  " + l)


if __name__ == "__main__":
    main()
