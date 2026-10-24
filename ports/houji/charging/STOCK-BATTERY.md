# One battery-percentage source, matching stock Android

Checked against Xiaomi's pinned Taiwan firmware
`OS3.0.303.0.WNCTWXM` (Android 16), using its extracted binaries locally.

## Stock data path

1. `/vendor/etc/init/android.hardware.health-service.qti.rc` starts
   `/vendor/bin/hw/android.hardware.health-service.qti` as the normal health HAL.
2. That binary's `main.cfi` waits for
   `/sys/class/power_supply/battery/capacity` and configures the battery monitor's
   capacity path from `%s/%s/capacity`. The charge counter and full-charge
   capacity are separately configured properties.
3. `android::BatteryMonitor::getProperty`, capacity property ID 4, reads that
   configured capacity file as an integer. It does not divide the charge
   counter by the full-charge capacity for this property.
4. Stock `qti_battery_charger.ko` maps the battery capacity property to GLINK
   `BATT_CAPACITY` (wire ID 4). Its `battery_psy_get_prop` capacity case divides
   that firmware value by 100. This case does not select a different formula
   based on whether the charger is connected. Stock has a debug fake-SOC
   override and a special zero-reading/GLINK-timeout guard; these are not
   alternate normal charging/discharging sources.
5. Stock exposes raw gauge SOC separately as Xiaomi property 195 (`fg1_rsoc`).
   The health HAL's capacity path is not that property. `xm_batt_update_work`
   polls firmware capacity and notifies the power-supply core when it changes;
   polling cadence is distinct from the source of the value.

Key disassembly checks: in the health binary, `main.cfi` sets the capacity path
at `0x13440–0x13484`, and `BatteryMonitor::getProperty` reads it for property 4
at `0x3b584–0x3b590`. In the stock module, `battery_prop_map[4]` is property 47
(capacity), and `battery_psy_get_prop` divides by 100 in that property's case.
Module addresses depend on the ELF loader's section placement; use symbols when
repeating the check. The stock firmware binaries are not redistributed here.

## Port behavior

The port now uses `BATT_CAPACITY / 100` in all states through the normal
`qcom_battmgr` capacity getter, as stock does. Steam/UPower, MangoHud and Houji
Settings all consume that same `battery/capacity` property. The former patch
`0028-houji-capacity-tracks-gauge.patch` and its state-dependent clamp tests were
removed: there is no discharge minimum latch, gauge-ratio substitution,
charging-only +3 margin, or full-status source switch.

The earlier clamp existed because firmware's smoothed capacity could lag the
raw gauge on this port after heavy discharge. Selecting the stock source removes
the port's plug-dependent calculation; it does not establish that Linux now has
stock's complete firmware update/notification behavior. Firmware can still
re-estimate or update its own value. Raw `charge_now`, `charge_full` and current
remain available for diagnosis independently of the displayed percentage.

## Observed firmware disagreement while charging (2026-10-18)

Matching the stock capacity getter did not resolve the underlying discrepancy.
After sustained gaming, Linux reported 0%, about 3.04 V and zero charge counter.
After load removal with external power attached, the main percentage jumped to
60% even though the counter remained near zero. It later declined to 56% while
the separate raw gauge and charge counter rose normally during charging.

A bounded read-only trace of incoming GLINK replies confirmed successful
property-4 values of `5600` while Xiaomi property 195 rose from 11 to 17.
Linux's 56% therefore reflected what firmware actually returned, not a Steam
display-only error. Later the raw gauge reported 29% and the charge counter
1.113 Ah against 3.847 Ah full capacity (28.9%), while property 4 still reported
56%. No unplug/replug test or charging-control override was used for this trace.

The behavior is consistent with divergent/stale firmware UI-SOC estimation,
but its exact state machine and whether missing stock control traffic causes
it are unresolved. Neither matching this getter nor replacing it with a clamp
proves stock-equivalent battery behavior. Until fixed, the displayed percentage
must not be relied on as a remaining-energy measure during such an incident.

Continued connected charging subsequently brought the readings together without
a driver change or reset: main 65% / raw 63%, then main 86% / raw 84% (counter
ratio 83.3%). A separate bounded trace of outgoing thermal/current writes and
incoming SOC replies did not observe a host capacity setter in its 60-second
window. This does not rule out other state interactions outside that window.

The stock ADSP image includes explicit SOC-smoothing diagnostics referring to
charger attachment, charging-status transitions, raw versus system SOC, and
saved raw/UI SOC at charger removal. The exact executed branch remains unknown.
The stock Android driver additionally polls Xiaomi property 116 and forwards
panel blank state through Xiaomi property 117, alongside USB liquid-port
detection control. These were not proven to cause the drift; the section below
records how the port now matches them.

## Stock battery state traffic (2026-10-19)

Stock `xm_batt_update_work` re-reads capacity (property 4) and Xiaomi property
116 (`THERMAL_TEMP`; it tracked the battery temperature in deci-°C on the phone,
225 against 224 from `battery/temp`). It reschedules every 5 s with USB online
and the display on, otherwise 8 s below 15%, 10 s above, or 20 s with the display
off.

Stock's panel notifier keeps one flag per panel. The enum is in the BTF of stock
`qti_amoled_ecm.ko`: `DRM_PANEL_EVENT_NONE=0, BLANK=1, UNBLANK=2, BLANK_LP=3`.
`screen_state_for_charge_callback` sets the flag to 1 on BLANK, 0 on UNBLANK, and
ignores everything else. `usb_chg_lpd_check_work` ANDs the primary and secondary
flags and logs `blank_state = %d, enable lpd = %d`. It writes the flag to Xiaomi
property 117 (`FB_BLANK_STATE`: 1 means blank, 0 means on) and also sets USB
moisture detection (USB property 8) to the opposite value. The ADSP has a
matching `FB_BLANK_SET: %d` handler.

Port behavior: patch `0034` exposes `thermal_temp` (116, read-only) and
`fb_blank_state` (117, boolean) in the `houji_charger` attribute group. The
native `houji-charge-policy` (`helpers/stock-battery-state.hpp`) takes the same
backlight state it already uses for charging decisions and:

- writes 1 or 0 to `fb_blank_state` whenever the display state changes;
- reads it back at each poll and rewrites it if firmware disagrees, so a service
  restart or an ADSP restart does not leave stale display state;
- re-reads `capacity` and `thermal_temp` at stock's interval;
- reports a failure without stopping the current-lease renewal and other charging
  decisions, and does nothing on a kernel without the two attributes.

The USB moisture-detection toggle is not ported: Type-C and liquid detection
have their own owner on this port. Capacity is still `BATT_CAPACITY / 100`.

Live check at 00:19–00:21 UTC+8, kernel `7.2.6-armada-houji-k20261010`, charger
connected at 100%: after the module reload and helper restart, a GLINK trace
showed GET 117 and GET 116 every 5 s, both with result 0 (116 returned 225). I
forced property 117 to 1 by hand; the helper's next poll read 1, wrote 0, and
the node read 0 again. At 00:23 I also set the panel's `bl_power` to blank and back
(restored from a script's `finally`): property 117 read 1 within a second of
blanking and 0 within a second of restoring. The logic is also covered by
`helpers/test-stock-battery-state.py`.

This matches stock's traffic. It is **not** shown to remove the disagreement
between the main percentage and the raw gauge. That needs a discharge under load
and a plug-in on a depleted battery; until then it is unproven.
