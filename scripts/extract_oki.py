#!/usr/bin/env python3
"""Extract the OKIM6295 ADPCM samples from Strider CPS-1.

These are the arcade game's sampled sound effects and voices - the direct
counterpart of the Mega Drive version's DPCM samples, except the codec is
standard OKI/Dialogic 4-bit ADPCM rather than Capcom's own delta table.

The sample ROM (18.11c + 19.12c = 256 KiB) begins with the chip's own phrase
table: 128 entries of 8 bytes, each holding a 24-bit big-endian start and end
address. Only entries 1..28 are populated here; 0 and 29..31 are zero and
32..127 are $FF padding.

Playback rate: the driver writes $0DCF = $01 to $F006, i.e. OKIM6295 pin 7
HIGH, so the divider is /132 and the rate is 1000000/132 = 7575.76 Hz.

Outputs into output-arcade/raw-adpcm/:
    OKI_nn.adpcm    raw 4-bit ADPCM exactly as stored in the ROM
    OKI_nn.wav      decoded, mono 16-bit at the native rate
    oki_samples.json
"""
import argparse, json, os, sys, wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import arcade_info as A

OUT = "output/arcade/raw-adpcm"


def write_wav_s16(path, samples12, rate):
    """OKI ADPCM decodes to 12-bit signed; scale by 16 into 16-bit."""
    import array
    a = array.array("h", (max(-32768, min(32767, s * 16)) for s in samples12))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(round(rate)))
        w.writeframes(a.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=A.OKI_RATE_PIN7_HIGH)
    a = ap.parse_args()

    rom = A.oki_rom()
    z80 = A.z80_rom()
    outdir = os.path.join(ROOT, OUT)
    os.makedirs(outdir, exist_ok=True)

    pin7 = z80[0x0DCF] & 1
    rate = A.OKI_RATE_PIN7_HIGH if pin7 else A.OKI_RATE_PIN7_LOW
    if a.rate:
        rate = a.rate
    print(f"OKIM6295 @ {A.OKI_CLOCK} Hz, pin7={pin7} (from $0DCF) "
          f"-> /{132 if pin7 else 165} = {rate:.2f} Hz")
    print(f"sample ROM: {' + '.join(A.OKI_ROMS)} = {len(rom)} bytes\n")

    codes = {c - 0x80: (c, b) for c, b, _ in A.oki_code_table(z80)[0]}

    hdr = (f"{'ph':>3} {'code':>5} {'rom range':>17} {'bytes':>7} {'samples':>8} "
           f"{'dur s':>7} {'voice':>5} {'att':>3}  file")
    print(hdr); print("-" * len(hdr))

    meta, nout = [], 0
    for p in A.oki_phrases(rom):
        i = p["index"]
        if p["empty"] or p["start"] == 0xFFFFFF or p["end"] < p["start"] \
           or p["end"] >= len(rom):
            continue
        raw = rom[p["start"]:p["end"] + 1]
        dec = A.decode_oki_adpcm(raw)
        base = f"OKI_{i:02d}"
        with open(os.path.join(outdir, base + ".adpcm"), "wb") as f:
            f.write(raw)
        write_wav_s16(os.path.join(outdir, base + ".wav"), dec, rate)
        code, playbyte = codes.get(i, (None, None))
        voice = {0x10: 0, 0x20: 1, 0x40: 2, 0x80: 3}.get(
            (playbyte or 0) & 0xF0, None)
        print(f"{i:3d} {('$%02X' % code) if code else '-':>5} "
              f"${p['start']:06X}-${p['end']:06X} {len(raw):7d} {len(dec):8d} "
              f"{len(dec)/rate:7.3f} {voice if voice is not None else '-':>5} "
              f"{(playbyte & 0x0F) if playbyte else 0:3d}  {base}.*")
        meta.append(dict(
            phrase=i, sound_code=f"${code:02X}" if code else None,
            rom_start=f"${p['start']:06X}", rom_end=f"${p['end']:06X}",
            adpcm_bytes=len(raw), samples=len(dec),
            playback_hz=round(rate, 2), duration_s=round(len(dec) / rate, 4),
            oki_voice=voice, attenuation=(playbyte & 0x0F) if playbyte else 0,
            play_byte=f"${playbyte:02X}" if playbyte is not None else None,
            format="OKI/Dialogic 4-bit ADPCM, 2 samples per byte, high nibble first",
            decoded_format="12-bit signed (scaled x16 into the 16-bit WAV)",
            raw_adpcm=f"{OUT}/{base}.adpcm", wav=f"{OUT}/{base}.wav"))
        nout += 1

    with open(os.path.join(outdir, "oki_samples.json"), "w") as f:
        json.dump(dict(
            chip="OKIM6295", clock=A.OKI_CLOCK, pin7=pin7,
            divider=132 if pin7 else 165, playback_hz=round(rate, 2),
            sample_roms=A.OKI_ROMS, rom_bytes=len(rom),
            phrase_table=dict(at="$000000", entries=A.OKI_PHRASE_ENTRIES,
                              entry_size=A.OKI_PHRASE_SIZE,
                              populated="1..28"),
            note=("Sound codes $80-$9C select phrase (code-$80) directly; the "
                  "byte at $0DB0+i is written as the OKI play byte "
                  "(voice mask in bits 4-7, attenuation in bits 0-3)."),
            samples=meta), f, indent=2)
    print(f"\nwrote {nout} samples to {OUT}/ (+ oki_samples.json)")
    tot = sum(m["adpcm_bytes"] for m in meta)
    print(f"total ADPCM {tot} bytes, {sum(m['samples'] for m in meta)} samples, "
          f"{sum(m['duration_s'] for m in meta):.1f} s of audio")


if __name__ == "__main__":
    main()
