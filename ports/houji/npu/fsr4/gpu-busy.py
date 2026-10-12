#!/usr/bin/env python3
"""Per-process Adreno GPU time from msm's submit-retired tracepoint (root).

Restores the previous tracing state. Per-submit elapsed time comes from the
kernel. The start/end counters were observed not to share one timeline across
submissions (their union exceeded the summed elapsed time), so no union is
reported. Summed elapsed time equals GPU busy time only when all submissions
used one ring, i.e. without preemption; check submits_per_ring.
"""
import json
import re
import sys
import time
from pathlib import Path

T = Path('/sys/kernel/tracing')
EVENTS = ('drm_msm_gpu/msm_gpu_submit_retired',)
seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0


def write(path, value):
    (T/path).write_text(value)


saved = {'tracing_on': (T/'tracing_on').read_text().strip(), 'buffer_size_kb': (T/'buffer_size_kb').read_text().split()[0]}
saved_events = {e: (T/'events'/e/'enable').read_text().strip() for e in EVENTS}
try:
    write('tracing_on', '0')
    write('trace', '')
    write('buffer_size_kb', '16384')
    for e in EVENTS:
        write(f'events/{e}/enable', '1')
    write('tracing_on', '1')
    time.sleep(seconds)
    write('tracing_on', '0')
    text = (T/'trace').read_text(errors='replace')
finally:
    for e, v in saved_events.items():
        write(f'events/{e}/enable', v)
    write('trace', '')
    write('buffer_size_kb', saved['buffer_size_kb'])
    write('tracing_on', saved['tracing_on'])

submit = re.compile(r'pid=(\d+) ring=(\d+):\d+ elapsed=(\d+) ns mhz=(\d+)')
per_pid, rings, mhz = {}, {}, []
for line in text.splitlines():
    m = submit.search(line)
    if m:
        pid, ring, elapsed, clock = map(int, m.groups())
        p = per_pid.setdefault(pid, {'submits': 0, 'elapsed_ms': 0.0})
        p['submits'] += 1
        p['elapsed_ms'] += elapsed/1e6
        rings[ring] = rings.get(ring, 0) + 1
        mhz.append(clock)
total_ms = sum(v['elapsed_ms'] for v in per_pid.values())


def name(pid):
    try:
        return Path(f'/proc/{pid}/comm').read_text().strip()
    except OSError:
        return 'exited'


summary = {
    'measurement': 'msm_gpu_submit_retired over a fixed window; summed per-submit elapsed GPU time',
    'window_s': seconds,
    'summed_gpu_ms_per_s': round(total_ms/seconds, 1),
    'processes': {f'{name(pid)}[{pid}]': {'submits': v['submits'], 'gpu_ms_per_s': round(v['elapsed_ms']/seconds, 2)}
                  for pid, v in sorted(per_pid.items(), key=lambda kv: -kv[1]['elapsed_ms'])},
    'submits_per_ring': rings,
    'mean_reported_mhz': round(sum(mhz)/len(mhz), 1) if mhz else None,
}
print(json.dumps(summary, indent=2))
