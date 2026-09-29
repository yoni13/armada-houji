"""Pure models of the Houji normal-mode thermal tables recovered from HyperOS.

This module performs no device I/O. Its high-current SIC output is a stock
thermal vote, not permission to apply that current. The running charge policy
also enforces authentication, telemetry freshness and a staged current ceiling.

Sources: mi_thermald timer_expires_2 (0xa7d64), generic_set_level (0xa41c8),
thermal-normal.conf and thermald-devices.conf from OS3.0.303.0.WNCTWXM.
"""


def trunc_div(numerator, denominator):
    """Match signed C integer division, including negative control deltas."""
    return (1 if numerator >= 0 else -1) * (abs(numerator) // denominator)


class Hysteresis:
    def __init__(self, trigger, clear):
        if len(trigger) != len(clear) or any(c >= t for c, t in zip(clear, trigger)):
            raise ValueError('invalid hysteresis table')
        self.trigger, self.clear = tuple(trigger), tuple(clear)
        self.active = [False] * len(trigger)

    def update(self, temperature):
        if not isinstance(temperature, int) or not -20_000 <= temperature <= 100_000:
            raise ValueError('temperature must be a plausible integer in milli-C')
        for i, (trigger, clear) in enumerate(zip(self.trigger, self.clear)):
            if temperature >= trigger:
                self.active[i] = True
            if temperature <= clear:
                self.active[i] = False
        # Stock SIC chooses the last contiguous active threshold.
        return next((i - 1 for i, active in enumerate(self.active) if not active),
                    len(self.active) - 1)


class WirelessThermal:
    """Stock normal-mode XM81 mitigation level, with display-state encoding."""
    TRIGGER = (36800, 37900, 38600, 39300, 40000, 40700,
               41300, 41900, 42500, 43100, 43700, 44500)
    CLEAR = (36000, 36800, 37900, 38600, 39300, 40000,
             40700, 41300, 41900, 42500, 43100, 43700)
    TARGET = (301, 402, 503, 604, 705, 806, 1008, 1210,
              1311, 1412, 1413, 1515)

    def __init__(self):
        self.hysteresis = Hysteresis(self.TRIGGER, self.CLEAR)

    def update(self, temperature, screen_on):
        if type(screen_on) is not bool:
            raise ValueError('a measured display state is required')
        level = self.hysteresis.update(temperature)
        encoded = 0 if level < 0 else self.TARGET[level]
        # HyperOS screen_state != 0 selects the hundreds; zero the remainder.
        return encoded // 100 if screen_on else encoded % 100


class WiredThermal(WirelessThermal):
    """Stock battery cooling-device level (BAT SET property 10)."""
    TRIGGER = (34000, 36000, 37500, 38500, 39500, 40500, 41500, 43000, 44000, 45000)
    CLEAR = (32000, 34200, 36200, 38000, 39200, 39500, 41000, 42000, 43200, 44200)
    TARGET = (500, 700, 1100, 1200, 1200, 1400, 1400, 1400, 1400, 1500)


class SicThermal:
    """Stock incremental SIC calculation, evaluated at its 2-second cadence.

The caller supplies three virtual-skin readings at the stock sensor's 1-second
cadence, newest first, and the current firmware FCC readback in microamps.
    This is a pure model; the caller must enforce stale-read handling,
    authentication, the other thermal votes and its current staging ceiling.
"""
    TRIGGER = (15000, 35000, 35200, 38500, 41300, 44500, 45000, 46000)
    CLEAR = (14000, 34000, 34500, 37700, 39000, 44000, 44500, 45500)
    TARGET = (0, 35000, 37200, 39500, 43500, 44500, 45000, 46000)
    KS = (0, 0, 6500000, 6500000, 6500000, 6500000, 6500000, 6500000)
    KI = (0, 0, 100000, 100000, 100000, 100000, 100000, 100000)
    MAX_MA = (15600, 15600, 13500, 8000, 4500, 1000, 500, 300)
    MIN_MA = (15600, 15600, 4600, 4500, 2500, 1000, 500, 300)

    def __init__(self):
        self.hysteresis = Hysteresis(self.TRIGGER, self.CLEAR)

    def update(self, history, current_ua):
        if len(history) != 3 or any(type(t) is not int or not -20000 <= t <= 100000
                                    for t in history):
            raise ValueError('three real temperature samples are required')
        if type(current_ua) is not int or not 0 <= current_ua <= 22000000:
            raise ValueError('invalid firmware FCC readback')
        level = self.hysteresis.update(history[0])
        if level < 0:
            return 22000000  # Stock default host vote; not a safe standalone limit.
        error = self.TARGET[level] - history[0]
        previous_error = self.TARGET[level] - history[1]
        delta = trunc_div(self.KI[level] * error +
                          self.KS[level] * (error - previous_error), 1000)
        return max(self.MIN_MA[level] * 1000,
                   min(self.MAX_MA[level] * 1000, current_ua + delta))
