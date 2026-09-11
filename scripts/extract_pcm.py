#!/usr/bin/env python3
"""Extract the genuine PCM/DAC content from the ROM.

Strider does not store raw PCM. The Z80 player at ROM $0B6E86 decodes a
**4-bit DPCM** stream: per source byte, high nibble first then low nibble, each
nibble indexing a 16-entry signed delta table, accumulating into an unsigned
8-bit value that starts at $80 and is written to YM2612 register $2A.

    acc = (acc + delta[nibble]) & 0xFF      ; acc starts at $80
    delta = 0 +1 +2 +4 +8 +16 +32 +64 -128 -1 -3 -4 -8 -16 -32 -64

Outputs, per sample, into output/raw-pcm/:
    *.dpcm       the raw 4-bit DPCM bytes exactly as stored in the ROM
    *_u8.raw     the decoded unsigned 8-bit stream the YM2612 DAC receives
    *.wav        the same, as a mono 8-bit WAV at the measured playback rate

The playback rate is taken from the emulator capture logs when available
(measured from the real inter-write spacing of YM register $2A), because the
Z80 delay loop timing depends on YM busy waits and bus contention that a
static cycle count cannot capture. Falls back to a cycle estimate otherwise.
"""
import argparse, json, os, struct, sys, wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rominfo as R
import vgm as V

OUT = "output/megadrive/raw-pcm"
LOG_DIR = "analysis/megadrive/logs"

# Per-sample period model, derived from the Z80 code and confirmed against the
# measured rates of all 8 samples to within 0.004%:
#
#     period_per_sample = FIXED_US[interruptible] + rate_byte * DJNZ_US
#
# DJNZ is 13 T-states at 3.579545 MHz. The two FIXED constants differ by exactly
# the cost of the "has the 68000 posted a new command?" test that the
# uninterruptible path skips - BIT 7,(HL) + JP nz = 22 T-states replaced by a
# taken JR at 5 T, once per source byte, so (22-5)/2 = 8.5 T = 2.375 us per
# sample. See docs/megadrive.md section 12.
DJNZ_US = 13.0 / R.Z80_CLOCK * 1e6
FIXED_US = {False: 55.17, True: 52.80}   # keyed by the uninterruptible flag


def decode_dpcm(data, deltas):
    """4-bit DPCM -> list of unsigned 8-bit DAC values (2 per source byte)."""
    signed = [v - 256 if v > 127 else v for v in deltas]
    acc = 0x80
    out = bytearray()
    for b in data:
        for nib in (b >> 4, b & 0x0F):
            acc = (acc + signed[nib]) & 0xFF
            out.append(acc)
    return bytes(out)


def measured_rate(sid):
    """Mean DAC write rate for this sound id, from the MAME capture log."""
    path = os.path.join(ROOT, LOG_DIR, f"sid_{sid:02X}.log")
    if not os.path.exists(path):
        return None, None
    _, events = V.parse_log(path)
    writes = V.to_chip_writes(events)
    t = [tt for tt, k, p, r, v in writes if k == "ym" and p == 0 and r == 0x2A]
    if len(t) < 2:
        return None, None
    span = t[-1] - t[0]
    return (len(t) - 1) / span, len(t)


def write_wav_u8(path, samples, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)          # 8-bit WAV is unsigned - matches the DAC
        w.setframerate(int(round(rate)))
        w.writeframes(samples)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default=os.path.join(ROOT, R.ROM_NAME))
    a = ap.parse_args()
    d = R.load(a.rom)
    deltas = R.dpcm_deltas(d)
    outdir = os.path.join(ROOT, OUT)
    os.makedirs(outdir, exist_ok=True)

    print("DPCM delta table @ $%06X: %s" % (
        R.DPCM_DELTA_ROM,
        " ".join(f"{v-256 if v>127 else v:+d}" for v in deltas)))
    print()
    hdr = (f"{'id':>4} {'bank':>4} {'rom start':>10} {'bytes':>7} {'samples':>8} "
           f"{'rate$':>5} {'meas Hz':>9} {'dur s':>7} {'uninter':>7}  file")
    print(hdr)
    print("-" * len(hdr))

    meta = []
    for e in R.dac_entries(d):
        sid = e["sound_id"]
        raw = d[e["rom_start"]:e["rom_start"] + e["length"]]
        assert len(raw) == e["length"]
        dec = decode_dpcm(raw, deltas)
        assert len(dec) == e["samples"]

        rate, nmeas = measured_rate(sid)
        source = "capture log"
        if rate is None:
            rate = 1e6 / (FIXED_US[e["uninterruptible"]] + e["rate"] * DJNZ_US)
            source = "cycle model (no capture log found - run extract.py first)"
            print(f"  NOTE: no capture log for ${sid:02X}; using the cycle "
                  f"model rather than a measurement", file=sys.stderr)

        base = f"DAC_{sid:02X}"
        with open(os.path.join(outdir, base + ".dpcm"), "wb") as f:
            f.write(raw)
        with open(os.path.join(outdir, base + "_u8.raw"), "wb") as f:
            f.write(dec)
        write_wav_u8(os.path.join(outdir, base + ".wav"), dec, rate)

        print(f" ${sid:02X} {e['bank']:>4} ${e['rom_start']:06X} {e['length']:7d} "
              f"{e['samples']:8d}  ${e['rate']:02X} {rate:9.1f} "
              f"{e['samples']/rate:7.3f} {'yes' if e['uninterruptible'] else 'no':>7}  {base}.*")
        meta.append(dict(
            sound_id=f"${sid:02X}", z80_cmd=e["z80_cmd"], bank=e["bank"],
            bank_base=f"${e['bank_base']:06X}",
            table_entry=f"${e['bank_base'] + e['entry_index']*12:06X}",
            z80_start=f"${e['z80_start']:04X}", rom_start=f"${e['rom_start']:06X}",
            rom_end=f"${e['rom_start']+e['length']-1:06X}",
            length_bytes=e["length"], samples=e["samples"],
            format="4-bit DPCM (2 samples/byte), delta table at $%06X" % R.DPCM_DELTA_ROM,
            decoded_format="unsigned 8-bit, centre $80",
            rate_byte=f"${e['rate']:02X}",
            playback_hz=round(rate, 1), rate_source=source,
            measured_writes=nmeas,
            duration_s=round(e["samples"] / rate, 4),
            uninterruptible=e["uninterruptible"],
            raw_dpcm=f"{OUT}/{base}.dpcm",
            decoded_raw=f"{OUT}/{base}_u8.raw",
            wav=f"{OUT}/{base}.wav",
            referenced_by_sound_ids=[f"${sid:02X}"],
        ))

    with open(os.path.join(ROOT, OUT, "pcm_samples.json"), "w") as f:
        json.dump(dict(
            note="Strider stores no raw PCM; all DAC content is 4-bit DPCM.",
            z80_driver=dict(rom_start=f"${R.Z80_BLOB_ROM:06X}",
                            length=R.Z80_BLOB_LEN,
                            uploaded_to="Z80 RAM $0000 ($A00000 from the 68000)"),
            delta_table=dict(rom=f"${R.DPCM_DELTA_ROM:06X}",
                             values=[v - 256 if v > 127 else v for v in deltas]),
            samples=meta), f, indent=2)
    print(f"\nwrote {len(meta)} samples to {OUT}/ (+ pcm_samples.json)")


if __name__ == "__main__":
    main()
