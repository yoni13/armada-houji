#!/usr/bin/env python3
"""Summarize and compare cooler benchmark runs produced by cooler-bench.py.

    cooler-analyze.py RUN_DIR [RUN_DIR ...]

Everything is computed from the saved telemetry and MangoHud frame log; nothing
is estimated from the headline MangoHud summary. Frame-time percentiles use the
frames themselves, FPS uses total frames / total time.
"""
import csv
import json
import statistics
import sys
from pathlib import Path


def load_frames(run):
    for path in sorted(run.glob('mangoapp-*.csv')):
        if path.name.endswith('_summary.csv'):
            continue
        lines = path.read_text().splitlines()
        # First two lines are system info (header + row); frame table follows.
        reader = csv.DictReader(lines[2:])
        rows = list(reader)
        if rows and 'frametime' in rows[0]:
            return [(float(r['frametime']), int(r['elapsed'])) for r in rows]
    return []


def percentile_low(frametimes, fraction):
    """Average FPS of the slowest `fraction` of frames (the usual '1% low')."""
    worst = sorted(frametimes, reverse=True)
    count = max(1, int(len(worst) * fraction))
    return 1000.0 / statistics.mean(worst[:count])


def summarize(run):
    meta = json.loads((run / 'meta.json').read_text())
    samples = json.loads((run / 'samples.json').read_text())
    frames = load_frames(run)
    result = {'label': meta['args']['label'], 'dir': run.name, 'result': meta['result'],
              'note': meta['args'].get('note', ''), 'cooldown_gate_met': meta.get('gate_met'),
              'cooldown_seconds': meta.get('cooldown_seconds')}
    steady = [s for s in samples if s['phase'] == 'run']
    if not steady:
        return result
    first, last = steady[0], steady[-1]
    duration = last['mono'] - first['mono']
    result['run_seconds'] = round(duration, 1)
    result['start_c'] = {'skin': first['skin'] / 1000, 'battery': first['board']['battery'] / 1000,
                         'die_max': first['die_max'] / 1000}
    result['end_c'] = {'skin': last['skin'] / 1000, 'battery': last['board']['battery'] / 1000,
                       'die_max': last['die_max'] / 1000}
    result['peak_c'] = {'skin': max(s['skin'] for s in steady) / 1000,
                        'die_max': max(s['die_max'] for s in steady) / 1000}
    third = max(1, len(steady) // 3)
    last_third = steady[-third:]
    result['last_third'] = {
        'skin_mean_c': statistics.mean(s['skin'] for s in last_third) / 1000,
        'cap_mhz_mean': {k: round(statistics.mean(s['cpu'][k]['cap'] for s in last_third) / 1000)
                         for k in last_third[0]['cpu']},
        'gpu_mhz_mean': round(statistics.mean(s['gpu']['cur'] for s in last_third) / 1e6),
    }
    watts = [int(s['battery']['current_now']) * int(s['battery']['voltage_now']) / 1e12
             for s in steady if s['battery']['current_now'] and s['battery']['voltage_now']]
    if watts:
        # Positive current_now is charging on this platform; report net discharge.
        result['battery_net_discharge_w'] = round(-statistics.mean(watts), 2)
    cpu_a, cpu_b = steady[0]['cpu_ticks'], steady[-1]['cpu_ticks']
    busy = (cpu_b['total'] - cpu_a['total']) - (cpu_b['idle'] - cpu_a['idle'])
    result['cpu_busy_percent'] = round(100 * busy / max(1, cpu_b['total'] - cpu_a['total']), 1)
    if frames:
        frametimes = [f for f, _ in frames]
        total_ms = sum(frametimes)
        result['frames'] = len(frames)
        result['avg_fps'] = round(len(frames) / (total_ms / 1000), 1)
        result['low_1pct_fps'] = round(percentile_low(frametimes, 0.01), 1)
        result['low_0_1pct_fps'] = round(percentile_low(frametimes, 0.001), 1)
        result['frametime_p99_ms'] = round(sorted(frametimes)[int(len(frametimes) * 0.99)], 1)
        # FPS per third of the run shows whether the advantage fades as heat soaks in.
        thirds = []
        for part in range(3):
            lo, hi = part * len(frames) // 3, (part + 1) * len(frames) // 3
            chunk = frametimes[lo:hi]
            thirds.append(round(len(chunk) / (sum(chunk) / 1000), 1))
        result['avg_fps_by_third'] = thirds
    return result


def main(argv):
    runs = [Path(a) for a in argv]
    if not runs:
        sys.exit(__doc__)
    summaries = [summarize(r) for r in runs]
    print(json.dumps(summaries, indent=2))
    comparable = [s for s in summaries if 'avg_fps' in s and s['result'] == 'complete']
    if len(comparable) > 1:
        base = comparable[0]
        print('\nComparison against first run (%s):' % base['label'])
        for s in comparable[1:]:
            delta = lambda key: round(s[key] - base[key], 1)
            print('  %-12s avg FPS %+.1f  1%% low %+.1f  end skin %+.1f C  net battery %+.2f W' % (
                s['label'], delta('avg_fps'), delta('low_1pct_fps'),
                s['end_c']['skin'] - base['end_c']['skin'],
                s['battery_net_discharge_w'] - base['battery_net_discharge_w']))


if __name__ == '__main__':
    main(sys.argv[1:])
