# Put your ROMs here

Nothing in this directory is tracked by git. Strider is copyright Capcom; you
need your own legally obtained dumps.

## Mega Drive

```
roms/Strider (USA, Europe).md
```

1 048 576 bytes, SHA-1 `26fe42d13a01c8789bbad722ebac05b8a829eb37`
(MD5 `fcab622a6e56f7e9b1e0907ab5a630df`). Header: `STRIDER HIRYU`,
serial `GM 00001112-00`, region `UE`.

The scripts warn if the hash differs but will still run — a different revision
will very likely have different addresses, so check
[../docs/megadrive.md](../docs/megadrive.md) before trusting the output.

## CPS-1 arcade

```
roms/strider/       (or roms/strider.zip)
```

An unzipped MAME romset for the set **`strider`** — *Strider (USA, B-Board
89624B-2)*, 23 files:

| File | Size | CRC32 | Role |
|---|---|---|---|
| `30.11f` | 131072 | `da997474` | 68000 program |
| `35.11h` | 131072 | `5463aaa3` | 68000 program |
| `31.12f` | 131072 | `d20786db` | 68000 program |
| `36.12h` | 131072 | `21aa2863` | 68000 program |
| `st-14.8h` | 524288 | `9b3cfc08` | 68000 program |
| **`09.12b`** | **65536** | **`2ed403bc`** | **Z80 sound program** |
| **`18.11c`** | **131072** | **`4386bc80`** | **OKIM6295 samples** |
| **`19.12c`** | **131072** | **`444536d7`** | **OKIM6295 samples** |
| `st-1.7a` `st-2.8a` `st-4.3a` `st-5.4a` `st-8.5a` `st-9.6a` `st-10.9a` `st-11.10a` | 524288 each | | graphics |
| `buf1` `ioa1` `lwio.11e` `prg1` `rom1` `sou1` `st24m1.1a` | 279 each | | PLDs |

The directory name **must** be `strider`: that is how MAME locates a set.
`mame.ini` sets `rompath roms`, so verify with:

```bash
mame strider -verifyroms      # -> "romset strider is good"
```

Only the sound ROMs and the `maincpu` program are used by the extraction, but
MAME needs the whole set to boot the board.
