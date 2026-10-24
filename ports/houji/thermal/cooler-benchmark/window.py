#!/usr/bin/env python3
"""Telemetry for a GTA built-in benchmark window: bench-window.py RUN_DIR START END (HH:MM:SS local)."""
import csv
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

run = Path(sys.argv[1])
samples = json.loads((run / 'samples.json').read_text())


def secs(text):
    h, m, s = (int(x) for x in text.split(':'))
    return h * 3600 + m * 60 + s


def local(sample):
    return secs(sample['time'][11:19])


start, end = secs(sys.argv[2]), secs(sys.argv[3])
win = [s for s in samples if s['phase'] in ('warmup', 'run') and start <= local(s) <= end]
print(run.name, len(win), 'samples', sys.argv[2], '-', sys.argv[3])
if not win:
    sys.exit(0)


def col(f):
    return [f(s) for s in win]


def stat(name, values, unit=''):
    print('  %-22s start %-8.1f mean %-8.1f max %-8.1f end %.1f %s' % (
        name, values[0], statistics.mean(values), max(values), values[-1], unit))


stat('skin', col(lambda s: s['skin'] / 1000), 'C')
stat('die max', col(lambda s: s['die_max'] / 1000), 'C')
stat('battery temp', col(lambda s: s['board']['battery'] / 1000), 'C')
for policy in sorted(win[0]['cpu']):
    caps = col(lambda s: s['cpu'][policy]['cap'] / 1000)
    cur = col(lambda s: s['cpu'][policy]['cur'] / 1000)
    print('  %-22s cap min %-6.0f mean cur %-6.0f MHz' % (policy, min(caps), statistics.mean(cur)))
gpu = [s['gpu'].get('cur', 0) / 1e6 for s in win if s['gpu']]
gmax = [s['gpu'].get('max', 0) / 1e6 for s in win if s['gpu']]
print('  gpu mean cur %.0f MHz, min cap %.0f MHz' % (statistics.mean(gpu), min(gmax)))
amps = [-int(s['battery']['current_now']) / 1e6 for s in win]
volts = [int(s['battery']['voltage_now']) / 1e6 for s in win]
watts = [a * v for a, v in zip(amps, volts)]
print('  battery draw mean %.2f W (%.2f A), max %.2f W' % (statistics.mean(watts), statistics.mean(amps), max(watts)))
first, last = win[0], win[-1]
print('  main %% %s -> %s, fg1_rsoc %s -> %s, counter %s -> %s uAh' % (
    first['battery']['capacity'], last['battery']['capacity'], first['gauge'].get('fg1_rsoc'),
    last['gauge'].get('fg1_rsoc'), first['battery'].get('charge_counter'), last['battery'].get('charge_counter')))
ticks = [(a['game_ticks'], b['game_ticks'], a['mono'], b['mono']) for a, b in zip(win, win[1:])
         if a['game_ticks'] is not None and b['game_ticks'] is not None]
if ticks:
    rate = sum(t[1] - t[0] for t in ticks) / sum(t[3] - t[2] for t in ticks) / 100
    print('  game CPU %.2f cores mean' % rate)
