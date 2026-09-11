-- Boot a patched capture ROM, let the game initialise (VDP regs, VBlank IRQ,
-- Z80 DAC blob upload), then genuinely freeze it and save the state that the
-- per-sound capture runs load.
--
-- Freezing works by forcing GAME_MODE out of range so the relocated main loop
-- at FREEZE_LOOP takes its "frozen" branch. The catch: several mode handlers
-- (notably mode 4, the attract demo) run their whole sequence internally and
-- only return to the main loop occasionally, so writing GAME_MODE does not
-- take effect immediately. We therefore keep asserting it and wait for direct
-- evidence that the relocated loop is executing -- a read of GAME_MODE issued
-- from inside the loop itself -- before saving. Merely seeing the loop once is
-- not enough (a handler may return briefly and dive back in), so we require it
-- to be spinning right now: SPIN_MIN reads per frame from inside the loop, for
-- CONFIRM consecutive frames.
local FREEZE_AT     = tonumber(os.getenv("FREEZE_AT") or "600")
local CONFIRM       = tonumber(os.getenv("CONFIRM") or "60")
local SPIN_MIN      = tonumber(os.getenv("SPIN_MIN") or "200")
local GAME_MODE     = 0xFFE100
local FREEZE_VAL    = 0x00FF
local FREEZE_LOOP   = 0x0B7800
local STATE_NAME    = os.getenv("STATE_NAME") or "clean"

local m   = manager.machine
local cpu = m.devices[":maincpu"]
p68 = cpu.spaces["program"]
frames, loop_hits, hits_this_frame, spinning, saved = 0, 0, 0, 0, false

KEEP_r = p68:install_read_tap(GAME_MODE, GAME_MODE + 1, "mode",
  function(off, data, mask)
    local pc = cpu.state["PC"].value
    if pc >= FREEZE_LOOP and pc <= FREEZE_LOOP + 8 then
      loop_hits = loop_hits + 1
      hits_this_frame = hits_this_frame + 1
    end
    return data
  end)

KEEP_fn = emu.add_machine_frame_notifier(function()
  frames = frames + 1
  if saved then return end
  local hits = hits_this_frame
  hits_this_frame = 0
  if frames < FREEZE_AT then return end
  p68:write_u16(GAME_MODE, FREEZE_VAL)
  if hits >= SPIN_MIN then spinning = spinning + 1 else spinning = 0 end
  if spinning >= CONFIRM then
    print(string.format(
      "f%d: FROZEN - relocated loop at $%06X is spinning (%d reads of $%06X "
      .. "from inside it last frame, %d frames in a row, %d total); "
      .. "mode=$%04X SR=%04X",
      frames, FREEZE_LOOP, hits, GAME_MODE, spinning, loop_hits,
      p68:read_u16(GAME_MODE), cpu.state["SR"].value))
    m:save(STATE_NAME)
    saved = true
    KEEP_fn2 = emu.add_machine_frame_notifier(function()
      print("state '" .. STATE_NAME .. "' saved; exiting")
      m:exit()
    end)
  elseif frames % 600 == 0 then
    print(string.format("f%d: waiting for the main loop to spin "
      .. "(hits last frame=%d, total=%d) ...", frames, hits, loop_hits))
  end
end)
