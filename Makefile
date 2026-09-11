# strider-extract
#
# Requires: MAME on PATH (brew install mame), and a Python venv:
#     python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
# Requires your own ROMs in roms/ -- see roms/README.md

PY      := ./.venv/bin/python
JOBS    ?= 5
MD_ROM  := roms/Strider (USA, Europe).md
ARC_ROM := roms/strider/09.12b

.DEFAULT_GOAL := help
.PHONY: help all megadrive arcade analyse validate clean distclean check-roms check-tools

help:
	@echo "strider-extract"
	@echo
	@echo "  make all         both platforms, end to end (~15 min)"
	@echo "  make megadrive   Mega Drive rip"
	@echo "  make arcade      CPS-1 arcade rip"
	@echo "  make analyse     static analysis reports only (no emulation)"
	@echo "  make validate    Mega Drive validation passes"
	@echo "  make clean       remove build/ and output/"
	@echo "  make distclean   also remove analysis/ and tools/"
	@echo
	@echo "  JOBS=$(JOBS)  parallel MAME processes"

check-tools:
	@command -v mame >/dev/null || { echo "error: mame not found (brew install mame)"; exit 1; }
	@test -x $(PY) || { echo "error: $(PY) missing; see README Quick start"; exit 1; }

check-roms: check-tools
	@test -f "$(MD_ROM)"  || { echo "error: missing '$(MD_ROM)' - see roms/README.md"; exit 1; }
	@test -f "$(ARC_ROM)" || { echo "error: missing '$(ARC_ROM)' - see roms/README.md"; exit 1; }
	@mame strider -verifyroms >/dev/null 2>&1 || echo "warning: 'mame strider -verifyroms' is not clean"

all: megadrive arcade
	@echo
	@echo "done. outputs in output/megadrive/ and output/arcade/"

megadrive: check-roms
	@mkdir -p analysis/megadrive/tables
	$(PY) scripts/analyze_rom.py > analysis/megadrive/tables/report.txt
	$(PY) scripts/extract.py --jobs $(JOBS)
	$(PY) scripts/extract_pcm.py
	$(PY) scripts/validate.py --jobs $(JOBS)

arcade: check-roms
	@mkdir -p analysis/arcade/tables analysis/arcade/disassembly
	$(PY) scripts/arcade_analyze.py > analysis/arcade/tables/report.txt
	$(PY) scripts/disz80.py --target arcade > analysis/arcade/disassembly/cps1_sound_09.12b.asm
	$(PY) scripts/extract_oki.py
	$(PY) scripts/arcade_extract.py --jobs $(JOBS)

analyse: check-roms
	@mkdir -p analysis/megadrive/tables analysis/megadrive/disassembly
	@mkdir -p analysis/arcade/tables analysis/arcade/disassembly
	$(PY) scripts/analyze_rom.py  > analysis/megadrive/tables/report.txt
	$(PY) scripts/arcade_analyze.py > analysis/arcade/tables/report.txt
	$(PY) scripts/dis68k.py B00F8 --mode trace \
	      --entries B00F8,B041A,B0502,B05D6,B061E,B0790 \
	      > analysis/megadrive/disassembly/driver_trace.asm
	$(PY) scripts/disz80.py --target genesis > analysis/megadrive/disassembly/z80_dac_driver.asm
	$(PY) scripts/disz80.py --target arcade  > analysis/arcade/disassembly/cps1_sound_09.12b.asm
	@echo "reports in analysis/*/tables/, listings in analysis/*/disassembly/"

validate: check-roms
	$(PY) scripts/validate.py --jobs $(JOBS)

clean:
	rm -rf build output

distclean: clean
	rm -rf analysis tools/mame/cfg tools/mame/nvram tools/mame/sta
