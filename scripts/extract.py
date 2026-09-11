#!/usr/bin/env python3
"""End-to-end Strider sound extraction.

Pipeline, per sound id:
    patched ROM + frozen save state
      -> MAME (headless) pokes the id into the driver queue at $FF9C0A
      -> chip-write log (YM2612 + SN76489)  -> output/vgm/*.vgm  + metadata
      -> MAME -wavwrite 48 kHz stereo       -> output/wav/*.wav   (DC-removed,
                                               conservatively trimmed)

Run `python scripts/extract.py --help` for options. With no options it does
everything: builds the patched ROM, builds the save state, captures all 87
playable sound ids, and writes output/sounds.{csv,json}.
"""
import argparse, concurrent.futures as cf, csv, hashlib, json, os, shutil
import subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rominfo as R
import vgm as V
import wavutil as W

MAME          = shutil.which("mame") or "mame"
MAME_SYSTEM   = "genesis"          # USA/NTSC: 60 Hz, one driver tick per frame
PATCHED_ROM   = "build/strider_capture.md"
STATE_NAME    = "clean"
STATE_DIR     = "tools/mame/sta"
CFG_DIR       = "tools/mame/cfg"
NVRAM_DIR     = "tools/mame/nvram"
LOG_DIR       = "analysis/megadrive/logs"
OUT_WAV       = "output/megadrive/wav"
OUT_VGM       = "output/megadrive/vgm"
SECONDS_CAP   = 400               # absolute machine-time cap; Lua exits sooner

# per-class capture windows, in 60 Hz frames
WINDOW = {
    "music": dict(max_frames=3600, quiet_frames=600),   # loops: hard cap 60 s
    "sfx":   dict(max_frames=900,  quiet_frames=90),
    "dac":   dict(max_frames=900,  quiet_frames=90),
}

RENDER_METHOD = ("MAME {ver} '{sys}' headless, YM2612 + SN76489 as mixed by the "
                 "emulated Mega Drive, 48 kHz/16-bit stereo -wavwrite")


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def mame_version():
    r = sh([MAME, "-version"])
    return (r.stdout or r.stderr).strip().split()[0]


def build_rom(force=False):
    out = os.path.join(ROOT, PATCHED_ROM)
    if os.path.exists(out) and not force:
        return out
    r = sh([sys.executable, os.path.join(HERE, "patch_rom.py"),
            "--mode", "freeze", "--out", PATCHED_ROM, "--quiet"], cwd=ROOT)
    if r.returncode:
        sys.exit("patch_rom.py failed:\n" + r.stdout + r.stderr)
    print(r.stdout.strip().splitlines()[-2] if r.stdout else "patched ROM built")
    return out


def build_state(force=False):
    sta = os.path.join(ROOT, STATE_DIR, MAME_SYSTEM, STATE_NAME + ".sta")
    if os.path.exists(sta) and not force:
        return sta
    print("building frozen save state ...")
    r = sh([MAME, MAME_SYSTEM, "-cart", PATCHED_ROM,
            "-video", "none", "-sound", "none", "-nothrottle", "-skip_gameinfo",
            "-seconds_to_run", "400",
            "-autoboot_script", "scripts/make_state.lua", "-autoboot_delay", "0",
            "-cfg_directory", CFG_DIR, "-nvram_directory", NVRAM_DIR,
            "-state_directory", STATE_DIR], cwd=ROOT)
    for line in (r.stdout.strip().splitlines() or ["(no output)"]):
        print("  " + line)
    if not os.path.exists(sta):
        sys.exit("failed to create save state:\n" + r.stdout + r.stderr)
    return sta


def capture(sid, kind, workdir):
    """Run MAME once for one sound id. Returns (log_path, wav_path, stdout)."""
    name = f"{sid:02X}"
    log = os.path.join(ROOT, LOG_DIR, f"sid_{name}.log")
    wav = os.path.join(workdir, f"sid_{name}.wav")
    env = dict(os.environ)
    env.update(SID=f"0x{sid:02X}", POKE_FRAME="2", LOG_PATH=log,
               MAX_FRAMES=str(WINDOW[kind]["max_frames"]),
               QUIET_FRAMES=str(WINDOW[kind]["quiet_frames"]))
    cfg = os.path.join(workdir, "cfg")
    nv = os.path.join(workdir, "nvram")
    os.makedirs(cfg, exist_ok=True)
    os.makedirs(nv, exist_ok=True)
    r = sh([MAME, MAME_SYSTEM, "-cart", PATCHED_ROM, "-state", STATE_NAME,
            "-video", "none", "-sound", "none", "-nothrottle", "-skip_gameinfo",
            "-seconds_to_run", str(SECONDS_CAP),
            "-autoboot_script", "scripts/capture.lua", "-autoboot_delay", "0",
            "-wavwrite", wav,
            "-cfg_directory", cfg, "-nvram_directory", nv,
            "-state_directory", STATE_DIR], cwd=ROOT, env=env)
    if not os.path.exists(log):
        raise RuntimeError(f"sid ${sid:02X}: no log produced\n{r.stdout}{r.stderr}")
    if not os.path.exists(wav):
        raise RuntimeError(f"sid ${sid:02X}: no wav produced\n{r.stdout}{r.stderr}")
    return log, wav, r.stdout


def process_one(sid, kind, idx, soundtest_index, label, rom, workdir):
    log, wav_raw, out = capture(sid, kind, workdir)
    meta, events = V.parse_log(log)
    writes = V.to_chip_writes(events)
    an = V.analyse(writes)

    # The game's own on-screen label, when the sound test exposes this id:
    # real titles for the music, S.E.NN for the effects. Ids the menu cannot
    # reach have no label and keep the bare name.
    suffix = ("_" + R.label_slug(label)) if label else ""
    if kind == "music":
        fname = f"MUS_{idx:02d}_{sid:02X}{suffix}"
    else:
        fname = f"SFX_{idx:03d}_{sid:02X}{suffix}"

    total = (events[-1][0] + W.TAIL_MARGIN_S) if events else 0.0
    vgm_bytes = V.build_vgm(writes, total_seconds=total)
    vgm_path = os.path.join(ROOT, OUT_VGM, fname + ".vgm")
    with open(vgm_path, "wb") as f:
        f.write(vgm_bytes)

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

    dac_used = an["dac_writes"] > 0
    row = dict(
        soundtest_index="" if soundtest_index is None else soundtest_index,
        soundtest_label=label or "",
        sound_id=f"${sid:02X}",
        sound_id_dec=sid,
        classification="music" if kind == "music" else "SFX",
        synthesis=("FM sequence" if kind in ("music", "sfx") else "DPCM sample"),
        wav=("" if st["silent"] else os.path.relpath(wav_path, ROOT)),
        vgm=os.path.relpath(vgm_path, ROOT),
        duration_s=round(st["duration"], 4),
        sample_rate=st["sample_rate"],
        channels=st["channels"],
        silent=st["silent"],
        peak=st["peak"],
        rms=round(st["rms"], 2) if st["rms"] else 0.0,
        truncated=bool(st.get("truncated", False)),
        hit_window=(meta.get("reason") == "max_frames"),
        capture_reason=meta.get("reason", ""),
        capture_frames=int(meta.get("frames", 0)),
        fm_channels_used=" ".join(str(c) for c in an["fm_keyon"]),
        fm_channels_touched=" ".join(str(c) for c in an["fm_touched"]),
        psg_channels_used=" ".join(("noise" if c == 3 else str(c + 1))
                                   for c in an["psg_audible"]),
        dac_used=dac_used,
        dac_write_count=an["dac_writes"],
        chip_writes=len(writes),
        priority=f"${R.priority(rom, sid):02X}" if sid < 0xD0 else "",
        data_ptr="",
        stray_writes=int(meta.get("stray", 0)),
        wav_sha1=digest,
        notes="",
    )
    return row, out.strip()


def plan(rom, only=None):
    """Build the ordered work list: (sid, kind, index, soundtest_index)."""
    st = R.soundtest_table(rom)
    labels = R.soundtest_labels(rom)
    st_index = {}
    for i, sid in enumerate(st):
        st_index.setdefault(sid, i)

    def ent(sid, kind, n):
        j = st_index.get(sid)
        return (sid, kind, n, j, labels[j] if j is not None else None)

    items = []
    for i in range(R.SONG_COUNT):
        items.append(ent(R.SONG_ID_FIRST + i, "music", i + 1))
    n = 0
    for i in range(R.SFX_COUNT):
        n += 1
        items.append(ent(R.SFX_ID_FIRST + i, "sfx", n))
    for i in range(R.DAC_COUNT):
        n += 1
        items.append(ent(R.DAC_ID_FIRST + i, "dac", n))
    if only:
        want = set(only)
        items = [it for it in items if it[0] in want]
    return items


def annotate(rows, rom):
    """Fill in data pointers and flag aliases / duplicates."""
    songs, sfxs = R.song_table(rom), R.sfx_table(rom)
    dacs = {e["sound_id"]: e for e in R.dac_entries(rom)}
    by_hash = {}
    for r in rows:
        sid = r["sound_id_dec"]
        if R.SONG_ID_FIRST <= sid <= 0x9F:
            r["data_ptr"] = f"${songs[sid - R.SONG_ID_FIRST]:06X}"
        elif R.SFX_ID_FIRST <= sid <= 0xCF:
            r["data_ptr"] = f"${sfxs[sid - R.SFX_ID_FIRST]:06X}"
        elif sid in dacs:
            e = dacs[sid]
            r["data_ptr"] = f"${e['rom_start']:06X}"
            r["notes"] = (f"DPCM {e['length']} bytes = {e['samples']} samples, "
                          f"rate byte ${e['rate']:02X}"
                          + (", uninterruptible" if e["uninterruptible"] else ""))
        if not r["silent"]:
            by_hash.setdefault(r["wav_sha1"], []).append(r["sound_id"])
    for r in rows:
        if r["hit_window"] and r["classification"] == "music":
            note = (f"looping BGM: captured {r['duration_s']:.0f} s from the "
                    f"start (hit the {WINDOW['music']['max_frames']}-frame cap)")
            r["notes"] = (r["notes"] + "; " + note) if r["notes"] else note
        grp = by_hash.get(r["wav_sha1"], [])
        if len(grp) > 1:
            others = [g for g in grp if g != r["sound_id"]]
            note = "identical rendered output to " + ", ".join(others)
            r["notes"] = (r["notes"] + "; " + note) if r["notes"] else note
    return rows


def validate(rows):
    problems = []
    for r in rows:
        if r["silent"]:
            problems.append(f"{r['sound_id']} ({r['wav']}): rendered SILENT")
        if r["hit_window"] and r["classification"] != "music":
            problems.append(f"{r['sound_id']} ({r['wav']}): SFX hit the capture "
                            f"window - may be truncated")
        if r["stray_writes"]:
            problems.append(f"{r['sound_id']}: {r['stray_writes']} stray queue "
                            f"writes from game code - capture may be contaminated")
    return problems


FIELDS = ["soundtest_index", "soundtest_label", "sound_id", "sound_id_dec",
          "classification",
          "synthesis", "wav", "vgm", "duration_s", "sample_rate", "channels",
          "silent", "peak", "rms", "truncated", "hit_window", "capture_reason",
          "capture_frames", "fm_channels_used", "fm_channels_touched",
          "psg_channels_used", "dac_used", "dac_write_count", "chip_writes",
          "priority", "data_ptr", "stray_writes", "wav_sha1", "notes"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", help="comma-separated hex sound ids, e.g. A0,A1,D6")
    ap.add_argument("--jobs", type=int, default=4, help="parallel MAME processes")
    ap.add_argument("--rebuild-rom", action="store_true")
    ap.add_argument("--rebuild-state", action="store_true")
    ap.add_argument("--skip-music", action="store_true")
    a = ap.parse_args()

    for d in (LOG_DIR, OUT_WAV, OUT_VGM, "output/megadrive", "build"):
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)

    rom = R.load(os.path.join(ROOT, R.ROM_NAME))
    R.verify(rom)
    build_rom(a.rebuild_rom)
    build_state(a.rebuild_state or a.rebuild_rom)

    only = [int(x, 16) for x in a.ids.split(",")] if a.ids else None
    items = plan(rom, only)
    if a.skip_music:
        items = [it for it in items if it[1] != "music"]
    print(f"capturing {len(items)} sounds with {a.jobs} parallel MAME processes "
          f"(this takes a few minutes)")

    rows, errors = [], []
    with tempfile.TemporaryDirectory() as td:
        def job(it):
            sid, kind, idx, sti, label = it
            wd = os.path.join(td, f"w{sid:02X}")
            os.makedirs(wd, exist_ok=True)
            return process_one(sid, kind, idx, sti, label, rom, wd)

        with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
            futs = {ex.submit(job, it): it for it in items}
            done = 0
            for fut in cf.as_completed(futs):
                sid, kind = futs[fut][0], futs[fut][1]
                done += 1
                try:
                    row, out = fut.result()
                    rows.append(row)
                    print(f"  [{done:3d}/{len(items)}] ${sid:02X} {kind:5s} "
                          f"{(row['soundtest_label'] or '-'):23s} "
                          f"{row['duration_s']:7.3f}s peak={row['peak']:5d} "
                          f"fm={row['fm_channels_used'] or '-':11s} "
                          f"psg={row['psg_channels_used'] or '-':6s} "
                          f"dac={'y' if row['dac_used'] else 'n'}")
                except Exception as e:
                    errors.append(f"${sid:02X}: {e}")
                    print(f"  [{done:3d}/{len(items)}] ${sid:02X} FAILED: {e}")

    rows.sort(key=lambda r: (r["classification"] != "music", r["sound_id_dec"]))
    annotate(rows, rom)

    with open(os.path.join(ROOT, "output/megadrive/sounds.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(ROOT, "output/megadrive/sounds.json"), "w") as f:
        json.dump(dict(
            rom=dict(name=R.ROM_NAME, sha1=R.ROM_SHA1),
            patched_rom=PATCHED_ROM,
            render_method=RENDER_METHOD.format(ver=mame_version(), sys=MAME_SYSTEM),
            soundtest=dict(code=f"${R.SOUNDTEST_CODE:06X}",
                           table=f"${R.SOUNDTEST_TABLE:06X}",
                           entries=R.SOUNDTEST_COUNT,
                           index_var=f"${R.SOUNDTEST_INDEX:06X}",
                           map=[{"index": i, "sound_id": f"${s:02X}",
                                 "label": l}
                                for i, (s, l) in enumerate(
                                    zip(R.soundtest_table(rom),
                                        R.soundtest_labels(rom)))]),
            sounds=rows), f, indent=2)

    print(f"\nwrote output/megadrive/sounds.csv and sounds.json ({len(rows)} sounds)")
    probs = validate(rows)
    if errors:
        print("\nCAPTURE ERRORS:")
        for e in errors:
            print("  " + e)
    if probs:
        print("\nVALIDATION NOTES:")
        for p in probs:
            print("  " + p)
    else:
        print("validation: no silent, truncated or contaminated captures")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
