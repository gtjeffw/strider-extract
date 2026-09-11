#!/usr/bin/env python3
"""Static analysis report for Strider's sound system. Writes analysis/tables/.

Everything printed here is derived from the ROM by following the pointers the
sound driver itself uses; nothing is hardcoded except the bank base $0B0000
(which the driver hardcodes too) and the addresses documented in NOTES.md.
"""
import argparse, json, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rominfo as R

OUT = "analysis/megadrive/tables"

# Eight signed bytes the song start routine accumulates to derive each track's
# channel byte (see docs/megadrive.md section 8.2).
SONG_CHANNEL_DELTAS = 0x0B08BE


# Song and SFX headers differ; both are confirmed from the driver's own start
# routines ($0B07A0 for songs, $0B061E for SFX). See NOTES.md section 8.
SONG_CHANNEL_DELTAS = 0x0B08BE      # 8 signed bytes, cumulative -> channel byte


def sfx_header(d, ptr):
    """SFX header: +0 voice rel, +2 ?, +3 track count, +4.. 6-byte entries."""
    tracks = []
    ntrk = d[ptr + 3]
    for i in range(ntrk):
        e = ptr + 4 + 6 * i
        ch = d[e + 1]
        rel = struct.unpack_from(">h", d, e + 2)[0]
        tracks.append(dict(entry=f"${e:06X}", flags=d[e], channel_byte=ch,
                           kind="PSG" if ch & 0x80 else "FM",
                           data=f"${ptr + rel:06X}", extra=R.be16(d, e + 4)))
    return dict(ptr=ptr, voice_table=ptr + R.be16(d, ptr), byte2=d[ptr + 2],
                track_count=ntrk, tracks=tracks)


def song_header(d, ptr):
    """Song header: +0 voice rel, +2 track count, +4 initial flag word
    (+5 is the tempo byte copied to $FF9C01/02), +6.. 4-byte entries.

    Track channel bytes are not stored per entry: the driver accumulates the
    signed deltas at $0B08BE instead, one per track, starting from the high
    word of $80000000 | header[+4].
    """
    ntrk = d[ptr + 2]
    deltas = d[SONG_CHANNEL_DELTAS:SONG_CHANNEL_DELTAS + 8]
    tracks, ch = [], 0
    for i in range(ntrk):
        e = ptr + 6 + 4 * i
        dl = deltas[i] if i < len(deltas) else 0
        ch = (ch + (dl - 256 if dl > 127 else dl)) & 0xFF
        rel = struct.unpack_from(">h", d, e)[0]
        tracks.append(dict(entry=f"${e:06X}", channel_byte=ch,
                           kind="PSG" if ch & 0x80 else "FM",
                           data=f"${ptr + rel:06X}", extra=R.be16(d, e + 2)))
    return dict(ptr=ptr, voice_table=ptr + R.be16(d, ptr), track_count=ntrk,
                init_flags=R.be16(d, ptr + 4), tempo=d[ptr + 5], tracks=tracks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default=os.path.join(ROOT, R.ROM_NAME))
    a = ap.parse_args()
    d = R.load(a.rom)
    R.verify_tables(d)
    os.makedirs(os.path.join(ROOT, OUT), exist_ok=True)
    rep = {}

    def w(name, obj):
        with open(os.path.join(ROOT, OUT, name), "w") as f:
            json.dump(obj, f, indent=2)

    print("=== ROM header ===")
    for lbl, off, n in (("console", 0x100, 16), ("copyright", 0x110, 16),
                        ("domestic", 0x120, 48), ("overseas", 0x150, 48),
                        ("serial", 0x180, 14), ("io", 0x190, 16),
                        ("region", 0x1F0, 3)):
        print(f"  {lbl:10s} {d[off:off+n].decode('ascii', 'replace').strip()!r}")
    print(f"  checksum   ${R.be16(d, 0x18E):04X}")
    print(f"  rom range  ${R.be32(d, 0x1A0):06X}-${R.be32(d, 0x1A4):06X}")

    print("\n=== sound bank descriptor @ $%06X ===" % R.BANK_BASE)
    hdr = R.bank_header(d)
    labels = {0x00: "priority table", 0x04: "end of sfx ptr table",
              0x08: "song pointer table", 0x0C: "sfx pointer table",
              0x10: "priority table (dup)", 0x14: "song ptr table (dup)",
              0x18: "sfx id base", 0x1C: "driver entry"}
    for k in sorted(hdr):
        print(f"  +${k:02X} = ${hdr[k]:06X}   {labels[k]}")
    rep["bank_header"] = {f"+0x{k:02X}": f"${v:06X}" for k, v in hdr.items()}

    print("\n=== songs: %d, ids $%02X..$%02X ===" % (
        R.SONG_COUNT, R.SONG_ID_FIRST, R.SONG_ID_FIRST + R.SONG_COUNT - 1))
    songs = R.song_table(d)
    srows = []
    for i, p in enumerate(songs):
        sid = R.SONG_ID_FIRST + i
        h = song_header(d, p)
        end = songs[i + 1] if i + 1 < len(songs) else 0x0B638C
        srows.append(dict(sound_id=f"${sid:02X}", ptr=f"${p:06X}", size=end - p,
                          voice_table=f"${h['voice_table']:06X}",
                          track_count=h["track_count"], tempo=h["tempo"],
                          priority=f"${R.priority(d, sid):02X}",
                          tracks=h["tracks"]))
        print(f"  ${sid:02X} @ ${p:06X} size={end-p:5d} tracks={h['track_count']:2d} "
              f"voices=${h['voice_table']:06X} tempo=${h['tempo']:02X} "
              f"prio=${R.priority(d, sid):02X}  ch="
              + ",".join(f"${t['channel_byte']:02X}" for t in h["tracks"]))
    rep["songs"] = srows
    w("songs.json", srows)

    print("\n=== sfx: %d, ids $%02X..$%02X ===" % (
        R.SFX_COUNT, R.SFX_ID_FIRST, R.SFX_ID_FIRST + R.SFX_COUNT - 1))
    sfxs = R.sfx_table(d)
    frows = []
    for i, p in enumerate(sfxs):
        sid = R.SFX_ID_FIRST + i
        h = sfx_header(d, p)
        end = sfxs[i + 1] if i + 1 < len(sfxs) else R.Z80_BLOB_ROM
        frows.append(dict(sound_id=f"${sid:02X}", ptr=f"${p:06X}",
                          size=end - p,
                          voice_table=f"${h['voice_table']:06X}",
                          track_count=h["track_count"],
                          priority=f"${R.priority(d, sid):02X}",
                          tracks=h["tracks"]))
        print(f"  ${sid:02X} @ ${p:06X} size={end-p:4d} tracks={h['track_count']} "
              f"voices=${h['voice_table']:06X} prio=${R.priority(d, sid):02X}  ch="
              + ",".join(f"${t['channel_byte']:02X}" for t in h["tracks"]))
    rep["sfx"] = frows
    w("sfx.json", frows)

    print("\n=== song track channel-delta table @ $%06X ===" % SONG_CHANNEL_DELTAS)
    deltas = d[SONG_CHANNEL_DELTAS:SONG_CHANNEL_DELTAS + 8]
    sig = [v - 256 if v > 127 else v for v in deltas]
    print("  signed deltas : " + " ".join(f"{v:+d}" for v in sig))
    acc, chans = 0, []
    for v in sig:
        acc = (acc + v) & 0xFF
        chans.append(acc)
    print("  accumulated   : " + " ".join(f"${c:02X}" for c in chans))
    print("  -> FM channels: " + " ".join(
        str(c + 1) if c < 3 else (str(c) if c in (4, 5, 6) else f"?{c}")
        for c in chans))
    rep["song_channel_deltas"] = dict(
        at=f"${SONG_CHANNEL_DELTAS:06X}", signed=sig,
        accumulated=[f"${c:02X}" for c in chans])

    print("\n=== DPCM delta table @ $%06X (inside the Z80 blob) ===" % R.DPCM_DELTA_ROM)
    dd = R.dpcm_deltas(d)
    sd = [v - 256 if v > 127 else v for v in dd]
    print("  nibble : " + " ".join(f"{i:5X}" for i in range(16)))
    print("  delta  : " + " ".join(f"{v:+5d}" for v in sd))
    rep["dpcm_deltas"] = sd

    print("\n=== worked example: SFX $A0 FM voice (standard SMPS 25-byte layout) ===")
    a0 = R.sfx_table(d)[0]
    v0 = a0 + R.be16(d, a0)
    print(f"  header ${a0:06X}, voice table ${v0:06X}, 25 bytes")
    for lbl, regs, lo, hi in (
            ("algorithm/feedback", "$B0+ch", 0, 0),
            ("DT/MUL            ", "$30 $34 $38 $3C +ch", 1, 4),
            ("RS/AR             ", "$50 $54 $58 $5C +ch", 5, 8),
            ("AM/D1R            ", "$60 $64 $68 $6C +ch", 9, 12),
            ("D2R               ", "$70 $74 $78 $7C +ch", 13, 16),
            ("D1L/RR            ", "$80 $84 $88 $8C +ch", 17, 20),
            ("TL                ", "$40 $44 $48 $4C +ch", 21, 24)):
        vals = " ".join(f"${d[v0+i]:02X}" for i in range(lo, hi + 1))
        print(f"  [{lo:2d}..{hi:2d}] {lbl} -> {regs:22s} {vals}")

    print("\n=== DAC / DPCM samples ===")
    drows = R.dac_entries(d)
    for e in drows:
        print(f"  ${e['sound_id']:02X} bank {e['bank']} (${e['bank_base']:06X}) "
              f"rom ${e['rom_start']:06X} len={e['length']:6d} "
              f"samples={e['samples']:6d} rate=${e['rate']:02X} "
              f"{'uninterruptible' if e['uninterruptible'] else ''}")
    rep["dac"] = [{k: (f"${v:06X}" if k in ("bank_base", "rom_start") else v)
                   for k, v in e.items()} for e in drows]
    w("dac.json", rep["dac"])

    print("\n=== sound test (menu index -> sound id) ===")
    t = R.soundtest_table(d)
    for i in range(0, len(t), 11):
        print("  " + "  ".join(f"{j:2d}:${t[j]:02X}"
                               for j in range(i, min(i + 11, len(t)))))
    counts = dict(music=sum(1 for x in t if x < 0xA0),
                  sfx=sum(1 for x in t if 0xA0 <= x < 0xD0),
                  dac=sum(1 for x in t if x >= 0xD0))
    print(f"  total {len(t)} entries: {counts}")
    missing_sfx = [x for x in range(0xA0, 0xD0) if x not in t]
    missing_dac = [x for x in range(0xD0, 0xD8) if x not in t]
    print(f"  SFX not reachable from the menu ({len(missing_sfx)}): "
          + " ".join(f"${x:02X}" for x in missing_sfx))
    print(f"  DAC not reachable from the menu ({len(missing_dac)}): "
          + " ".join(f"${x:02X}" for x in missing_dac))
    rep["soundtest"] = dict(
        entries=len(t), counts=counts,
        map=[{"index": i, "sound_id": f"${x:02X}",
              "class": ("music" if x < 0xA0 else "sfx" if x < 0xD0 else "dac")}
             for i, x in enumerate(t)],
        unreachable_sfx=[f"${x:02X}" for x in missing_sfx],
        unreachable_dac=[f"${x:02X}" for x in missing_dac])
    w("soundtest.json", rep["soundtest"])

    print("\n=== game-side 'play sound' stub table @ $%06X ===" % R.STUB_TABLE)
    stubs, end = R.parse_stubs(d)
    print(f"  {len(stubs)} stubs, $%06X..$%06X" % (R.STUB_TABLE, end - 1))
    kinds = {}
    for s in stubs:
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    print(f"  kinds: {kinds}")
    rep["stubs"] = [dict(addr=f"${s['addr']:06X}", sound_id=f"${s['sid']:02X}",
                         kind=s["kind"]) for s in stubs]
    w("stubs.json", rep["stubs"])

    w("summary.json", rep)
    print(f"\nwrote {OUT}/{{songs,sfx,dac,soundtest,stubs,summary}}.json")


if __name__ == "__main__":
    main()
