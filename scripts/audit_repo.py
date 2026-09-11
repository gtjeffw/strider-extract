#!/usr/bin/env python3
"""Audit what ROM-derived content is present in the *tracked* files.

The repository's stated policy is: ship the recipe, not the result. Generated
disassembly, extracted audio and dumped data tables all live in untracked
directories; the write-ups quote short disassembly excerpts for commentary but
reproduce no verbatim ROM data tables.

This checks that, rather than trusting it:

  1. Lists every tracked file, so the set under discussion is explicit.
  2. Confirms no tracked path falls inside roms/, build/, output/ or analysis/.
  3. Scans every tracked text file for runs of >= MIN_RUN hex bytes and tests
     each run against the actual ROM images. Any hit is verbatim ROM content.
  4. Counts disassembly-shaped lines (address + mnemonic) as a measure of how
     much original code is quoted.

Exit status is non-zero if (2) or (3) finds anything, so it can gate a commit.
Needs the ROMs present for step 3; without them it says so and skips it.
"""
import os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

MIN_RUN = 4          # bytes; shorter runs are not meaningfully "ROM data"
TEXT_EXT = (".md", ".py", ".lua", ".txt", ".ini", ".cfg", ".yml", ".yaml", "")
FORBIDDEN_PREFIXES = ("roms/", "build/", "output/", "analysis/", "tools/")
ALLOWED_IN_ROMS = ("roms/README.md", "roms/.gitkeep")

HEXRUN = re.compile(r'(?:\b[0-9A-Fa-f]{2}\b[ \t]+){%d,}\b[0-9A-Fa-f]{2}\b'
                    % (MIN_RUN - 1))
M68K = (r'move|movea|movem|lea|jmp|jsr|bsr|bra|b(?:eq|ne|cc|cs|ge|lt|mi|pl)|'
        r'rts|rte|add|adda|addq|sub|suba|subq|cmp|cmpi|cmpa|tst|btst|bset|'
        r'bclr|and|andi|or|ori|eor|not|neg|clr|swap|ext|lsl|lsr|asl|asr|rol|'
        r'ror|dbra|moveq|nop|mulu|muls|divu|chk')
Z80 = (r'LD|JP|JR|CALL|RET|RETI|DI|EI|IM|PUSH|POP|ADD|ADC|SUB|SBC|AND|OR|XOR|'
       r'CP|INC|DEC|BIT|SET|RES|RL|RR|RLC|RRC|SLA|SRA|SRL|EX|EXX|DJNZ|NOP|'
       r'RST|NEG|CPL|HALT|OUT|IN')
DISLINE = re.compile(r'^\s*([0-9A-Fa-f]{4,6}):\s+(?:%s|%s)\b' % (M68K, Z80),
                     re.IGNORECASE)


def tracked_files():
    r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit("not a git repository")
    return [f for f in r.stdout.split("\n") if f]


def load_roms():
    roms = {}
    try:
        import rominfo as R
        p = os.path.join(ROOT, R.ROM_NAME)
        if os.path.exists(p):
            roms["megadrive"] = open(p, "rb").read()
    except Exception:
        pass
    try:
        import arcade_info as A
        if os.path.isdir(A.ROMDIR):
            roms["cps1-z80"] = A.z80_rom()
            roms["cps1-oki"] = A.oki_rom()
    except Exception:
        pass
    return roms


def main():
    files = tracked_files()
    roms = load_roms()
    fail = 0

    print(f"tracked files: {len(files)}")
    by_dir = {}
    for f in files:
        by_dir.setdefault(os.path.dirname(f) or ".", []).append(f)
    for d in sorted(by_dir):
        print(f"  {d + '/':16s} {len(by_dir[d])}")

    print("\n[1] generated / ROM directories must not be tracked")
    bad = [f for f in files
           if f.startswith(FORBIDDEN_PREFIXES) and f not in ALLOWED_IN_ROMS]
    if bad:
        fail = 1
        for f in bad:
            print(f"    FAIL tracked: {f}")
    else:
        print("    ok - nothing tracked under "
              + ", ".join(p.rstrip('/') + '/' for p in FORBIDDEN_PREFIXES))
        print("    (allowed exceptions: " + ", ".join(ALLOWED_IN_ROMS) + ")")

    print(f"\n[2] verbatim ROM byte runs (>= {MIN_RUN} bytes) in tracked text")
    if not roms:
        print("    SKIPPED - no ROMs present; put them in roms/ to run this check")
    else:
        print("    checking against: " + ", ".join(
            f"{k} ({len(v)} bytes)" for k, v in roms.items()))
        hits = []
        for f in files:
            if os.path.splitext(f)[1] not in TEXT_EXT:
                continue
            path = os.path.join(ROOT, f)
            if not os.path.isfile(path):
                continue
            for i, ln in enumerate(open(path, errors="replace"), 1):
                for m in HEXRUN.finditer(ln):
                    try:
                        b = bytes.fromhex(re.sub(r"[ \t]+", "", m.group(0)))
                    except ValueError:
                        continue
                    if len(b) < MIN_RUN:
                        continue
                    for name, rom in roms.items():
                        if b in rom:
                            hits.append((f, i, name, len(b), m.group(0)))
                            break
        if hits:
            fail = 1
            for f, i, name, n, txt in hits:
                print(f"    FAIL {f}:{i}  {n} bytes from {name}: {txt[:56]}")
            print(f"    total {sum(h[3] for h in hits)} verbatim ROM bytes")
        else:
            print("    ok - 0 verbatim ROM bytes found")

    print("\n[3] quoted disassembly (address + mnemonic lines)")
    tot, per = 0, {}
    for f in files:
        if os.path.splitext(f)[1] not in TEXT_EXT:
            continue
        path = os.path.join(ROOT, f)
        if not os.path.isfile(path):
            continue
        n = sum(1 for ln in open(path, errors="replace") if DISLINE.match(ln))
        if n:
            per[f] = n
            tot += n
    for f, n in sorted(per.items(), key=lambda kv: -kv[1]):
        print(f"    {f:26s} {n:4d} lines")
    print(f"    total {tot} lines (~{tot * 3} bytes of original code), quoted "
          f"with commentary")
    print("    informational, not a failure: short excerpts are the normal form")
    print("    for reverse-engineering write-ups.")

    print("\nRESULT: " + ("FAIL" if fail else "PASS"))
    return fail


if __name__ == "__main__":
    sys.exit(main())
