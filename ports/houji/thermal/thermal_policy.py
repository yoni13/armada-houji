"""Houji CPU/GPU thermal policy, modelled on HyperOS mi_thermald.

This module performs no device I/O. It turns the virtual skin temperature
(board_thermal.py) and the battery level into CPU frequency caps, a GPU
level and paused CPUs.

Sources: mi_thermald from OS3.0.303.0.WNCTWXM (timer_expires 0xa6298 for
"ss", timer_expires_1 0xa7138 for "monitor"), thermal-normal.conf,
thermal-mgame.conf and thermald-devices.conf, and the stock KGSL GPU level
table selected for feature code 2 (msm_kgsl.ko adreno_device_probe).

Plasma uses the stock normal profile. Game Mode keeps the normal CPU steps but
leaves the GPU unlimited until the stock game-profile trip, so games keep
graphics performance and lose CPU speed first.
"""

# KGSL power levels: pwrlevels-3 matches feature code 2 (sku-codes <0>).
STOCK_GPU_MHZ = (903, 834, 770, 720, 680, 629, 578, 500, 422, 366, 310, 231)

# Ordered by threshold; "ss" uses triggers only.
NORMAL_CPU = {
    0: ((25000, 39000, 40000, 45000, 48000),
        (2035200, 1689600, 1459200, 1344000, 1017600)),
    2: ((25000, 34000, 37000, 39000, 41000, 42000, 43000, 44000, 45000, 46000,
         47000, 48000, 49000, 50000),
        (2630400, 2323200, 2188800, 2035200, 1824000, 1612800, 1612800, 1286400,
         1286400, 1286400, 1190400, 1190400, 1075200, 960000)),
    5: ((25000, 34000, 37000, 39000, 41000, 41500, 42000, 43000, 45000, 46000,
         47000, 48000, 49000, 50000),
        (2630400, 2323200, 2188800, 1824000, 1497600, 1401600, 1286400, 1286400,
         1286400, 1286400, 1190400, 1190400, 1075200, 960000)),
    7: ((25000, 32000, 34000, 37000, 39000, 40000, 41000, 41500, 42000, 43000,
         44000, 45000, 46000, 47000, 48000, 50000),
        (2630400, 2169600, 1939200, 1824000, 1708800, 1363200, 1248000, 1248000,
         1248000, 1248000, 1248000, 1132800, 1132800, 1017600, 1017600, 1017600)),
}

# (trigger, clear, targets); targets are GPU levels.
NORMAL_GPU = ((15000,), (13000,), (2,))
# Game Mode: the stock game trip (46/44 C) and boost limit (48/45 C).
GAME_GPU = ((46000, 48000), (44000, 45000), (2, 3))

# Both stock profiles pause cpu7, cpu3 and cpu4 at 50 C skin.
CORE_PAUSE = ((50000,), (48000,), (frozenset({3, 4, 7}),))

# Battery level (reversed): CPU caps, GPU level and paused CPUs.
NORMAL_LOW_BATTERY = ((1, 3), (2, 4), (
    ({0: 787200, 2: 844800, 5: 1286400}, 0, frozenset({3, 4, 7})),
    ({0: 2265600, 2: 1286400, 5: 1286400}, 0, frozenset({4, 7})),
))
GAME_LOW_BATTERY = ((1, 3), (2, 4), (
    ({0: 1017600, 2: 1497600, 5: 1497600}, 6, frozenset({2, 3, 4, 7})),
    ({0: 2265600, 2: 1286400, 5: 1286400}, 3, frozenset({4, 7})),
))


class Monitor:
    """mi_thermald "monitor": per-threshold hysteresis, contiguous levels."""

    def __init__(self, trigger, clear, targets, reverse=False):
        if not len(trigger) == len(clear) == len(targets) or not trigger:
            raise ValueError('invalid monitor table')
        for t, c in zip(trigger, clear):
            if (c <= t) if reverse else (c >= t):
                raise ValueError('invalid monitor hysteresis')
        self.trigger, self.clear, self.targets = trigger, clear, targets
        self.reverse = reverse
        self.active = [False] * len(trigger)

    def update(self, value):
        """Return the active target, or None when no threshold applies."""
        n = len(self.trigger)
        if self.reverse:
            for i in range(n):
                if value <= self.trigger[i]:
                    self.active[i] = True
                if value >= self.clear[i]:
                    self.active[i] = False
            # The trailing run of active thresholds starts at the level.
            level = 0
            for i in range(n - 1, -1, -1):
                if not self.active[i]:
                    level = i + 1
                    break
        else:
            for i in range(n - 1, -1, -1):
                if value >= self.trigger[i]:
                    self.active[i] = True
                if value <= self.clear[i]:
                    self.active[i] = False
            # The leading run of active thresholds ends at the level.
            level = next((i for i, a in enumerate(self.active) if not a), n) - 1
        return self.targets[level] if 0 <= level < n else None


def ss_target(trigger, targets, value):
    """mi_thermald "ss": the highest trigger reached selects the cap."""
    level = -1
    for i, t in enumerate(trigger):
        if value >= t:
            level = i
    return targets[level] if level >= 0 else None


def cap_state(freqs, target):
    """Cooling state that caps a frequency table at target (None: no cap)."""
    if target is None:
        return 0
    return min(sum(1 for f in freqs if f > target), len(freqs) - 1)


def gpu_state(freqs, level):
    """Map a stock KGSL level onto the Linux devfreq cooling states."""
    if not level:
        return 0
    level = min(level, len(STOCK_GPU_MHZ) - 1)
    return cap_state(freqs, STOCK_GPU_MHZ[level] * 1_000_000)


def step_towards(current, target):
    """Stock ss moves one frequency-table entry per poll."""
    if current < target:
        return current + 1
    if current > target:
        return current - 1
    return current


class Policy:
    """Combine the stock sections like select_device_target does: the lowest
    CPU frequency, the highest GPU level and every paused CPU win."""

    def __init__(self, game=False):
        self.game = game
        self.gpu = Monitor(*(GAME_GPU if game else NORMAL_GPU))
        self.pause = Monitor(*CORE_PAUSE)
        self.battery = Monitor(*(GAME_LOW_BATTERY if game else NORMAL_LOW_BATTERY), reverse=True)

    def update(self, skin, battery):
        if not isinstance(skin, int) or not -20_000 <= skin <= 100_000:
            raise ValueError('implausible skin temperature')
        if not isinstance(battery, int) or not 0 <= battery <= 100:
            raise ValueError('implausible battery level')
        caps = {cpu: ss_target(trig, targets, skin) for cpu, (trig, targets) in NORMAL_CPU.items()}
        gpu = self.gpu.update(skin) or 0
        paused = set(self.pause.update(skin) or ())
        low = self.battery.update(battery)
        if low:
            low_caps, low_gpu, low_paused = low
            for cpu, freq in low_caps.items():
                caps[cpu] = freq if caps[cpu] is None else min(caps[cpu], freq)
            gpu = max(gpu, low_gpu)
            paused |= low_paused
        return caps, gpu, paused
