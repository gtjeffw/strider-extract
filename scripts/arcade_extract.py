#!/usr/bin/env python3
"""End-to-end sound extraction for Strider CPS-1 (MAME set `strider`).

Pipeline, per sound code:
    unmodified ROM set + silent test-screen save state
      -> MAME (headless) writes the code to the 68000 sound latch at $800180
      -> chip-write log (YM2151 + OKIM6295) -> output-arcade/vgm/*.vgm + metadata
      -> MAME -wavwrite 48 kHz mono -> output-arcade/wav/*.wav (trimmed)

No ROM patching is needed: with the "Game Mode" dipswitch set to Test the game
issues no sound commands of its own, and the idle $FF it does write dispatches
to $0394 = RET. Verified silent (peak 0) before any capture runs.

Sound codes are classified from the YM2151 channels they actually key on. The
driver partitions the chip: music uses channels 1-6 and FM sound effects use
only channels 7-8, with no overlap in the whole 94-entry sequence set. That is
a far better discriminator than "does it loop", because 10 of the music entries
are finite cues (stage clear, game over) rather than looping BGM.
"""
import argparse, concurrent.futures as cf, csv, hashlib, json, os, shutil
import subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import arcade_info as A
import arcade_vgm as V
import wavutil as W

MAME        = shutil.which("mame") or "mame"
STATE_NAME  = "arc_clean"
LOG_DIR     = "analysis/arcade/logs"
OUT         = "output/arcade"
OUT_WAV     = OUT + "/wav"
OUT_VGM     = OUT + "/vgm"
SECONDS_CAP = 400          # absolute machine time; the state is at ~25 s

# Capture windows, in ~60 Hz frames.
#
# FM sequences write to the YM2151 continuously while they play, so "the chips
# went quiet" is a valid end-of-sound test for them.
#
# OKIM6295 codes are different: starting a sample takes just three bus writes
# (stop voice, select phrase, play) and the chip then plays the whole thing
# autonomously with no further traffic. Quiet detection therefore measures the
# quiet window, not the sample - it truncated every phrase longer than ~1.3 s.
# So OKI codes get a fixed window comfortably longer than the longest phrase
# (7.77 s) and their true length comes from trimming the rendered audio.
WINDOW = {
    "fm":  dict(max_frames=3600, quiet_frames=90),    # may loop: cap at 60 s
    "oki": dict(max_frames=1100, quiet_frames=1100),  # ~18 s, quiet disabled
}

RENDER = ("MAME {ver} '{set}' headless, YM2151 + OKIM6295 as mixed by the "
          "emulated CPS-1 board, 48 kHz/16-bit MONO -wavwrite")


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def mame_version():
    r = sh([MAME, "-version"])
    return (r.stdout or r.stderr).strip().split()[0]


def build_state(force=False):
    sta = os.path.join(ROOT, "tools/mame/sta", A.MAME_SET, STATE_NAME + ".sta")
    if os.path.exists(sta) and not force:
        return sta
    print("building silent test-screen state ...")
    r = sh([MAME, A.MAME_SET, "-sound", "none", "-nothrottle", "-skip_gameinfo",
            "-seconds_to_run", "120",
            "-autoboot_script", "scripts/arcade_make_state.lua",
            "-autoboot_delay", "0"], cwd=ROOT)
    for line in (r.stdout.strip().splitlines() or ["(no output)"]):
        print("  " + line)
    if not os.path.exists(sta):
        sys.exit("failed to create save state:\n" + r.stdout + r.stderr)
    return sta


def capture(code, kind, workdir):
    log = os.path.join(ROOT, LOG_DIR, f"code_{code:02X}.log")
    wav = os.path.join(workdir, f"code_{code:02X}.wav")
    env = dict(os.environ)
    env.update(CODE=str(code), LOG_PATH=log,
               MAX_FRAMES=str(WINDOW[kind]["max_frames"]),
               QUIET_FRAMES=str(WINDOW[kind]["quiet_frames"]))
    cfg = os.path.join(workdir, "cfg"); os.makedirs(cfg, exist_ok=True)
    nv = os.path.join(workdir, "nvram"); os.makedirs(nv, exist_ok=True)
    r = sh([MAME, A.MAME_SET, "-state", STATE_NAME,
            "-sound", "none", "-nothrottle", "-skip_gameinfo",
            "-seconds_to_run", str(SECONDS_CAP),
            "-autoboot_script", "scripts/arcade_capture.lua",
            "-autoboot_delay", "0", "-wavwrite", wav,
            "-cfg_directory", cfg, "-nvram_directory", nv], cwd=ROOT, env=env)
    if not (os.path.exists(log) and os.path.exists(wav)):
        raise RuntimeError(f"code ${code:02X}: no output\n{r.stdout}{r.stderr}")
    return log, wav


def process_one(code, kind, extra, workdir):
    log, wav_raw = capture(code, kind, workdir)
    meta, events = V.parse_log(log)
    an = V.analyse(events)
    # OKI codes always run to the frame cap by design (see WINDOW), so the cap
    # says nothing about looping for them; they are one-shot samples.
    looping = (kind == "fm" and meta.get("reason") == "max_frames")
    role, prefix = classify(kind, an)
    subrole = ("looping BGM" if looping else "finite cue") if role == "music" \
        else ("sampled" if kind == "oki" else "FM")
    fname = f"{prefix}_{code:02X}"

    total = (events[-1][0] + W.TAIL_MARGIN_S) if events else 0.0
    uses_oki = an["oki_writes"] > 0
    vgm_bytes = V.build_vgm(events, total_seconds=total, include_oki_rom=uses_oki)
    vgm_path = os.path.join(ROOT, OUT_VGM, fname + ".vgm")
    with open(vgm_path, "wb") as f:
        f.write(vgm_bytes)
    verr, vinfo = V.validate_vgm(vgm_bytes)

    wav_path = os.path.join(ROOT, OUT_WAV, fname + ".wav")
    st = W.process(wav_raw, wav_path)
    os.remove(wav_raw)
    # A sound that renders to nothing gets a row but no file: a zero-sample WAV
    # is a trap for players and downstream tools. (Arcade code $80 selects the
    # null OKI phrase-table entry, so it is silent by design.)
    if st["silent"]:
        os.remove(wav_path)
    digest = ""
    if not st["silent"]:
        with open(wav_path, "rb") as f:
            digest = hashlib.sha1(f.read()).hexdigest()

    row = dict(
        sound_code=f"${code:02X}",
        sound_code_dec=code,
        chip_group=kind,
        classification=role,
        role_detail=subrole,
        synthesis=("OKIM6295 ADPCM sample" if kind == "oki"
                   else "YM2151 FM sequence"),
        wav=("" if st["silent"] else os.path.relpath(wav_path, ROOT)),
        vgm=os.path.relpath(vgm_path, ROOT),
        duration_s=round(st["duration"], 4),
        sample_rate=st["sample_rate"],
        channels=st["channels"],
        silent=st["silent"],
        peak=st["peak"],
        rms=round(st["rms"], 2) if st["rms"] else 0.0,
        looping=looping,
        capture_reason=meta.get("reason", ""),
        capture_frames=int(meta.get("frames", 0)),
        ym2151_channels=" ".join(str(c + 1) for c in an["ym_channels"]),
        ym2151_writes=an["ym_music_writes"],
        oki_used=uses_oki,
        oki_phrases=" ".join(str(p) for p in an["oki_phrases"]),
        oki_voices=" ".join(str(v) for v in an["oki_voices"]),
        oki_writes=an["oki_writes"],
        seq_ptr=extra.get("seq_ptr", ""),
        seq_bank=extra.get("seq_bank", ""),
        aliases=extra.get("aliases", ""),
        vgm_valid=(not verr),
        vgm_errors="; ".join(verr) if verr else "",
        wav_sha1=digest,
        notes=extra.get("notes", ""),
    )
    return row


# YM2151 channels 7 and 8 (0-indexed 6 and 7) are the driver's sound-effect
# channels; music never touches them and FM effects never touch 1-6.
SFX_YM_CHANNELS = {6, 7}


def classify(kind, an):
    """-> (classification, filename prefix)"""
    if kind == "oki":
        return "SFX", "PCM"
    if set(an["ym_channels"]) & SFX_YM_CHANNELS:
        return "SFX", "SFX"
    return "music", "MUS"


def plan(only=None):
    d = A.z80_rom()
    fm, n1, n2 = A.fm_sequence_table(d)
    by_ptr = {}
    for code, ptr, bank in fm:
        by_ptr.setdefault(ptr, []).append(code)

    items = []
    for ptr, codes in sorted(by_ptr.items(), key=lambda kv: kv[1][0]):
        code = codes[0]
        bank = next(b for c, p, b in fm if c == code)
        extra = dict(seq_ptr=f"${ptr:04X}",
                     seq_bank=("fixed" if bank is None else f"bank{bank}"))
        if len(codes) > 1:
            extra["aliases"] = " ".join(f"${c:02X}" for c in codes[1:])
            extra["notes"] = (f"{len(codes)-1} further code(s) share this "
                              f"sequence pointer (unused table filler)")
        items.append((code, "fm", extra))

    ok, n = A.oki_code_table(d)
    ph = {p["index"]: p for p in A.oki_phrases(A.oki_rom())}
    for code, b, stop in ok:
        idx = code - 0x80
        p = ph[idx]
        extra = dict(seq_ptr=f"phrase {idx}",
                     notes=f"OKI phrase {idx}, play byte ${b:02X}")
        if p["empty"]:
            extra["notes"] = (f"OKI phrase {idx} is a null table entry "
                              f"(start=end=0), so the chip never starts a "
                              f"voice: silent by design, no WAV emitted")
        else:
            extra["notes"] += (f", ROM ${p['start']:06X}-${p['end']:06X}, "
                               f"{p['length']} ADPCM bytes")
        items.append((code, "oki", extra))

    if only:
        want = set(only)
        items = [i for i in items if i[0] in want]
    return items


FIELDS = ["sound_code", "sound_code_dec", "chip_group", "classification",
          "role_detail", "synthesis", "wav", "vgm", "duration_s", "sample_rate", "channels",
          "silent", "peak", "rms", "looping", "capture_reason",
          "capture_frames", "ym2151_channels", "ym2151_writes", "oki_used",
          "oki_phrases", "oki_voices", "oki_writes", "seq_ptr", "seq_bank",
          "aliases", "vgm_valid", "vgm_errors", "wav_sha1", "notes"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codes", help="comma-separated hex codes, e.g. 00,31,81")
    ap.add_argument("--jobs", type=int, default=5)
    ap.add_argument("--rebuild-state", action="store_true")
    ap.add_argument("--only", choices=["fm", "oki"])
    a = ap.parse_args()

    for dd in (LOG_DIR, OUT_WAV, OUT_VGM, OUT):
        os.makedirs(os.path.join(ROOT, dd), exist_ok=True)
    A.verify_roms()
    build_state(a.rebuild_state)

    only = [int(x, 16) for x in a.codes.split(",")] if a.codes else None
    items = plan(only)
    if a.only:
        items = [i for i in items if i[1] == a.only]
    print(f"capturing {len(items)} sound codes with {a.jobs} parallel MAME "
          f"processes")

    rows, errors = [], []
    with tempfile.TemporaryDirectory() as td:
        def job(it):
            code, kind, extra = it
            wd = os.path.join(td, f"w{code:02X}")
            os.makedirs(wd, exist_ok=True)
            return process_one(code, kind, extra, wd)
        with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
            futs = {ex.submit(job, it): it for it in items}
            done = 0
            for fut in cf.as_completed(futs):
                code, kind = futs[fut][0], futs[fut][1]
                done += 1
                try:
                    row = fut.result()
                    rows.append(row)
                    print(f"  [{done:3d}/{len(items)}] ${code:02X} {kind:3s} "
                          f"{row['classification']:5s} {row['role_detail']:11s} "
                          f"{row['duration_s']:7.3f}s "
                          f"peak={row['peak']:6d} "
                          f"fm={row['ym2151_channels'] or '-':15s} "
                          f"oki={row['oki_phrases'] or '-'}")
                except Exception as e:
                    errors.append(f"${code:02X}: {e}")
                    print(f"  [{done:3d}/{len(items)}] ${code:02X} FAILED: {e}")

    rows.sort(key=lambda r: r["sound_code_dec"])
    by_hash = {}
    for r in rows:
        if not r["silent"]:
            by_hash.setdefault(r["wav_sha1"], []).append(r["sound_code"])
    for r in rows:
        if r["looping"] and r["classification"] == "music":
            note = (f"looping BGM: captured {r['duration_s']:.0f} s from the "
                    f"start (hit the {WINDOW['fm']['max_frames']}-frame cap)")
            r["notes"] = (r["notes"] + "; " + note) if r["notes"] else note
        grp = by_hash.get(r["wav_sha1"], [])
        if len(grp) > 1:
            note = ("identical rendered output to "
                    + ", ".join(g for g in grp if g != r["sound_code"]))
            r["notes"] = (r["notes"] + "; " + note) if r["notes"] else note

    with open(os.path.join(ROOT, OUT, "sounds.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader(); w.writerows(rows)
    d = A.z80_rom()
    fm, n1, n2 = A.fm_sequence_table(d)
    with open(os.path.join(ROOT, OUT, "sounds.json"), "w") as f:
        json.dump(dict(
            mame_set=A.MAME_SET,
            hardware=dict(main="68000 @ 10 MHz", audio="Z80 @ 3.579545 MHz",
                          fm="YM2151 @ 3.579545 MHz",
                          pcm="OKIM6295 @ 1 MHz, pin7 high -> 7575.76 Hz",
                          output="mono"),
            render_method=RENDER.format(ver=mame_version(), set=A.MAME_SET),
            command_space=dict(
                latch=f"${A.SND_CMD_ADDR:06X}", idle=f"${A.IDLE_CMD:02X}",
                fm_codes=f"$00-${len(fm)-1:02X} ({len(fm)}, "
                         f"{n1} in the fixed table + {n2} in bank 1)",
                oki_codes="$80-$9C (29; $9D-$EF alias them modulo 29)",
                commands="$F0-$F9 driver commands, $FA-$FF = RET",
                stop_all=f"${A.CMD_STOP_ALL:02X}"),
            sounds=rows), f, indent=2)

    print(f"\nwrote {OUT}/sounds.csv and {OUT}/sounds.json ({len(rows)} sounds)")
    sil = [r["sound_code"] for r in rows if r["silent"]]
    bad = [r["sound_code"] for r in rows if not r["vgm_valid"]]
    print(f"  music:        {sum(1 for r in rows if r['classification']=='music')}"
          f" ({sum(1 for r in rows if r['classification']=='music' and r['looping'])}"
          f" looping BGM, "
          f"{sum(1 for r in rows if r['classification']=='music' and not r['looping'])}"
          f" finite cues)")
    print(f"  FM effects:   {sum(1 for r in rows if r['classification']=='SFX' and r['chip_group']=='fm')}")
    print(f"  PCM effects:  {sum(1 for r in rows if r['chip_group']=='oki')}")
    print(f"  silent:          {len(sil)} {sil}")
    print(f"  malformed VGM:   {len(bad)} {bad}")
    if errors:
        print("\nCAPTURE ERRORS:")
        for e in errors:
            print("  " + e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
