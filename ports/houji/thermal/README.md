# Houji thermal policy

`helpers/thermal.cpp` is the installed native daemon. `thermal_policy.py` and
`houji-thermal` are the Python reference/rollback implementation. Both accept
`--game-policy legacy|balanced` and `--exit-caps hold|release`.
The installed service selects `balanced` for Game Mode; Desktop Mode keeps
the normal CPU/GPU table. Omitting the option selects `legacy` for rollback.

The virtual skin estimate remains Xiaomi's calibrated weighted board-sensor
formula. CPU die temperature is not substituted for a board thermistor.
Frequency ceilings use kernel cooling-device votes, independently of Steam's
power-profile votes. The lower ceiling wins. Core-pause and low-battery rules
remain active, and the kernel's stronger GPU cooling vote takes precedence.

## CPU/GPU balance

The original (`legacy`) Game Mode policy combines Xiaomi's normal CPU table
with a relaxed GPU ceiling. At 45°C estimated skin, that allows the full GPU
clock while limiting the prime CPU to 1132.8 MHz. Armada's generic Performance
profile also sets its GPU minimum equal to maximum. This gives a CPU-limited
game little benefit from the GPU power being spent.

Houji's image stager changes the **factory Performance GPU minimum to zero**
(lowest supported OPP), enabling normal `simple_ondemand` scaling. Maximum GPU
frequency, manual GPU selection and owner `/etc/armada/power-profiles.conf`
overrides still take their existing paths. The change is specific to the Houji
staged image; the shared Armada profile file is not changed for other devices.

The `balanced` policy moves some thermal budget from GPU to CPU below 46°C:

| Skin threshold | Little cap | Big CPU2 cap | Big CPU5 cap | Prime cap | GPU cap on warming |
| --- | ---: | ---: | ---: | ---: | ---: |
| 39°C | 1689.6 MHz | 2323.2 MHz | 2188.8 MHz | 2438.4 MHz | 720 MHz |
| 41°C | 1459.2 MHz | 2131.2 MHz | 1920 MHz | 2246.4 MHz | 720 MHz |
| 42°C | 1459.2 MHz | 2131.2 MHz | 1920 MHz | 2246.4 MHz | 680 MHz |
| 43°C | 1459.2 MHz | 1920 MHz | 1708.8 MHz | 2035.2 MHz | 680 MHz |
| 44°C | 1459.2 MHz | 1824 MHz | 1612.8 MHz | 1939.2 MHz | 629 MHz |
| 45°C | 1344 MHz | 1612.8 MHz | 1497.6 MHz | 1708.8 MHz | 629 MHz |
| 46°C | 1344 MHz | 1286.4 MHz | 1286.4 MHz | 1132.8 MHz | 578 MHz |
| 48°C | 1017.6 MHz | 1190.4 MHz | 1190.4 MHz | 1017.6 MHz | 500 MHz |

CPU targets at/above 46°C are the same as the previous policy; the GPU caps are
stricter. CPU changes still walk one cooling state per poll, and GPU thresholds
clear 2°C below their triggers. This is a Houji tuning curve, not a claim to
reproduce Android's gaming framework. Stock `thermal-mgame.conf` uses a 720 MHz
GPU cap and sharp CPU reduction at 46°C, while `thermal-hp-mgame.conf` uses a
629 MHz GPU cap and different CPU steps.

## Service handover

The daemon now **holds CPU cooling votes on exit by default**. Previously,
stopping it dropped every CPU vote to zero. A live restart with Performance
selected briefly sent all clusters to maximum before the replacement read
its first sensors; the bounded test observed a 95°C die sample and aborted.
The replacement can adjust retained CPU votes immediately after valid telemetry.
Missing telemetry retains them, including on startup after a restart.

For deliberate service removal, stop the daemon then run its one-shot release:

```sh
sudo systemctl stop houji-thermal
sudo /usr/libexec/armada/houji-thermal --once --exit-caps release
```

This explicitly releases the CPU votes instead of treating every restart as
service removal. Stronger kernel-owned GPU votes are preserved. Transitioning
from an older binary that still releases CPU votes requires temporary independent
power-profile CPU ceilings across the first restart.

## Tests

```sh
python3 ports/houji/thermal/test-thermal-policy.py
python3 ports/houji/helpers/test-policy-native.py
```

Tests sweep heating/cooling thresholds, GPU hysteresis, low-battery levels and
random trajectories against the Python model. Runtime fixtures cover missing
telemetry, stronger kernel GPU votes, retained CPU ceilings on stop/restart,
and explicit release. Live runs retain temperatures, actual frequencies and
MangoHud frame times privately; results belong in `HISTORY.md` after completion.

## Measured tradeoff (October 18, 2026, Taiwan UTC+8)

GTA V Enhanced stayed in the same stationary outdoor view for sequential tests.
All FPS rows are 60-second captures. The revised policy ran for twelve minutes
with five-second board/die readings; this is not an hour-long endurance result.

| Configuration | Average FPS | MangoHud 1% minimum |
| --- | ---: | ---: |
| Original CPU/GPU policy and fixed Performance GPU floor | 32.1 | 20.5 |
| Original thermal policy, GPU floor removed | 32.4 | 20.9 |
| Balanced thermal policy, after three minutes | 37.0 | 23.5 |
| Balanced thermal policy, after ten minutes | 38.7 | 26.2 |
| Return to original policy/floor | 35.0 | 21.9 |
| Installed balanced policy, confirmation capture | 39.9 | 25.7 |

During the balanced run, estimated skin averaged 45.10°C, ranged 44.66–45.33°C,
and battery temperature averaged 38.60°C. Maximum sampled die temperature was
85.3°C early and 79.5°C over the last three minutes. Late caps were 1612.8/1497.6
MHz on the big clusters, 1708.8 MHz on prime, GPU 629 MHz. This improved the
tested CPU-bound workload, but it trades away GPU peak clock at warm skin
temperatures and may reduce performance in GPU-bound games.

Net battery discharge was about 5.2 W late in the balanced run versus about
4.3 W during the GPU-scaling-only trial. USB was supplying only about 2.2 W;
the battery's `Charging` label did not mean it was gaining charge. These are
net battery readings, not total system power. The A/B/A ordering and changing
game simulation prevent claiming a universal percentage uplift or 60 FPS.
The installed daemon was also restarted under load: 143 samples over three
seconds showed no CPU-cap increase above the pre-restart values, confirming
the retained-vote handover fix on the handset.

Rollback: restore the original files retained in
`/var/lib/armada/thermal-before-20261018/`, or select `--game-policy legacy`
in the thermal service while retaining the CPU-vote handover repair. Restore
the factory power-profile backup if the previous fixed GPU floor is wanted.
