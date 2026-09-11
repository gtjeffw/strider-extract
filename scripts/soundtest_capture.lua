-- Capture one sound-test entry by INDEX, through the game's own dispatch code.
--
-- Requires the `--mode soundtest` ROM: its frozen main loop polls $FFFDE8 and,
-- when set, calls $003442 (`move.w $fde6.w,d0 / move.b $344E(pc,d0.w),$9C0A.w`).
-- So the harness only supplies the menu index; the game resolves it to a sound
-- id itself. Logs chip writes in exactly the format scripts/capture.lua uses.
--
-- Env: IDX, LOG_PATH, MAX_FRAMES, QUIET_FRAMES

local IDX          = tonumber(os.getenv("IDX"))
local MAX_FRAMES   = tonumber(os.getenv("MAX_FRAMES")   or "900")
local QUIET_FRAMES = tonumber(os.getenv("QUIET_FRAMES") or "90")
local LOG_PATH     = os.getenv("LOG_PATH") or "/tmp/strider_st.log"

local ST_INDEX = 0xFFFDE6
local ST_GO    = 0xFFFDE8
local POKE_FRAME = 3

local m   = manager.machine
local cpu = m.devices[":maincpu"]
p68 = cpu.spaces["program"]
pz8 = m.devices[":genesis_snd_z80"].spaces["program"]

frames, poked, last_write, poke_time, nwrite, log = 0, false, nil, nil, 0, {}
resolved = nil

local function now() return m.time:as_double() end
local function rec(kind, off, val)
  if not poked then return end
  nwrite = nwrite + 1; last_write = frames
  log[#log+1] = string.format("%.9f %s %d %d", now() - poke_time, kind, off, val & 0xff)
end

KEEP1 = p68:install_write_tap(0xA04000, 0xA04003, "a", function(off, data, mask)
  local b = off - 0xA04000
  if (mask & 0xff00) ~= 0 then rec("ym", b,     (data >> 8) & 0xff) end
  if (mask & 0x00ff) ~= 0 then rec("ym", b + 1,  data       & 0xff) end
  return data end)
KEEP2 = pz8:install_write_tap(0x4000, 0x4003, "b", function(off, data, mask)
  rec("ym", off - 0x4000, data); return data end)
KEEP3 = p68:install_write_tap(0xC00010, 0xC00013, "c", function(off, data, mask)
  if (mask & 0x00ff) ~= 0 then rec("psg", 0, data & 0xff) end
  return data end)
-- record which sound id the GAME chose for this index
KEEP4 = p68:install_write_tap(0xFF9C0A, 0xFF9C0D, "q", function(off, data, mask)
  local v = data & 0xff
  if poked and v ~= 0 and resolved == nil then resolved = v end
  return data end)

local function finish(why)
  local f = io.open(LOG_PATH, "w")
  f:write(string.format("# idx=%d sid=0x%02X frames=%d writes=%d reason=%s source=soundtest\n",
    IDX, resolved or 0, frames, nwrite, why))
  f:write(table.concat(log, "\n"))
  if #log > 0 then f:write("\n") end
  f:close()
  print(string.format("idx=%2d -> sid=$%02X writes=%d (%s)", IDX, resolved or 0, nwrite, why))
  m:exit()
end

KEEP_fn = emu.add_machine_frame_notifier(function()
  frames = frames + 1
  if frames == POKE_FRAME - 1 then
    p68:write_u16(ST_INDEX, IDX)              -- select the menu entry
  elseif frames == POKE_FRAME then
    poke_time = now(); poked = true
    p68:write_u8(ST_GO, 1)                    -- one-shot: the game plays it
  elseif poked then
    if frames >= MAX_FRAMES then return finish("max_frames") end
    if last_write and (frames - last_write) >= QUIET_FRAMES then return finish("quiet") end
    if not last_write and frames - POKE_FRAME >= QUIET_FRAMES then
      return finish("never_started")
    end
  end
end)
