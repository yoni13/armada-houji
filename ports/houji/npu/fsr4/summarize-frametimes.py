#!/usr/bin/env python3
"""Summarize observed Gamescope presentation intervals without clipping outliers."""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics


def percentile(sorted_values, quantile):
    at = (len(sorted_values)-1)*quantile
    lo, hi = math.floor(at), math.ceil(at)
    return sorted_values[lo]+(sorted_values[hi]-sorted_values[lo])*(at-lo)


def summarize(path):
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) < 2:
        raise ValueError('At least two presentation samples are required')
    values = [float(row['present_interval_ms']) for row in rows]
    elapsed = [float(row['elapsed_seconds']) for row in rows]
    if not all(math.isfinite(v) and v > 0 for v in values):
        raise ValueError('Non-positive or non-finite presentation interval')
    if not all(math.isfinite(v) and v >= 0 for v in elapsed) or any(b < a for a,b in zip(elapsed,elapsed[1:])):
        raise ValueError('Invalid recording timestamps')
    ordered = sorted(values)
    mean = statistics.fmean(values)
    slowest_count = max(1, math.ceil(len(values)*.01))
    return {
        'measurement': 'Gamescope observed presentation intervals; not upscaler-call duration',
        'samples': len(values), 'sample_span_seconds': elapsed[-1]-elapsed[0],
        'mean_ms': mean, 'fps_from_mean_interval': 1000/mean,
        'p50_ms': percentile(ordered,.5), 'p95_ms': percentile(ordered,.95),
        'p99_ms': percentile(ordered,.99), 'max_ms': ordered[-1],
        'slowest_one_percent_mean_fps': 1000/statistics.fmean(ordered[-slowest_count:]),
        'discarded_outliers': 0,
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recording',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    text=json.dumps(summarize(args.recording),indent=2)+'\n'
    if args.output:args.output.write_text(text)
    print(text,end='')


if __name__=='__main__':main()
