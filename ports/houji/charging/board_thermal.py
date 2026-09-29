#!/usr/bin/python3
"""Stock Houji virtual skin sensor, using the original board ADC channels."""
from pathlib import Path
import glob

WEIGHTS = {'pa_therm0': 119, 'quiet_therm': 799, 'charger_therm0': 48,
           'cpu_therm': 115, 'battery': 306, 'wifi_therm': -457}


def virtual_temperature(temperatures):
    # Do not substitute CPU die or PMIC die readings for board thermistors.
    if any(name not in temperatures for name in [*WEIGHTS, 'usb_therm']):
        raise ValueError('a required board thermistor is missing')
    if any(not -20_000 <= value <= 100_000 for value in temperatures.values()):
        raise ValueError('invalid thermistor reading')
    weighted = sum(temperatures[name] * weight for name, weight in WEIGHTS.items())
    # C integer division truncates towards zero, including negative sums.
    value = (abs(weighted) // 1000) * (-1 if weighted < 0 else 1) + 1779
    if not 0 <= value <= 80_000:
        raise ValueError('virtual temperature outside reporting range')
    return value


def read_temperatures(bat=Path('/sys/class/power_supply/qcom-battmgr-bat'),
                      devices='/sys/bus/iio/devices/iio:device*'):
    values = {'battery': int((bat / 'temp').read_text()) * 100}
    found = 0
    for directory in glob.glob(devices):
        root = Path(directory)
        if (root / 'name').read_text().strip() != 'spmi-adc5-gen3':
            continue
        found += 1
        for label in root.glob('in_temp*_label'):
            name = label.read_text().strip()
            if name in WEIGHTS or name == 'usb_therm':
                if name in values:
                    raise ValueError('duplicate board thermistor')
                values[name] = int(label.with_name(label.name.removesuffix('_label') + '_input').read_text())
    if found != 1:
        raise ValueError('board ADC device unavailable or ambiguous')
    return values, virtual_temperature(values)


if __name__ == '__main__':
    import json
    values, temperature = read_temperatures()
    print(json.dumps({'temperatures_mC': values, 'virtual_skin_mC': temperature}, indent=2))
