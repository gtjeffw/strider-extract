-- Strider sound capture, driven by scripts/extract.py.
--
-- Loads the frozen "clean" state (see scripts/make_state.lua), pokes one sound
-- id into the driver's request queue at $FF9C0A, logs every YM2612 and SN76489
-- write with its machine timestamp, and exits once the sound has stopped
-- writing to the chips for QUIET_FRAMES (or MAX_FRAMES, whichever comes first).
--
-- Environment:
--   SID          sound id to play, e.g. 0xA0
--   POKE_FRAME   frame on which to poke it (default 2)
--   MAX_FRAMES   hard cap (default 900)
--   QUIET_FRAMES stop after this many frames with no chip writes (default 150)
--   LOG_PATH     where to write the chip-write log
--
-- Log format, one write per line:  <seconds> <ym|psg> <offset> <value>
-- YM offsets are 0..3 (0/1 = port 0 addr/data, 2/3 = port 1 addr/data), from
-- either CPU; PSG offset is always 0.

local SID          = tonumber(os.getenv("SID"))
local POKE_FRAME   = tonumber(os.getenv("POKE_FRAME")   or "2")
local MAX_FRAMES   = tonumber(os.getenv("MAX_FRAMES")   or "900")
local QUIET_FRAMES = tonumber(os.getenv("QUIET_FRAMES") or "150")
local LOG_PATH     = os.getenv("LOG_PATH") or "/tmp/strider_chip.log"

local QUEUE     = 0xFF9C0A
local GAME_MODE = 0xFFE100

local m   = manager.machine
local cpu = m.devices[":maincpu"]
p68 = cpu.spaces["program"]
pz8 = m.devices[":genesis_snd_z80"].spaces["program"]

frames      = 0
poked       = false
last_write  = nil     -- frame of the most recent chip write after the poke
poke_time   = nil     -- machine time (s) at which the poke happened
nwrite      = 0
log         = {}
stray       = {}

local function now() return m.time:as_double() end

local function rec(kind, off, val)
  if not poked then return end          -- ignore anything before the poke
  nwrite = nwrite + 1
  last_write = frames
  log[#log+1] = string.format("%.9f %s %d %d", now() - poke_time, kind, off, val & 0xff)
end

-- Tap semantics, verified empirically on MAME 0.289:
--   68000 (16-bit bus): `off` is the WORD-aligned address and `data` has the
--     byte mirrored into both halves; `mask` selects which byte -- 0xFF00 is
--     the even (low) address, 0x00FF the odd one.
--   Z80 (8-bit bus): `off` is the exact byte address, `data` the byte.
--
-- Logged YM offsets are normalised to 0..3:
--   0 = port 0 address, 1 = port 0 data, 2 = port 1 address, 3 = port 1 data.

-- YM2612: 68000 side at $A04000-$A04003, Z80 side at $4000-$4003.
KEEP_y68 = p68:install_write_tap(0xA04000, 0xA04003, "ym68",
  function(off, data, mask)
    local base = off - 0xA04000
    if (mask & 0xff00) ~= 0 then rec("ym", base,     (data >> 8) & 0xff) end
    if (mask & 0x00ff) ~= 0 then rec("ym", base + 1,  data       & 0xff) end
    return data
  end)
KEEP_yz8 = pz8:install_write_tap(0x4000, 0x4003, "ymz8",
  function(off, data, mask) rec("ym", off - 0x4000, data); return data end)

-- SN76489: only the odd byte is the PSG port ($C00011 / $7F11).
KEEP_p68 = p68:install_write_tap(0xC00010, 0xC00013, "psg68",
  function(off, data, mask)
    if (mask & 0x00ff) ~= 0 then rec("psg", 0, data & 0xff) end
    return data
  end)
KEEP_pz8 = pz8:install_write_tap(0x7F11, 0x7F11, "psgz8",
  function(off, data, mask) rec("psg", 0, data); return data end)

-- Anything else enqueueing a sound would contaminate the capture; the patched
-- ROM should make this impossible, but assert it rather than assume it.
KEEP_q = p68:install_write_tap(0xFF9C0A, 0xFF9C0D, "queue",
  function(off, data, mask)
    local v = data & 0xff
    if poked and v ~= 0 and cpu.state["PC"].value < 0x0B0000 then
      stray[#stray+1] = string.format("f%d pc=%06X %06X<-%02X",
        frames, cpu.state["PC"].value, off, v)
    end
    return data
  end)

local function finish(why)
  local f = io.open(LOG_PATH, "w")
  f:write(string.format("# sid=0x%02X poke_frame=%d frames=%d writes=%d reason=%s\n",
    SID, POKE_FRAME, frames, nwrite, why))
  f:write(string.format("# last_write_frame=%s stray=%d\n",
    tostring(last_write), #stray))
  for i = 1, #stray do f:write("# STRAY "..stray[i].."\n") end
  f:write(table.concat(log, "\n"))
  if #log > 0 then f:write("\n") end
  f:close()
  print(string.format("sid=$%02X frames=%d writes=%d last_write=%s reason=%s stray=%d",
    SID, frames, nwrite, tostring(last_write), why, #stray))
  m:exit()
end

KEEP_fn = emu.add_machine_frame_notifier(function()
  frames = frames + 1
  if frames == POKE_FRAME then
    poke_time = now()
    poked = true
    p68:write_u8(QUEUE, SID)
    -- Record the poke instant so the Python side can trim the lead-in exactly.
    print(string.format("POKE_TIME %.9f", poke_time))
  elseif poked then
    if frames >= MAX_FRAMES then
      return finish("max_frames")
    end
    if last_write and (frames - last_write) >= QUIET_FRAMES then
      return finish("quiet")
    end
    if not last_write and frames - POKE_FRAME >= QUIET_FRAMES then
      return finish("never_started")
    end
  end
end)
