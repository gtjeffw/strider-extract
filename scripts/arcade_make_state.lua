-- Boot Strider CPS-1 into its TEST screen and save a clean, silent state.
--
-- The "Game Mode" dipswitch (DSWC bit 7) selects Test. On that screen the game
-- issues no sound commands at all - only the periodic idle $FF, which the Z80
-- driver dispatches to $0394 = RET. Measured: 0 real commands, 0 OKI writes,
-- and exactly zero audio output (peak 0, single distinct sample value) over
-- 30 s. So unlike the Mega Drive version, no ROM patching is needed.
--
-- "Silent" is verified rather than assumed before the state is saved.
local SETTLE      = tonumber(os.getenv("SETTLE") or "1200")
local CONFIRM     = tonumber(os.getenv("CONFIRM") or "300")
local STATE_NAME  = os.getenv("STATE_NAME") or "arc_clean"

local m = manager.machine
p68 = m.devices[":maincpu"].spaces["program"]
pz8 = m.devices[":audiocpu"].spaces["program"]

frames, sig, oki, realcmd, quiet, saved = 0, 0, 0, 0, 0, false
YM_TIMER = { [0x10]=true, [0x11]=true, [0x12]=true, [0x14]=true }
latch = nil

for _, port in pairs(m.ioport.ports) do
  for name, f in pairs(port.fields) do
    if name == "Game Mode" then f.user_value = 0; print("Game Mode -> Test") end
  end
end

KEEP1 = pz8:install_write_tap(0xF000, 0xF001, "ym", function(off, data, mask)
  if off == 0xF000 then latch = data & 0xff
  elseif latch and not YM_TIMER[latch] then sig = sig + 1 end
  return data end)
KEEP2 = pz8:install_write_tap(0xF002, 0xF002, "oki", function(o,d,k) oki=oki+1; return d end)
KEEP3 = p68:install_write_tap(0x800180, 0x800181, "cmd", function(off, data, mask)
  if (data & 0xff) ~= 0xFF then realcmd = realcmd + 1 end
  return data end)

KEEP_fn = emu.add_machine_frame_notifier(function()
  frames = frames + 1
  if saved or frames < SETTLE then return end
  if frames == SETTLE then sig, oki, realcmd = 0, 0, 0 end
  if sig == 0 and oki == 0 and realcmd == 0 then quiet = quiet + 1
  else
    quiet = 0; sig, oki, realcmd = 0, 0, 0
  end
  if quiet >= CONFIRM then
    print(string.format("f%d: SILENT for %d consecutive frames "
      .. "(0 significant YM writes, 0 OKI writes, 0 real sound commands); "
      .. "Z80 $D000=$%02X", frames, quiet, pz8:read_u8(0xD000)))
    m:save(STATE_NAME)
    saved = true
    KEEP_fn2 = emu.add_machine_frame_notifier(function()
      print("state '" .. STATE_NAME .. "' saved; exiting"); m:exit()
    end)
  end
end)
