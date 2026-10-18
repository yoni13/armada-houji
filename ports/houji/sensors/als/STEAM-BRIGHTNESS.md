# Steam automatic brightness on Houji

## Verified implementation (2026-10-18)

Inspected the handset's loaded `steamrtarm64/steamclient.so` (SHA-256
`8dec8579abad65d000079f146aee5f42c12a4bd9ca7a24051b691813cced11dc`) and queried
the running client's settings through its local debugging interface. Steam is
proprietary; these are observations of this version, not a stable public API.
The separate `linuxarm64/steamclient.so` depot copy was initially inspected too;
the process maps identify the `steamrtarm64` binary as the one actually in use.
Its adaptive-bound and filtering behavior was checked separately and matches.

### Sensor and sampling

Steam discovers supported IIO devices (`ltrf216a`, `opt3001`, or generic `als`).
Houji supplies the generic `als` device; `in_illuminance_raw` is interpreted as
lux with gain 1.0. This path does not consume SensorProxy's D-Bus light level.
SteamOS management mode and nonzero `STEAM_ENABLE_DYNAMIC_BACKLIGHT` make the
feature available. `AdaptiveBrightnessEnabled` is a separate user setting.

`CClientJobCollectAmbientLightLevel` samples asynchronously and enforces a
minimum 100 ms collection-job interval; actual frequency also depends on the
system-manager scheduler. Earlier handset observation was about five reads/s.
The code supports two sensors, takes the higher of their latest readings, keeps
up to 32 timestamped samples, and computes min/median/max over the latest two
seconds. The median is published after more than four recent samples.

### It constrains brightness; it does not always chase lux

The adaptive path computes a **minimum and maximum allowed normalized backlight**
and only adjusts the user's chosen brightness when it lies outside that range.
The breakpoints recovered from the ARM64 client are:

| Median lux | Minimum backlight | Maximum backlight |
| --- | ---: | ---: |
| 0 | 0 / 65535 (0%) | 22000 / 65535 (33.57%) |
| 50 | 4000 / 65535 (6.10%) | 33000 / 65535 (50.35%) |
| 100 and above | 10000 / 65535 (15.26%) | 65535 / 65535 (100%) |

Between breakpoints it interpolates linearly. The target values are rounded to
percentage steps and approached with a 0.025-new / 0.975-old filter until within
small thresholds (0.003 for the lower bound, 0.01 for the upper). Blending toward
a bound uses logarithmic brightness space. Toggling adaptive mode blends over
0.25 seconds. Manual slider changes have a separate temporary override path;
the code can retain a manual selection outside adaptive bounds until re-enabled
or reset on resume. The UI slider itself has a nonlinear/logarithmic mapping,
so its position is not simply the raw backlight percentage.

For the generic unidentified-display profile seen on this phone, slider
breakpoints are 0, 0.5, 0.9 and 1.0. They map through log2-brightness points
approximately -6.64386, -1.75, -0.25 and 0, with linear interpolation between
those logarithms and exponentiation afterward. Thus slider 0.5233 corresponds
to approximately 0.3159 normalized backlight—matching the observed 646/2047.
Other display profiles can have different calibration/overdrive parameters.

There is also a bright-environment minimum floor of 5%, with hysteresis: an
upward transition above 1125 lux and a downward transition below 500 lux around
a 750-lux reference, fading over 0.5 seconds. This is a separate low-brightness
guard, not an instruction to go to maximum brightness under a flashlight.

**Consequence:** at 400 lux, a chosen 32% raw backlight is already inside the
roughly 15–100% allowed range. Raising the reading to thousands of lux need not
change it. That explains why a flashlight test alone cannot establish that
adaptive brightness is broken.

### Live read-only observations

- The IIO sensor was actively read and reported about 385 lux during inspection.
- The panel reported `brightness=actual_brightness=646`, `max_brightness=2047`:
  about 31.56% raw output.
- Steam's settings callback later reported both primary and median light around
  683 lux, raw backlight 646, adaptive minimum 0.15 and maximum 1.0.
- Both adaptive availability and enabled flags were set. The same raw brightness
  persisted because it was inside those bounds.
- Steam's brightness callback reported a UI slider value around 0.5233, distinct
  from raw output, consistent with the nonlinear mapping.

The settings callback is a protobuf ArrayBuffer (`RegisterForSettingsChanges`):
fields 6/7 are adaptive available/enabled, 21/22 primary/median lux, 23 raw
backlight, 24/25 adaptive min/max, and 33 alternate lux in this build. Avoid
hard-coding this private schema in production code.

### Hardware write path

The loaded client enumerates `/sys/class/backlight/`, reads `max_brightness`,
and resolves the selected device's `brightness` file. For an unidentified display
such as Houji's earlier EDID, the generic backend uses unit gain/exponent. The
display manager combines adaptive limits, manual preference, idle dimming and
resume fades, then maps normalized brightness to an integer within the device
range. It formats that integer and writes it to the sysfs brightness path.
The write helper first tries an ordinary user write; if that fails in SteamOS
mode it tries `steamos-polkit-helpers/steamos-priv-write`, then its legacy sudo
fallback. This makes file permissions another distinct failure point to inspect.
The alternative `/dev/drm_dp_aux0` path is for supported Deck OLED display types;
it is not required for this generic panel.

Armada's `backlight-acl` gives the user write permission through the wheel group.
SensorProxy provides the sensor feed indirectly through Houji's bridge, but is
not the brightness controller in Game Mode.

## Code locations for repeat inspection

Loaded `steamrtarm64` image-relative addresses:

- `0xD169C0`: collection job.
- `0xD0A5F8`: two-sensor maximum, rolling two-second median, bright-light floor.
- `0xD0C268`: lux breakpoints and filtered adaptive bounds.
- `0xD0AE38`: apply adaptive bounds to the user's chosen brightness.
- `0xD14BC0`: combine adaptive adjustment, idle dimming and resume fades, then
  call the hardware backlight writer when the result changes.
- `0xED1C18`, `0xED1ED8`, `0xED5410`: backlight discovery/max/initialization.
- `0xED21C8`, `0x193EBC0`: map output to hardware range and format/write it.
- `0x193E7A8`: direct user write and SteamOS privileged fallback.

## Remaining validation

The read path, filtering, bounds, output path and live settings are established. This
investigation did not force the sensor value, move the user's slider, or claim
a calibrated lux-to-nits curve for the N3 panel. The write path was traced
statically, not by inducing a hardware brightness change. A controlled
dark-room test with a selected brightness above the dark maximum would exercise
actual adaptive dimming more meaningfully than a flashlight at medium brightness.
