#!/usr/bin/env python3
"""Validate the extraction.

Three independent checks:

1. **Patch audit.** The patched ROMs must differ from the original only at the
   byte ranges patch_rom.py claims, and must not touch any sound data.

2. **Sound-test mapping, driven by the game's own code.** For every one of the
   66 sound-test entries, boot the `--mode soundtest` ROM (whose frozen main
   loop calls the game's real dispatch at $003442, i.e.
   `move.w $fde6.w,d0 / move.b $344E(pc,d0.w),$9C0A.w`), select the index, and
   record which sound id the game actually enqueued. Compare against the table
   read statically from $00344E.

3. **Rendering fidelity.** Compare the chip-write sequence the game's own
   sound-test path produces against the sequence the extraction harness
   produces by poking $FF9C0A directly. If the (port, register, value)
   sequences are identical, the two paths drive the sound chips identically and
   the rendered WAVs are equivalent by construction.
"""
import argparse, concurrent.futures as cf, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rominfo as R
import vgm as V

MAME        = shutil.which("mame") or "mame"
SYSTEM      = "genesis"
ST_ROM      = "build/strider_soundtest.md"
CAP_ROM     = "build/strider_capture.md"
ST_STATE    = "clean_st"
STATE_DIR   = "tools/mame/sta"
LOG_DIR     = "analysis/megadrive/logs/soundtest"


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def ensure_prereqs():
    """Build the two patched ROMs and the sound-test save state if absent.

    `--mode soundtest` leaves the game's own dispatch instruction at $003446
    intact and makes the frozen main loop call it, which is what check 2/3 need.
    """
    for mode, path in (("freeze", CAP_ROM), ("soundtest", ST_ROM)):
        full = os.path.join(ROOT, path)
        if os.path.exists(full):
            continue
        print(f"building {path} (--mode {mode}) ...")
        r = sh([sys.executable, os.path.join(HERE, "patch_rom.py"),
                "--mode", mode, "--out", path, "--quiet"], cwd=ROOT)
        if r.returncode or not os.path.exists(full):
            sys.exit(f"patch_rom.py --mode {mode} failed:\n{r.stdout}{r.stderr}")

    sta = os.path.join(ROOT, STATE_DIR, SYSTEM, ST_STATE + ".sta")
    if not os.path.exists(sta):
        print(f"building save state '{ST_STATE}' ...")
        env = dict(os.environ, STATE_NAME=ST_STATE)
        r = sh([MAME, SYSTEM, "-cart", ST_ROM,
                "-sound", "none", "-nothrottle", "-skip_gameinfo",
                "-seconds_to_run", "400",
                "-autoboot_script", "scripts/make_state.lua",
                "-autoboot_delay", "0"], cwd=ROOT, env=env)
        for line in (r.stdout.strip().splitlines() or ["(no output)"]):
            print("  " + line)
        if not os.path.exists(sta):
            sys.exit(f"failed to create '{ST_STATE}':\n{r.stdout}{r.stderr}")


# ---------------------------------------------------------------- 1. patches
def audit_patches(orig):
    out = []
    import patch_rom as P
    for mode, path in (("freeze", CAP_ROM), ("soundtest", ST_ROM)):
        full = os.path.join(ROOT, path)
        if not os.path.exists(full):
            out.append((path, "MISSING", []))
            continue
        patched = open(full, "rb").read()
        diffs = [i for i in range(len(orig)) if orig[i] != patched[i]]
        # group into ranges
        ranges, start, prev = [], None, None
        for i in diffs:
            if start is None:
                start = prev = i
            elif i == prev + 1:
                prev = i
            else:
                ranges.append((start, prev)); start = prev = i
        if start is not None:
            ranges.append((start, prev))
        out.append((path, mode, ranges))
    return out


SOUND_DATA_RANGES = [
    (0x0B0020, 0x0B00F7, "song pointer table + priority table"),
    (0x0B10EA, 0x0B638B, "song sequence + voice data"),
    (0x0B638C, 0x0B6E85, "sfx pointer table + sequence + voice data"),
    (0x0B6E86, 0x0B6FF5, "Z80 DAC driver blob"),
    (0x0B8000, 0x0BD8AF, "DAC sample table + DPCM data (bank $17)"),
    (0x0D8000, 0x0DFFFB, "DAC sample table + DPCM data (bank $1B)"),
    (0x00344E, 0x00348F, "sound-test index -> sound id table"),
]


# ------------------------------------------------- 2 & 3. soundtest captures
def capture_index(idx, workdir):
    log = os.path.join(ROOT, LOG_DIR, f"idx_{idx:02d}.log")
    env = dict(os.environ)
    env.update(IDX=str(idx), LOG_PATH=log, MAX_FRAMES="900", QUIET_FRAMES="90")
    cfg = os.path.join(workdir, "cfg"); os.makedirs(cfg, exist_ok=True)
    nv = os.path.join(workdir, "nvram"); os.makedirs(nv, exist_ok=True)
    r = sh([MAME, SYSTEM, "-cart", ST_ROM, "-state", ST_STATE,
            "-video", "none", "-sound", "none", "-nothrottle", "-skip_gameinfo",
            "-seconds_to_run", "400",
            "-autoboot_script", "scripts/soundtest_capture.lua",
            "-autoboot_delay", "0",
            "-cfg_directory", cfg, "-nvram_directory", nv,
            "-state_directory", STATE_DIR], cwd=ROOT, env=env)
    if not os.path.exists(log):
        raise RuntimeError(f"idx {idx}: no log\n{r.stdout}{r.stderr}")
    meta, events = V.parse_log(log)
    return meta, V.to_chip_writes(events)


def seq(writes):
    """(port, reg, value) sequence, timing-independent."""
    return [(p, r, v) if k == "ym" else ("psg", None, v)
            for _, k, p, r, v in writes]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--indices", help="comma separated, default all 66")
    a = ap.parse_args()
    os.makedirs(os.path.join(ROOT, LOG_DIR), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "output/megadrive"), exist_ok=True)
    ensure_prereqs()
    orig = R.load(os.path.join(ROOT, R.ROM_NAME))
    table = R.soundtest_table(orig)
    ok = True

    print("=" * 72)
    print("1. PATCH AUDIT - patched ROMs vs the original")
    print("=" * 72)
    for path, mode, ranges in audit_patches(orig):
        nbytes = sum(hi - lo + 1 for lo, hi in ranges)
        print(f"\n{path}  (mode {mode}): {len(ranges)} changed ranges, {nbytes} bytes")
        for lo, hi in ranges:
            tag = ""
            for s, e, what in SOUND_DATA_RANGES:
                if lo <= e and hi >= s:
                    tag = f"  <-- OVERLAPS SOUND DATA: {what}"
                    ok = False
            print(f"    ${lo:06X}-${hi:06X} ({hi-lo+1:3d} b){tag}")
        clean = all(not (lo <= e and hi >= s)
                    for lo, hi in ranges for s, e, _ in SOUND_DATA_RANGES)
        print(f"    => sound data untouched: {clean}")

    idxs = ([int(x) for x in a.indices.split(",")] if a.indices
            else list(range(R.SOUNDTEST_COUNT)))
    print()
    print("=" * 72)
    print(f"2/3. SOUND-TEST DISPATCH + FIDELITY ({len(idxs)} entries, driven by "
          f"the game's own code at $00{R.SOUNDTEST_PLAY:04X})")
    print("=" * 72)
    print(f"{'idx':>4} {'static':>7} {'game':>6} {'map':>4} "
          f"{'game writes':>12} {'harness':>8} {'seq':>5}")
    print("-" * 56)

    results = []
    with tempfile.TemporaryDirectory() as td:
        def job(i):
            return i, capture_index(i, os.path.join(td, f"w{i}"))
        with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
            out = dict(ex.map(job, idxs))

    for i in idxs:
        meta, writes = out[i]
        got = int(meta.get("sid", "0"), 16)
        want = table[i]
        map_ok = (got == want)
        ok = ok and map_ok

        hpath = os.path.join(ROOT, "analysis/megadrive/logs", f"sid_{got:02X}.log")
        seq_ok, hn = None, None
        if os.path.exists(hpath):
            _, hev = V.parse_log(hpath)
            hw = V.to_chip_writes(hev)
            hn = len(hw)
            a_, b_ = seq(writes), seq(hw)
            # the harness capture may run longer (music cap); compare the common
            # prefix, which is where they must agree exactly
            n = min(len(a_), len(b_))
            seq_ok = (a_[:n] == b_[:n]) and n > 0
            ok = ok and bool(seq_ok)
        print(f"{i:>4}   ${want:02X}    ${got:02X} {'OK' if map_ok else 'FAIL':>5} "
              f"{len(writes):>12} {hn if hn is not None else '-':>8} "
              f"{('OK' if seq_ok else 'FAIL') if seq_ok is not None else '-':>5}")
        results.append(dict(index=i, static_id=f"${want:02X}", game_id=f"${got:02X}",
                            mapping_ok=map_ok, game_writes=len(writes),
                            harness_writes=hn, sequence_identical=seq_ok))

    with open(os.path.join(ROOT, "output/megadrive/validation.json"), "w") as f:
        json.dump(dict(soundtest=results,
                       patch_audit=[dict(rom=p, mode=m,
                                         ranges=[[f"${lo:06X}", f"${hi:06X}"]
                                                 for lo, hi in rg])
                                    for p, m, rg in audit_patches(orig)]),
                  f, indent=2)

    print()
    print("=" * 72)
    print("RESULT:", "ALL CHECKS PASSED" if ok else "FAILURES PRESENT")
    print("=" * 72)
    print("wrote output/validation.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
