#!/usr/bin/env python3
"""Write a human-readable MANIFEST.md beside each platform's sounds.csv.

Reads the CSV the extractor already produced, so this needs no emulation and
can be re-run at any time. `sounds.csv` / `sounds.json` remain the
machine-readable form; this is the version meant to be browsed.
"""
import argparse, csv, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PLATFORMS = {
    "megadrive": dict(
        title="Strider (Mega Drive) — sound manifest",
        blurb=(
            "87 sound ids, rendered by the game's own SMPS-68k driver inside "
            "MAME.\n\nFilenames carry both numbers and the game's own label:\n\n"
            "* `MUS_17_91_ST19_HIRYU.wav` — 17th music id, id `$91`, "
            "sound-test index 19, titled *HIRYU*\n"
            "* `SFX_001_A0_ST31_SE00.wav` — 1st effect id, id `$A0`, "
            "sound-test index 31, the game's *S.E.00*\n"
            "* `SFX_002_A1.wav` — the sound test cannot reach `$A1`, so it has "
            "no index and no label\n\n"
            "Sequence numbers follow ROM order, because the 21 unreachable ids "
            "have no menu position. The `ST` column is what ties a file to the "
            "sound test.\n"),
        id_col="sound_id",
        cols=[("soundtest_index", "ST", 4), ("soundtest_label", "Label", 24),
              ("sound_id", "Id", 4), ("classification", "Class", 6),
              ("synthesis", "Synthesis", 12), ("duration_s", "Dur s", 8),
              ("fm_channels_used", "FM ch", 12),
              ("psg_channels_used", "PSG", 5),
              ("dac_used", "DAC", 5), ("wav", "File", 0)],
    ),
    "arcade": dict(
        title="Strider (CPS-1 arcade) — sound manifest",
        blurb=(
            "123 sound codes, rendered by the game's own Z80 driver inside "
            "MAME.\n\nThe CPS-1 sound test was not used, so there are no "
            "in-game labels on this side; codes are classified from the "
            "YM2151 channels they key on (music uses 1-6, effects only 7-8).\n\n"
            "`$80` selects a null OKI phrase-table entry and is silent by "
            "design, so it has a row and no file.\n"),
        id_col="sound_code",
        cols=[("sound_code", "Code", 5), ("classification", "Class", 6),
              ("role_detail", "Role", 12), ("synthesis", "Synthesis", 22),
              ("duration_s", "Dur s", 8),
              ("ym2151_channels", "YM ch", 12),
              ("oki_phrases", "OKI ph", 7), ("wav", "File", 0)],
    ),
}


def write_manifest(plat):
    spec = PLATFORMS[plat]
    src = os.path.join(ROOT, "output", plat, "sounds.csv")
    if not os.path.exists(src):
        print(f"  {plat}: no sounds.csv yet, skipping", file=sys.stderr)
        return None
    rows = list(csv.DictReader(open(src)))
    out = os.path.join(ROOT, "output", plat, "MANIFEST.md")
    with open(out, "w") as f:
        f.write(f"# {spec['title']}\n\n{spec['blurb']}\n")
        f.write(f"Generated from `sounds.csv` by `scripts/make_manifest.py` "
                f"(`make manifest`). {len(rows)} entries.\n\n")
        hdr = [c[1] for c in spec["cols"]]
        f.write("| " + " | ".join(hdr) + " |\n")
        f.write("|" + "|".join("---" for _ in hdr) + "|\n")
        for r in rows:
            cells = []
            for key, _lbl, _w in spec["cols"]:
                v = r.get(key, "")
                if key == "wav":
                    v = f"`{os.path.basename(v)}`" if v else "*(silent, no file)*"
                elif key in ("sound_id", "sound_code"):
                    v = f"`{v}`"
                elif key == "dac_used":
                    v = "yes" if v == "True" else "—"
                elif v == "":
                    v = "—"
                cells.append(v)
            f.write("| " + " | ".join(cells) + " |\n")
    return out, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=sorted(PLATFORMS) + ["all"],
                    default="all")
    a = ap.parse_args()
    todo = sorted(PLATFORMS) if a.platform == "all" else [a.platform]
    for plat in todo:
        r = write_manifest(plat)
        if r:
            print(f"  wrote {os.path.relpath(r[0], ROOT)} ({r[1]} entries)")


if __name__ == "__main__":
    main()
