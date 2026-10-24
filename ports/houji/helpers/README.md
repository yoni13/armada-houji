# Small resident native helpers

These C++17 services replace resident Python interpreters without replacing
the underlying services or protocols:

- `houji-steam-power`: reads UPower over D-Bus and publishes `/run/vpower`.
  Readiness follows the first complete snapshot; unknown time remains `-1`,
  never a zero shutdown request. Lost telemetry clears old estimates, retains
  the last valid percentage, and retries. Unchanged files are not rewritten.
- `houji-gamescope-orientation`: preserves the rotation lock, 750 ms sensor
  debounce, sensor claim/release, owner changes and compositor/socket restart
  detection. The existing native Wayland rotation helper performs each change.
- `houji-x2s-session`: detects the user's actual compositor executable, gives
  Plasma priority over nested Gamescope, and applies the existing InputPlumber
  profile only at session changes. Custom overrides are preserved. Profile
  publication precedes controller rediscovery.
- `houji-gyro`: calls libssc directly, reusing sensor objects across close/open
  cycles. It preserves 200 Hz gyro / 50 Hz accelerometer sampling, rotation,
  Steam Deck units and InputPlumber's connected-controller/gamepad-target gate.
  Sensor subscriptions and the virtual IMU are released while off or waiting.
- `houji-thermal`: preserves the stock skin formula, CPU frequency steps,
  thermal and low-battery hysteresis, paused-core ownership and stronger
  kernel GPU cooling limits. Missing readings hold the previous caps.
- `houji-charge-policy`: preserves authentication attempts, conservative fallback,
  current ramp, kernel lease renewal, wired/wireless thermal votes, charge-limit
  hysteresis and the charger/light-sleep query. The stock authentication binary
  and kernel current guard still make the same independent checks. It also
  mirrors stock Android's battery-state traffic: the display's blank state is
  written to Xiaomi property 117 (and read back), and capacity plus property 116
  are polled at stock's interval. A failure there is logged and does not stop
  lease renewal; kernels without the attributes are tolerated. See
  [stock battery reporting](../charging/STOCK-BATTERY.md).
- `license-relay`: strict QRTR/QMI framing and the existing native QTEE ABI.
  License decisions remain with stock QTEE; malformed packets do not reach it.
  The service publishes readiness only after secure storage is ready, and never
  logs request payloads or license identifiers.
- `cellular-control`: Polkit-authorized SIM/eSIM settings broker. It preserves
  the `org.armada.Cellular1` Status/Run interface, serialization, request
  validation, activation-code input through stdin, filtered profile results,
  and ModemManager restoration after failures/timeouts or graceful shutdown.
  SIM/lpac subprocesses run only for explicit operations; they are not resident.

The Python implementations remain as executable reference/rollback sources.
`stage-root.py` installs the native binaries at their existing runtime paths;
their units execute them directly. The diagnostic collector and desktop
power-profile adapter retain their Python implementations.

## Build and verification

```sh
python3 ports/houji/helpers/build.py --sysroot WORK/userspace/sysroot \
  --output WORK/userspace/helpers
python3 ports/houji/helpers/test-native.py
python3 ports/houji/helpers/test-policy-native.py
python3 ports/houji/helpers/test-stock-battery-state.py
python3 ports/houji/helpers/test-license-native.py
python3 ports/houji/helpers/test-cellular-native.py
```

The test needs a host C++ compiler, make, pkg-config, GLib/GIO development
libraries, nlohmann-json development headers, Python GI and dbus-daemon. It launches the actual binaries against
a private D-Bus with simulated UPower/SensorProxy services and temporary
process/config trees. It exercises all battery states, invalid/missing telemetry,
the shutdown sentinel, readiness, provider conflicts, exact X2s profiles,
custom-config refusal, rotation locks, sensor restart and compositor replacement.
No host or handset system service is changed by the test. CLI path/bus options
allow this isolation; normal units use the fixed production defaults.

The second set compares thousands of thermal/charging trajectories and gyro
samples directly against the Python reference models, plus temporary-sysfs
control tests. The relay tests use a fake QTEE ABI and local datagram sockets
to verify byte-exact replies, malformed-frame rejection, retry behavior and
oversized-response rejection without contacting a modem or secure element.
The cellular tests use a private Polkit service and fake command binaries,
including authorization rejection, concurrent and cross-process locking,
failed SIM selection, download timeout, notification failure and cancellation.
The cross-build downloads the version/hash-pinned JSON header in `sources.json`;
its MIT license ships with the image.

Child helpers are started without a shell, in a separate process group, with
bounded timeouts. `posix_spawnp` avoids unsafe work in a forked GLib process.
Polling intervals remain two seconds (battery), half a second (orientation)
and one second (session routing).

## Initial handset memory comparison

Measured on 2026-10-18, same boot, using `/proc/PID/smaps_rollup`:

| Helper | Python RSS | Native RSS | Python PSS | Native PSS |
| --- | ---: | ---: | ---: | ---: |
| Steam battery telemetry | 33.7 MiB | 11.1 MiB | 21.6 MiB | 5.0 MiB |
| Gamescope orientation (locked) | 30.5 MiB | 5.9 MiB | 19.0 MiB | 0.9 MiB |
| X2s session routing | 13.4 MiB | 3.7 MiB | 8.0 MiB | 0.6 MiB |

Combined proportional memory fell from 48.6 MiB to 6.1–6.5 MiB in subsequent
samples (42–43 MiB saved; shared-page proportions vary). RSS includes shared
pages and cannot be added as unique physical savings. Sensor-following mode may
use more memory than locked orientation because it opens a D-Bus connection.

Separately, the parked FSR4 Decky plugin was moved outside Decky's scanned
plugin directory. Disabling its upscaler toggle alone still spawned its FEX/
Python process (82.1 MiB RSS, 53.4 MiB PSS in the initial sample). Sources,
settings and assets are retained; see [FSR4](../npu/fsr4/README.md).

## Active gyro, charging, thermal and license relay

A subsequent same-boot comparison, with gyro actively streaming:

| Helper | Python RSS | Native RSS | Python PSS | Native PSS |
| --- | ---: | ---: | ---: | ---: |
| Gyro aiming | 36.8 MiB | 15.2 MiB | 22.6 MiB | 7.4 MiB |
| Charging policy | 14.3 MiB | 3.6 MiB | 8.8 MiB | 0.5 MiB |
| Thermal policy | 14.1 MiB | 3.4 MiB | 8.8 MiB | 0.5 MiB |
| Modem license relay | 11.8 MiB | 3.1 MiB | 7.0 MiB | 0.4 MiB |

These four saved about 38 MiB PSS. Ten-second CPU samples showed charging
1.57% → 0.69% and thermal 0.88% → 0.39% of one core. Gyro remained around
10% (10.01% → 9.65%); this does not establish a meaningful gyro CPU reduction.
The sensor/transport workload remains active at the same sampling rates.

NFC Manager retains its Python protocol implementation and now exits when fully
off and idle for at least 30 seconds (checked every 30 seconds). D-Bus actions
reactivate it. Passive UI/status polling does not repeatedly activate it.
Reader, emulation, iPASS, recovery and in-flight operations prevent idle exit.
Active iPASS still requires its resident manager, so no memory saving is claimed
for that mode. The handset's saved iPASS mode was restored after installation
with byte-identical settings.

## SIM/eSIM settings broker

Replaced `/usr/libexec/houji-cellular/service.py` as the service entry point with
`/usr/libexec/houji-cellular/cellular-control`. The Python file remains as a
reference. In a status-only same-boot comparison, RSS fell from 35.2 to 10.9 MiB
and PSS from 22.9 to 4.9 MiB (about 18 MiB PSS saved). Deployment did not change
the saved SIM selection or any modem, storage, QTEE or license-service invocation.
Mutating eSIM operations were validated with synthetic inputs and fake tools;
no live profile was downloaded, enabled, disabled or deleted for verification.
