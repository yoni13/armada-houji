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
