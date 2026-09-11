#!/usr/bin/env python3
"""Static analysis report for Strider CPS-1's sound system.

Writes analysis-arcade/tables/*.json. Everything is derived by following the
pointers the Z80 sound driver itself uses.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import arcade_info as A

OUT = "analysis/arcade/tables"


def main():
    d = A.z80_rom()
    rom = A.oki_rom()
    os.makedirs(os.path.join(ROOT, OUT), exist_ok=True)
    rep = {}

    print("=== hardware (mame -listxml strider) ===")
    print("  68000    10.000000 MHz  main")
    print("  Z80       3.579545 MHz  audiocpu = 09.12b (64 KiB)")
    print("  YM2151    3.579545 MHz")
    print("  OKIM6295  1.000000 MHz  samples = 18.11c + 19.12c (256 KiB)")
    print("  output: MONO")

    print("\n=== Z80 sound map ===")
    for a, lbl in ((0x0000, "ROM, direct -> ROM $0000-$7FFF"),
                   (0x8000, "ROM, banked -> bank0 ROM $8000 (all $FF), "
                            "bank1 ROM $C000"),
                   (0xD000, "RAM ($D000-$D7FF)"),
                   (0xF000, "YM2151 address"), (0xF001, "YM2151 data"),
                   (0xF002, "OKIM6295"), (0xF004, "ROM bank select"),
                   (0xF006, "OKI pin 7 / sample bank"),
                   (0xF008, "soundlatch (sound code from the 68000)"),
                   (0xF00A, "soundlatch2 (fade)")):
        print(f"  ${a:04X}  {lbl}")

    print("\n=== sound command dispatch ($0137) ===")
    fm, n1, n2 = A.fm_sequence_table(d)
    ok, no = A.oki_code_table(d)
    print(f"  $00-${len(fm)-1:02X}  FM sequence  ({len(fm)} codes: {n1} from the "
          f"fixed table at ${A.FM_TBL1:04X} + {n2} from bank 1 at "
          f"${A.FM_TBL2:04X} in ROM)")
    print(f"  $80-${0x80+no-1:02X}  OKIM6295 sample ({no} codes; the index is "
          f"taken modulo {no}, so $9D-$EF alias back)")
    print(f"  $F0-$F9  driver commands (jump table at ${A.CMD_JUMP_TBL:04X})")
    print(f"  $FA-$FF  -> $0394 = RET (no-op; $FF is the game's idle value)")

    by_ptr = {}
    for code, ptr, bank in fm:
        by_ptr.setdefault(ptr, []).append(code)
    print(f"\n  distinct sequence pointers: {len(by_ptr)} of {len(fm)} codes")
    for ptr, codes in sorted(by_ptr.items()):
        if len(codes) > 1:
            print(f"    ${ptr:04X} shared by "
                  + " ".join(f"${c:02X}" for c in codes))
    rep["fm_codes"] = [dict(code=f"${c:02X}", ptr=f"${p:04X}",
                            bank=("fixed" if b is None else f"bank{b}"))
                       for c, p, b in fm]

    print(f"\n=== OKI code table (${A.OKI_TBL:04X}, count at ${A.OKI_TBL_COUNT:04X}) ===")
    ph = {p["index"]: p for p in A.oki_phrases(rom)}
    rows = []
    for code, b, stop in ok:
        i = code - 0x80
        p = ph[i]
        voice = {0x10: 0, 0x20: 1, 0x40: 2, 0x80: 3}.get(b & 0xF0)
        rng = ("null entry" if p["empty"]
               else f"${p['start']:06X}-${p['end']:06X} ({p['length']} bytes)")
        print(f"  ${code:02X} -> phrase {i:2d}  play ${b:02X} "
              f"(voice {voice}, atten {b & 0x0F})  {rng}")
        rows.append(dict(code=f"${code:02X}", phrase=i, play_byte=f"${b:02X}",
                         oki_voice=voice, attenuation=b & 0x0F,
                         rom=None if p["empty"] else f"${p['start']:06X}-${p['end']:06X}",
                         adpcm_bytes=0 if p["empty"] else p["length"]))
    rep["oki_codes"] = rows

    print("\n=== OKIM6295 phrase table ===")
    valid = [p for p in A.oki_phrases(rom)
             if not p["empty"] and p["start"] != 0xFFFFFF
             and p["end"] >= p["start"] and p["end"] < len(rom)]
    print(f"  {A.OKI_PHRASE_ENTRIES} entries x {A.OKI_PHRASE_SIZE} bytes at "
          f"$000000; populated: {valid[0]['index']}..{valid[-1]['index']} "
          f"({len(valid)})")
    print(f"  entry 0 is null; 29-31 are zero; 32-127 are $FF padding")
    print(f"  sample data ${valid[0]['start']:06X}-${valid[-1]['end']:06X}, "
          f"{sum(p['length'] for p in valid)} bytes, contiguous")
    rep["oki_phrases"] = [dict(index=p["index"], start=f"${p['start']:06X}",
                               end=f"${p['end']:06X}", length=p["length"])
                          for p in valid]

    print(f"\n=== driver commands ===")
    cmds = {0xF0: "$02F6 full stop / reset (stop OKI + silence FM + clear state)",
            0xF1: "$02F9 clear channel state and sequence state",
            0xF2: "$02FC clear the 8-entry channel array at $D100",
            0xF3: "$0335 clear $D012 and the $D300 sequence block",
            0xF4: "$0347 stop OKI then silence FM",
            0xF5: "$034A silence FM",
            0xF6: "$036B stop OKI, clear $D023/$D024",
            0xF7: "$0385 stop OKI voices 1 and 0",
            0xF8: "$0388 stop OKI voice 0 (writes $08)",
            0xF9: "$038E stop OKI voice 1 (writes $10)"}
    for c in sorted(cmds):
        print(f"  ${c:02X} -> {cmds[c]}")
    rep["commands"] = {f"${c:02X}": cmds[c] for c in sorted(cmds)}

    print(f"\n=== YM2151 timer housekeeping ===")
    print(f"  registers written continuously even when idle: "
          + " ".join(f"${r:02X}" for r in sorted(A.YM_TIMER_REGS)))
    print(f"  (measured over 900 idle frames on the test screen; nothing else)")

    with open(os.path.join(ROOT, OUT, "summary.json"), "w") as f:
        json.dump(rep, f, indent=2)
    print(f"\nwrote {OUT}/summary.json")


if __name__ == "__main__":
    main()
