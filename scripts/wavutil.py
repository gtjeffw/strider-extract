#!/usr/bin/env python3
"""WAV post-processing for the Strider captures.

MAME writes 48 kHz / 16-bit / stereo. Two things need doing:

* DC removal. With YM2612 register $2B (DAC enable) left set and a value
  latched in $2A, MAME's idle output sits at a constant offset (+252 here)
  rather than at zero. Real hardware AC-couples its output, so removing the
  measured offset is the more faithful result, not a cosmetic tweak. The
  offset is measured from the pre-sound lead-in, which is guaranteed silent
  because the capture starts from a frozen, silent save state.
* Conservative trimming. Leading silence is cut to PRE_MARGIN before the first
  audible sample; trailing silence is cut to TAIL_MARGIN after the last one, so
  FM release envelopes and delayed echoes survive.
"""
import array, wave

SILENCE_THRESH = 8      # LSB deviation from DC counted as audible (~-72 dBFS)
PRE_MARGIN_S   = 0.010
TAIL_MARGIN_S  = 0.200
LEADIN_PROBE_S = 0.008  # window used to measure the DC offset


def read_wav(path):
    with wave.open(path, "rb") as w:
        assert w.getsampwidth() == 2, "expected 16-bit"
        nch, sr, n = w.getnchannels(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    s = array.array("h")
    s.frombytes(raw)
    return s, nch, sr, n


def write_wav(path, samples, nch, sr):
    with wave.open(path, "wb") as w:
        w.setnchannels(nch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.tobytes())


def process(src, dst, thresh=SILENCE_THRESH):
    s, nch, sr, n = read_wav(src)
    probe = max(1, int(LEADIN_PROBE_S * sr))
    dc = []
    for c in range(nch):
        vals = sorted(s[c:probe * nch:nch])
        dc.append(vals[len(vals) // 2] if vals else 0)

    first = last = None
    for i in range(n):
        base = i * nch
        for c in range(nch):
            if abs(s[base + c] - dc[c]) > thresh:
                if first is None:
                    first = i
                last = i
                break

    stats = dict(sample_rate=sr, channels=nch, dc_offset=dc,
                 raw_frames=n, raw_duration=n / sr)
    if first is None:
        stats.update(silent=True, frames=0, duration=0.0, peak=0, rms=0.0,
                     onset=None)
        write_wav(dst, array.array("h"), nch, sr)
        return stats

    a = max(0, first - int(PRE_MARGIN_S * sr))
    b = min(n, last + int(TAIL_MARGIN_S * sr) + 1)
    out = array.array("h", bytes(0))
    peak, sq = 0, 0
    for i in range(a, b):
        base = i * nch
        for c in range(nch):
            v = s[base + c] - dc[c]
            v = -32768 if v < -32768 else (32767 if v > 32767 else v)
            out.append(v)
            av = -v if v < 0 else v
            if av > peak:
                peak = av
            sq += v * v
    write_wav(dst, out, nch, sr)
    stats.update(silent=False, frames=b - a, duration=(b - a) / sr,
                 peak=peak, rms=(sq / len(out)) ** 0.5 if out else 0.0,
                 onset=first / sr, trimmed_head=a, trimmed_tail=n - b,
                 truncated=(b >= n))
    return stats
