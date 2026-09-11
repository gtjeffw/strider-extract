# strider-extract

Extract every sound from both versions of Capcom's **Strider** — Sega Mega Drive
and CPS-1 arcade — as WAV files, by reverse engineering each game's sound driver
and then letting the real driver play each sound inside an accurate emulator.

Not a sample ripper. Most of this audio does not exist as samples anywhere in
the ROMs: it is FM synthesis performed on a YM2612 or YM2151 at playback time.
The only way to get the audible result is to run the game's own sound code on
emulated chips and record the output. That is what this does, headlessly and
reproducibly.

| Version | Sound ids | Music | Sound effects | Output |
|---|---|---|---|---|
| Mega Drive | 87 | 31 | 48 FM + 8 DPCM | `output/megadrive/` |
| CPS-1 arcade | 123 | 24 | 70 FM + 28 ADPCM | `output/arcade/` |

That is 87 + 122 = **209 WAVs**. The arcade side has 123 sound codes but one
(`$80`) selects a null sample-table entry and is silent by design, so it gets a
metadata row and no file.

Versions targeted, exactly:

* **Mega Drive** — `Strider (USA, Europe)`, SHA-1
  `26fe42d13a01c8789bbad722ebac05b8a829eb37`
* **CPS-1 arcade** — MAME set **`strider`**, listed in MAME's game list as
  *MAME Games > Strider (USA, B-Board 89624B-2)*

Other revisions and other MAME Strider sets (`striderua`, `striderj`,
`striderjr`, `strideruc`) have different sound-ROM contents, so the addresses in
the docs will not carry over to them.

**You must supply your own ROMs.** None are included — see
[Adding ROMs](#adding-roms).

---

## Quick start

```bash
brew install mame
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
# put your ROMs in roms/ (see below), then:
make all
```

`make all` runs both platforms end to end: static analysis, patched-ROM build,
save states, all captures, sample extraction and validation. Expect ~15 minutes.

Individual targets:

```bash
make megadrive     # Mega Drive rip only
make arcade        # arcade rip only
make analyse       # static analysis reports only, no emulation
make validate      # re-run the Mega Drive validation passes
make clean         # remove build/ and output/ (keeps roms/)
```

## Adding ROMs

`roms/` is tracked but empty. Drop in:

```
roms/
  Strider (USA, Europe).md     # Mega Drive, 1 048 576 bytes
  strider/                     # CPS-1, unzipped MAME romset
    09.12b  18.11c  19.12c  30.11f  31.12f  35.11h  36.12h
    st-1.7a  st-2.8a  st-4.3a  st-5.4a  st-8.5a  st-9.6a
    st-10.9a  st-11.10a  st-14.8h
    buf1  ioa1  lwio.11e  prg1  rom1  sou1  st24m1.1a
```

* The Mega Drive file must be SHA-1 `26fe42d13a01c8789bbad722ebac05b8a829eb37`.
  The scripts warn on a mismatch but will still run.
* The arcade set is MAME's **`strider`** — in MAME's game list,
  *Strider (USA, B-Board 89624B-2)*. Other Strider sets exist (`striderua`,
  `striderj`, `striderjr`, `strideruc`) and have **different** sound-ROM
  contents, so the addresses in [docs/arcade.md](docs/arcade.md) will not
  match them.
* The arcade directory **must be named `strider`** — that is the MAME set name
  and how MAME finds it. `roms/strider.zip` works too. `mame.ini` sets
  `rompath roms`, so `mame strider -verifyroms` should print *"romset strider is
  good"*.
* Only the sound-relevant arcade files are strictly needed (`09.12b`, `18.11c`,
  `19.12c`, and the four `maincpu` ROMs), but MAME wants the full set to boot.

## Outputs

```
output/
  megadrive/
    sounds.csv              87 rows, 27 columns
    sounds.json             same, plus the sound-test map and render method
    validation.json         patch audit + per-index validation results
    wav/                    48 kHz / 16-bit / stereo
      MUS_01_81.wav  … MUS_31_9F.wav      31 music tracks
      SFX_001_A0.wav … SFX_048_CF.wav     48 FM sound effects
      SFX_049_D0.wav … SFX_056_D7.wav      8 DPCM samples
    vgm/                    87 VGM 1.50 logs (YM2612 + SN76489)
    raw-pcm/                the 8 DPCM samples: .dpcm, _u8.raw, .wav
  arcade/
    sounds.csv              123 rows, 30 columns (one row has no WAV: $80)
    sounds.json
    wav/                    48 kHz / 16-bit / mono
      MUS_00.wav … MUS_17.wav             24 music entries
      SFX_30.wav … SFX_77.wav             70 FM sound effects
      PCM_81.wav … PCM_9C.wav             28 sampled effects / voices
    vgm/                    123 VGM 1.61 logs (YM2151 + OKIM6295)
    raw-adpcm/              the 28 OKI samples: .adpcm, .wav
```

Audio policy: no gain, no normalisation, no dither. The emulator's constant DC
offset is removed (real hardware AC-couples its output) and silence is trimmed
conservatively — 10 ms head, 200 ms tail — so FM release tails survive. Native
channel count per platform. Looping music is captured as a 60 s excerpt and
flagged as such in `notes`. See
[method.md §8](docs/method.md#8-wav-post-processing).

## Documentation

| File | Contents |
|---|---|
| [docs/method.md](docs/method.md) | How the rips are made, and how to adapt the approach to another game |
| [docs/megadrive.md](docs/megadrive.md) | Mega Drive sound system: SMPS-68k driver, Z80 DPCM player, sound test |
| [docs/arcade.md](docs/arcade.md) | CPS-1 sound system: Z80 driver, YM2151, OKIM6295 |

Both platform documents mark every claim **[C] confirmed** or **[H] hypothesis**.

## What the two systems turned out to be

They share essentially nothing.

| | Mega Drive | CPS-1 arcade |
|---|---|---|
| Sequencer runs on | **68000** (SMPS-68k variant, Capcom-modified) | **Z80** |
| Clocked by | VBlank, 60 Hz | YM2151 Timer A |
| FM | YM2612, 6 channels | YM2151, 8 channels |
| Second chip | SN76489 PSG — **never used** | — |
| Samples | Capcom 4-bit DPCM on the Z80 → YM2612 DAC | OKIM6295, standard OKI ADPCM |
| Sample count | 8 (13.5 s) | 28 (66.4 s) |
| Output | stereo | mono |
| Sound request | queue byte at `$FF9C0A` | latch byte at `$800180` |

Two findings worth pulling out:

* **The Mega Drive version never writes the PSG.** Zero SN76489 writes across
  all 87 captures, even though the driver carries three PSG tracks.
* **The arcade driver partitions the YM2151.** Music uses channels 1–6, FM
  effects use only 7–8, with no overlap across all 94 sequences — so an effect
  can never steal a voice from the music. The Mega Drive driver does the
  opposite, explicitly marking and taking over a music track's channel.

## Validation

`make validate` (Mega Drive) — all checks pass:

1. **Patch audit.** The patched ROMs differ from the original in 330 / 342 bytes
   across 53 / 52 ranges, and no changed range overlaps the song tables,
   sequence data, FM voices, Z80 DPCM player, sample data or the sound-test
   table.
2. **Sound-test mapping via the game's own code.** A ROM variant leaves the real
   dispatch instruction intact and calls it with each menu index. All **66 / 66**
   resolve to exactly the ids read statically from `$00344E`.
3. **Rendering fidelity.** For all **66 / 66**, the `(port, register, value)`
   chip-write sequence the game's own sound-test path produces is **identical**
   to the harness's, so the WAVs are equivalent by construction.

Arcade, cross-checked two independent ways: every OKI sample's duration computed
by decoding the ADPCM in Python from the phrase table, versus measured from
MAME's rendered audio — **28 / 28 agree to within 11 ms**.

Across both platforms: 0 unexpected silent outputs, 0 truncated effects, 0 stray
sound requests during capture, 0 duplicate outputs, 0 malformed VGM.

## Repository layout

```
strider-extract/
  README.md  Makefile  mame.ini  requirements.txt
  docs/            method.md, megadrive.md, arcade.md
  scripts/         analysis, patching, capture and rendering
  roms/            your ROMs go here (contents untracked)
  build/           patched ROM copies (generated)
  analysis/        disassemblies, chip-write logs, static tables (generated)
  output/          the rips (generated)
  tools/mame/      MAME cfg, nvram and save states (generated)
```

Everything except `README.md`, `Makefile`, `mame.ini`, `requirements.txt`,
`docs/` and `scripts/` is generated or user-supplied, and is not tracked.

## Scripts

| Script | Purpose |
|---|---|
| `rominfo.py` / `arcade_info.py` | Shared constants and ROM table parsers |
| `dis68k.py` | 68000 disassembler (linear-with-resync and recursive-descent) |
| `disz80.py` | Z80 disassembler (`--target genesis` / `--target arcade`) |
| `analyze_rom.py` / `arcade_analyze.py` | Static analysis reports |
| `patch_rom.py` | Mega Drive ROM patcher (`--mode mute\|freeze\|soundtest`) |
| `extract.py` / `arcade_extract.py` | Capture orchestrators |
| `extract_pcm.py` / `extract_oki.py` | Sample extraction and decoding |
| `validate.py` | Mega Drive validation passes |
| `vgm.py` / `arcade_vgm.py` | Chip-write log → VGM, chip-usage analysis |
| `wavutil.py` | DC removal and conservative trimming |
| `audit_repo.py` | Check no ROM-derived content is tracked by git |
| `*.lua` | MAME automation: save states, captures, sound-test driving |

## Known limitations

* Looping music is captured as a 60 s excerpt from the start; loop points are
  not marked, because neither sequence format's loop command was decoded.
* VGM files are **structurally** validated (header fields, command-stream walk,
  accumulated waits vs the total-samples field) but have **not** been played
  back — no VGM player was installed. The arcade ones in particular use
  YM2151 + OKIM6295 with an attached ROM data block, a less common combination.
* The Mega Drive sound test's on-screen labels were not recovered: they are VDP
  tile descriptors, not ASCII, and the ROM contains no sound-name strings.
  Outputs are named by id.
* The arcade music / FM-effect split is inferred from YM2151 channel allocation.
  The separation is perfect, but it is an inference from behaviour rather than a
  label in the ROM.

## What this repository does and does not contain

`roms/`, `build/`, `output/` and `analysis/` are all untracked. Concretely, the
32 tracked files are: `README.md`, `LICENSE`, `Makefile`, `mame.ini`,
`requirements.txt`, `.gitignore`, three files in `docs/`, `roms/README.md` and
21 scripts. Nothing else.

That means **none** of the following is distributed here:

| Not distributed | Where it lives when you build |
|---|---|
| ROM images | `roms/` — you supply them |
| Patched ROM copies | `build/` |
| Full disassembly listings | `analysis/*/disassembly/` |
| Extracted audio (WAV, VGM, raw samples) | `output/` |
| Chip-write logs | `analysis/*/logs/` |
| Extracted data tables (JSON) | `analysis/*/tables/` |

The generated listings are the *result* of running the tooling on your own dump,
not something shipped with it — you get the recipe, and you run it.

The `docs/` write-ups do contain **short disassembly excerpts** — 103 lines
across the two platform documents, roughly 0.3 KiB of the original programs,
each quoted alongside the analysis of what it does. That is the normal form for
reverse-engineering documentation; without it the write-ups would be assertions
with nothing backing them.

They contain **no verbatim ROM data tables**. Every table the documents refer to
— the sound-test index map, the DPCM delta table, the FM voice bytes, the song
channel deltas, the OKIM6295 phrase table — is described structurally and then
printed from *your* ROM by `make analyse`. This is enforced by an audit, not by
good intentions:

```bash
make audit
```

`scripts/audit_repo.py` lists every tracked file, asserts that nothing under
`roms/`, `build/`, `output/`, `analysis/` or `tools/` is tracked, and scans
every tracked text file for hex byte runs of 4 bytes or more, testing each one
against your actual ROM images. Any hit is verbatim ROM content and fails the
run. It currently reports **0 bytes**. It also counts the quoted disassembly
lines, so that number cannot drift unnoticed.

I am not a lawyer and none of this is legal advice. It is a description of what
is in the repository so you can make your own call.

## Licence

Code and documentation: MIT, see [LICENSE](LICENSE).

Strider is copyright Capcom. The licence covers this tooling only — not the
game, not any ROM image, and not anything the scripts generate from one.
Whatever you extract is a derived work of Capcom's copyrighted material; keep it
local rather than redistributing it.
