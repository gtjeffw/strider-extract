-- Capture one Strider CPS-1 sound code, driven by scripts/arcade_extract.py.
--
-- Loads the silent test-screen state, writes one sound code to the 68000's
-- sound latch at $800180, logs every YM2151 and OKIM6295 write with its
-- machine timestamp, and exits once the chips have gone quiet.
--
-- The game keeps writing the idle code $FF every couple of frames even on the
-- test screen. On its own that is harmless ($FF dispatches to $0394 = RET),
-- but it could also land between our write and the Z80 polling the latch and
-- lose the command. So once we have poked, a write tap rewrites any further
-- $FF into our own code: the Z80 compares each new code against the last one
-- it consumed ($D000) and returns early when they match, so the rewritten
-- value is a guaranteed no-op and the race disappears.
--
-- Env: CODE, MAX_FRAMES, QUIET_FRAMES, LOG_PATH
--
-- Log format:  <seconds since poke> <ym|oki> <reg> <value>

local CODE         = tonumber(os.getenv("CODE"))
local MAX_FRAMES   = tonumber(os.getenv("MAX_FRAMES")   or "900")
local QUIET_FRAMES = tonumber(os.getenv("QUIET_FRAMES") or "90")
local LOG_PATH     = os.getenv("LOG_PATH") or "/tmp/strider_cps1.log"
local POKE_FRAME   = 3
local IDLE         = 0xFF

local m = manager.machine
p68 = m.devices[":maincpu"].spaces["program"]
pz8 = m.devices[":audiocpu"].spaces["program"]

YM_TIMER = { [0x10]=true, [0x11]=true, [0x12]=true, [0x14]=true }
frames, poked, suppress = 0, false, false
poke_time, last_sig, nwrite, nsig = nil, nil, 0, 0
log, latch, consumed = {}, nil, nil

for _, port in pairs(m.ioport.ports) do
  for name, f in pairs(port.fields) do
    if name == "Game Mode" then f.user_value = 0 end
  end
end

local function now() return m.time:as_double() end

local function rec(kind, reg, val)
  if not poked then return end
  nwrite = nwrite + 1
  if kind == "oki" or not YM_TIMER[reg] then
    nsig = nsig + 1
    last_sig = frames
  end
  log[#log+1] = string.format("%.9f %s %d %d", now() - poke_time, kind, reg, val)
end

KEEP1 = pz8:install_write_tap(0xF000, 0xF001, "ym", function(off, data, mask)
  local v = data & 0xff
  if off == 0xF000 then latch = v
  elseif latch then rec("ym", latch, v) end
  return data end)
KEEP2 = pz8:install_write_tap(0xF002, 0xF002, "oki", function(off, data, mask)
  rec("oki", 0, data & 0xff); return data end)
KEEP3 = p68:install_write_tap(0x800180, 0x800181, "cmd", function(off, data, mask)
  if suppress and (data & 0xff) == IDLE then return CODE end
  return data end)

local function finish(why)
  local f = io.open(LOG_PATH, "w")
  f:write(string.format("# code=0x%02X frames=%d writes=%d significant=%d "
    .. "reason=%s consumed=%s\n", CODE, frames, nwrite, nsig, why,
    consumed and string.format("0x%02X", consumed) or "none"))
  f:write(table.concat(log, "\n"))
  if #log > 0 then f:write("\n") end
  f:close()
  print(string.format("code=$%02X frames=%d writes=%d sig=%d reason=%s",
    CODE, frames, nwrite, nsig, why))
  m:exit()
end

KEEP_fn = emu.add_machine_frame_notifier(function()
  frames = frames + 1
  if frames == POKE_FRAME then
    poke_time = now()
    poked = true
    suppress = true
    p68:write_u8(0x800180, CODE)
  elseif poked then
    if consumed == nil then
      local d0 = pz8:read_u8(0xD000)
      if d0 == CODE then consumed = d0 end
    end
    if frames >= MAX_FRAMES then return finish("max_frames") end
    if last_sig and (frames - last_sig) >= QUIET_FRAMES then
      return finish("quiet")
    end
    if not last_sig and (frames - POKE_FRAME) >= QUIET_FRAMES then
      return finish("never_started")
    end
  end
end)
