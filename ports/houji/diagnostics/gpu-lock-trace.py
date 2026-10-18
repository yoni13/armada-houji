#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
"""Opt-in kufs1 GPU reservation-lock/fence diagnostics; no kernel image changes.

Run as root under a system service. Tracefs probes are read-only instrumentation.
Offsets come from running BTF, not a different build's vmlinux. The collector
streams a bounded recent window to private files, saves on a persistent GPU
retirement wait or SIGUSR1, and removes its own probes on exit. Continuous
service mode re-arms after each capture and keeps eight recent runs. It does not
reset the GPU, freeze tasks, alter eviction, or initiate suspend.
"""
import argparse
from collections import deque
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import select
import signal
import struct
import time

ROOT = Path('/sys/kernel/tracing')
GROUP = 'houji_gpu_locks'
SUPPORTED = ('7.2.6-armada-houji-kufs1', '7.2.6-armada-houji-k20261010')
MUTEX_FLAGS = 7  # kernel/locking/mutex.h: WAITERS | HANDOFF | PICKUP.
LOCK = Path('/run/lock/houji-gpu-lock-trace.lock')
KEEP_RUNS = 8


def text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ''


def stamp():
    return {'time': datetime.datetime.now().astimezone().isoformat(),
            'mono': time.monotonic(), 'boot': time.clock_gettime(time.CLOCK_BOOTTIME)}


def btf_layout(data):
    """Return struct member byte offsets; reject unsupported or truncated BTF."""
    magic, version, _, hlen, toff, tlen, soff, slen = struct.unpack_from('<HBBIIIII', data)
    if magic != 0xEB9F or version != 1 or hlen + max(toff + tlen, soff + slen) > len(data):
        raise ValueError('Unsupported BTF header')
    strings = data[hlen + soff:hlen + soff + slen]
    def name(offset):
        return strings[offset:strings.index(b'\0', offset)].decode()
    pos, end, result = hlen + toff, hlen + toff + tlen, {}
    while pos < end:
        n, info, size = struct.unpack_from('<III', data, pos)
        pos += 12
        kind, count = (info >> 24) & 31, info & 0xFFFF
        if kind in (4, 5):
            members = {}
            for _ in range(count):
                mn, _, bits = struct.unpack_from('<III', data, pos)
                pos += 12
                bitfield = bits >> 24 if info >> 31 else 0
                bits = bits & 0xFFFFFF if info >> 31 else bits
                if not bitfield and bits % 8 == 0:
                    members[name(mn)] = bits // 8
            if kind == 4 and name(n):
                result[name(n)] = {'size': size, 'members': members}
        else:
            pos += {1: 4, 2: 0, 3: 12, 6: count * 8, 7: 0, 8: 0, 9: 0,
                    10: 0, 11: 0, 12: 0, 13: count * 8, 14: 4, 15: count * 12,
                    16: 0, 17: 4, 18: 0, 19: count * 12}[kind]
        if pos > end:
            raise ValueError('Truncated BTF types')
    return result


def definitions(layout, symbols, workers):
    def off(typ, member):
        return layout[typ]['members'][member]
    def at(offset, value):
        return f'{offset:+d}({value})'
    def member(typ, key, value):
        return at(off(typ, key), value)
    if off('dma_resv', 'lock') or off('ww_mutex', 'base') or off('mutex', 'owner'):
        raise ValueError('Unsupported reservation/mutex nesting')
    resv = member('drm_gem_object', 'resv', '$arg1')
    owner = at(0, resv)
    obj = f'obj=$arg1:x64 resv={resv}:x64 owner_raw={owner}:x64 bytes={member("drm_gem_object", "size", "$arg1")}:u64'
    worker = '(' + ' || '.join(f'common_pid == {pid}' for pid in workers) + ')'
    def fence(base, prefix):
        return ' '.join(f'{prefix}{key}={member("dma_fence", key, base)}:{typ}'
                        for key, typ in (('ops', 'x64'), ('context', 'u64'), ('seqno', 'u64'), ('flags', 'x64')))
    hw = member('msm_gem_submit', 'hw_fence', '$arg1')
    uf = member('msm_gem_submit', 'user_fence', '$arg1')
    lock_owner = at(0, '$arg1')
    # owner_raw contains three flag bits. Read all eight possible pid positions
    # with nofault fetches, then select only pidN where N == owner_raw & 7.
    # Never interpret owner_raw directly as a task pointer without masking it.
    owner_pids = ' '.join(f'owner_pid{n}={at(off("task_struct", "pid") - n, lock_owner)}:u32'
                          for n in range(8))
    parent = at(off('drm_sched_fence', 'parent') - off('drm_sched_fence', 'finished'), '$arg1')
    fctx = member('msm_fence', 'fctx', parent)
    hardware = at(0, member('msm_fence_context', 'fenceptr', fctx))
    # Kernel fetches use nofault reads. Interpret the nested fields ONLY after
    # matching both ops pointers: arrays/chains and not-yet-scheduled fences
    # do not have this layout. Generic waits are still recorded separately.
    detail = (f'fence=$arg1:x64 {fence("$arg1", "")} parent={parent}:x64 {fence(parent, "hw_")} '
              f'fctx={fctx}:x64 completed={member("msm_fence_context", "completed_fence", fctx)}:u32 '
              f'hardware={hardware}:u32 timeout=$arg3:s64')
    typed = (f'timeout == 10 && ops == {symbols["drm_sched_fence_ops_finished"]} '
             f'&& hw_ops == {symbols["msm_fence_ops"]}')
    probes = [
        ('alloc', 'p', 'drm_gem_get_pages', obj, None),
        ('alloc_end', 'r128', 'drm_gem_get_pages', 'obj=$arg1:x64 result=$retval:x64', None),
        ('evict', 'p', 'active_evict', obj, None),
        ('evict_end', 'r128', 'active_evict', 'obj=$arg1:x64 result=$retval:s64', None),
        ('purge', 'p', 'active_purge', obj, None),
        ('purge_end', 'r128', 'active_purge', 'obj=$arg1:x64 result=$retval:s64', None),
        ('resv_wait', 'p', 'dma_resv_wait_timeout', 'resv=$arg1:x64 owner_raw=+0($arg1):x64 usage=$arg2:u32 timeout=$arg4:u64', 'timeout == 10'),
        ('resv_end', 'r128', 'dma_resv_wait_timeout', 'resv=$arg1:x64 timeout=$arg4:u64 result=$retval:s64', 'timeout == 10'),
        ('wait', 'p', 'dma_fence_wait_timeout', f'fence=$arg1:x64 {fence("$arg1", "")} timeout=$arg3:s64', 'timeout == 10'),
        ('wait_detail', 'p', 'dma_fence_wait_timeout', detail, typed),
        ('wait_end', 'r128', 'dma_fence_wait_timeout', 'fence=$arg1:x64 timeout=$arg3:s64 result=$retval:s64', 'timeout == 10'),
        ('lock', 'p', '__ww_mutex_lock_slowpath', f'lock=$arg1:x64 owner_raw=+0($arg1):x64 {owner_pids}', worker),
        ('lock_end', 'r64', '__ww_mutex_lock_slowpath', 'lock=$arg1:x64 result=$retval:s64', worker),
        ('retire', 'p', 'msm_submit_retire',
         f'submit=$arg1:x64 ident={member("msm_gem_submit", "ident", "$arg1")}:u32 '
         f'hw={hw}:x64 user={uf}:x64 {fence(hw,"hw_")} {fence(uf,"user_")}', worker),
        ('retire_end', 'r64', 'msm_submit_retire', 'submit=$arg1:x64', worker),
    ]
    return probes


LINE = re.compile(r'-(\d+)\s+\[\d+\].*?\s(\d+\.\d+): (\w+): (.*)')
VALUES = re.compile(r'(\w+)=(-?0x[0-9a-fA-F]+|-?\d+)\b')


def complete32(completed, seq):
    return ((completed - seq) & 0xFFFFFFFF) < 0x80000000


class Dependency:
    """Correlate identities, not merely similar stacks. All conclusions are candidates."""
    def __init__(self):
        self.alloc = {}
        self.reclaim = {}
        self.reservations = {}
        self.locks = {}
        self.retire = {}
        self.waits = {}
        self.candidates = deque(maxlen=64)
        self.last_candidate = {}
        self.dropped_state = 0
        self.lines = 0

    def feed(self, line):
        match = LINE.search(line)
        if not match:
            return
        tid, when, event, rest = match.groups()
        tid, when = int(tid), float(when)
        values = {key: int(value, 16 if '0x' in value else 10) for key, value in VALUES.findall(rest)}
        row = dict(values, tid=tid, mono=when, event=event)
        if event == 'lock':
            raw = values.get('owner_raw', 0)
            row['owner_tid'] = values.get(f'owner_pid{raw & MUTEX_FLAGS}', 0) if raw & ~MUTEX_FLAGS else 0
        self.lines += 1
        stores = {'alloc': self.alloc, 'evict': self.reclaim, 'purge': self.reclaim,
                  'resv_wait': self.reservations, 'lock': self.locks,
                  'retire': self.retire, 'wait': self.waits}
        ends = {'alloc_end': (self.alloc, 'obj'), 'evict_end': (self.reclaim, 'obj'),
                'purge_end': (self.reclaim, 'obj'), 'lock_end': (self.locks, 'lock'),
                'retire_end': (self.retire, 'submit'), 'wait_end': (self.waits, 'fence'),
                'resv_end': (self.reservations, 'resv')}
        if event == 'wait' and self.waits.get(tid, {}).get('event') == 'wait_detail' and \
                self.waits[tid].get('fence') == values.get('fence') and abs(when - self.waits[tid]['mono']) < 0.001:
            # Two probes at the same entry can dispatch in either order.
            return
        if event in stores:
            stores[event][tid] = row
            # Bound state even if unusual recursion, thread churn or losses
            # prevent matching returns. Never silently present eviction as proof.
            if len(stores[event]) > 4096:
                stores[event].pop(next(iter(stores[event])))
                self.dropped_state += 1
        elif event in ends:
            store, key = ends[event]
            if store.get(tid, {}).get(key) == values.get(key):
                store.pop(tid, None)
        elif event == 'wait_detail':
            self.waits[tid] = row
            allocation, reclaim = self.alloc.get(tid), self.reclaim.get(tid)
            reservation = self.reservations.get(tid)
            if (not allocation or not reclaim or not reservation
                    or reservation.get('resv') != reclaim.get('resv')
                    or not allocation['mono'] <= reclaim['mono'] <= reservation['mono'] <= when):
                return
            owner = allocation.get('owner_raw', 0) & ~MUTEX_FLAGS
            if not owner or owner != (reclaim.get('owner_raw', 0) & ~MUTEX_FLAGS):
                return
            for worker, lock in self.locks.items():
                if (worker not in self.retire or when < lock['mono']
                        or (lock.get('owner_raw', 0) & ~MUTEX_FLAGS) != owner
                        or lock.get('owner_tid') != tid):
                    continue
                seq = values.get('hw_seqno', 0)
                hardware_done = complete32(values.get('hardware', 0), seq)
                notified = bool(values.get('flags', 0) & 1)
                row2 = {'worker': worker, 'owner_tid': tid, 'retire': self.retire[worker],
                        'lock': lock, 'allocation': allocation, 'reclaim': reclaim,
                        'reservation_wait': reservation, 'wait': row,
                        'hardware_complete': hardware_done, 'user_fence_signaled': notified,
                        'lock_matches': ('allocation' if lock.get('lock') == allocation.get('resv') else
                                         'reclaim' if lock.get('lock') == reclaim.get('resv') else 'another owner lock'),
                        'classification': 'candidate dependency; confirm stacks and loss counters'}
                # Preserve bounded examples per contended lock, not every 40ms retry.
                key = (worker, lock.get('lock'))
                if when - self.last_candidate.get(key, -100) >= 0.5:
                    self.candidates.append(row2)
                    self.last_candidate[key] = when
                    if len(self.last_candidate) > 4096:
                        self.last_candidate.pop(next(iter(self.last_candidate)))

    def pending_worker(self, worker, now):
        retire, lock = self.retire.get(worker), self.locks.get(worker)
        return bool(retire and lock and lock['mono'] >= retire['mono']
                    and now - lock['mono'] >= 2)

    def state(self):
        return {key: value for key, value in vars(self).items()
                if key not in ('candidates', 'last_candidate')} | {'candidates': list(self.candidates)}


class Rolling:
    def __init__(self, out, size=8 << 20, count=8):
        self.out, self.size, self.count = out, size, count
        self.index, self.written, self.total = 0, 0, 0
        self.stream = None
        self.paths = deque()

    def write(self, data):
        if self.stream is None or self.written + len(data) > self.size:
            if self.stream:
                self.stream.flush()
                os.fsync(self.stream.fileno())
                self.stream.close()
            self.index += 1
            path = self.out / f'trace-{self.index:06d}.txt'
            self.paths.append(path)
            self.stream = path.open('wb')
            self.written = 0
            while len(self.paths) > self.count:
                self.paths.popleft().unlink()
        self.stream.write(data)
        self.written += len(data)
        self.total += len(data)

    def flush(self):
        if self.stream:
            self.stream.flush()
            os.fsync(self.stream.fileno())

    def close(self):
        self.flush()
        if self.stream:
            self.stream.close()


def prune_runs(root, current, keep=KEEP_RUNS):
    """Bound continuous storage; never prune old one-shot captures or foreign files."""
    runs = []
    for directory in root.iterdir():
        if directory == current or directory.is_symlink() or not directory.is_dir():
            continue
        if not re.fullmatch(r'\d{8}T\d{6}\.\d{6}', directory.name):
            continue
        files = list(directory.iterdir())
        allowed = r'(?:trace-\d+\.txt|start\.json|end\.json|dependency-state\.json|\.dependency-state\.tmp|tasks\.json|meminfo\.txt|vmstat\.txt|loss-counters\.json|kprobe-profile\.txt)'
        if any(p.is_symlink() or not p.is_file() or not re.fullmatch(allowed, p.name) for p in files):
            continue
        try:
            marker = json.loads((directory / 'start.json').read_text())
        except (OSError, ValueError):
            continue
        if marker.get('collector') == GROUP and marker.get('continuous') is True:
            runs.append((directory.stat().st_mtime_ns, directory, files))
    for _, directory, files in sorted(runs, reverse=True)[keep - 1:]:
        for path in files:
            path.unlink()
        directory.rmdir()


def limit_reason(continuous, elapsed, seconds, total):
    if continuous:
        return None
    if elapsed >= seconds:
        return 'time-limit'
    if total >= 8 << 30:
        return 'total-write-limit'
    return None


def snapshot(out):
    tasks = {}
    for proc in Path('/proc').glob('[0-9]*'):
        for task in (proc / 'task').glob('[0-9]*'):
            stat = text(task / 'stat')
            if not stat:
                continue
            state = stat[stat.rfind(')') + 2:].split()[0]
            comm = text(task / 'comm')
            if state != 'D' and comm not in ('gpu-worker', 'steam', 'steamwebhelper', 'gamescope-wl', 'mangoapp'):
                continue
            tasks[task.name] = {'tgid': proc.name, 'comm': comm, 'state': state,
                                'stack': text(task / 'stack'), 'wchan': text(task / 'wchan'),
                                'schedstat': text(task / 'schedstat')}
    (out / 'tasks.json').write_text(json.dumps(tasks, indent=2))
    for name in ('meminfo', 'vmstat'):
        (out / f'{name}.txt').write_text(text(Path('/proc') / name))


class Trace:
    def __init__(self, root=ROOT):
        self.root = root
        self.instance = root / 'instances' / GROUP
        self.installed = []
        self.owned = False

    def command(self, line):
        # Python text append seeks to EOF, unsupported by tracefs seq_file.
        fd = os.open(self.root / 'kprobe_events', os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, (line + '\n').encode())
        finally:
            os.close(fd)

    def configure(self, probes, workers):
        if self.instance.exists() or (self.root / 'events' / GROUP).exists():
            raise RuntimeError('An existing houji_gpu_locks trace needs cleanup first')
        self.instance.mkdir()
        self.owned = True
        for name, value in (('tracing_on', 0), ('buffer_size_kb', 2048), ('trace_clock', 'mono')):
            (self.instance / name).write_text(str(value))
        for name, kind, function, fields, filt in probes:
            self.command(f'{kind}:{GROUP}/{name} {function} {fields}')
            self.installed.append(name)
            base = self.instance / 'events' / GROUP / name
            if filt:
                (base / 'filter').write_text(filt)
            (base / 'enable').write_text('1')
        for name in ('contention_begin', 'contention_end'):
            base = self.instance / 'events' / 'lock' / name
            (base / 'filter').write_text(' || '.join(f'common_pid == {pid}' for pid in workers))
            (base / 'enable').write_text('1')

    def disable(self):
        if self.owned:
            (self.instance / 'tracing_on').write_text('0')

    def cleanup(self):
        errors = []
        if self.owned:
            for name in ('tracing_on', 'events/enable'):
                try:
                    (self.instance / name).write_text('0')
                except OSError as exc:
                    errors.append(f'{name}: errno {exc.errno}')
        for name in reversed(self.installed):
            try:
                self.command(f'-:{GROUP}/{name}')
            except OSError as exc:
                errors.append(f'{name}: errno {exc.errno}')
        if self.owned:
            try:
                self.instance.rmdir()
            except OSError as exc:
                errors.append(f'instance: errno {exc.errno}')
        return errors

    def cleanup_stale(self):
        """Only under LOCK, after the previous collector has exited."""
        self.owned = self.instance.exists()
        for line in text(self.root / 'kprobe_events').splitlines():
            fields = line.split()
            match = re.fullmatch(r'[pr]\d*:' + GROUP + r'/(\w+)', fields[0]) if fields else None
            if match:
                self.installed.append(match[1])
        return self.cleanup()


def collect(args):
    if args.continuous:
        # Separate retention from existing evidence, including interrupted runs.
        args.output = args.output / 'continuous'
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    layout = btf_layout(Path('/sys/kernel/btf/vmlinux').read_bytes())
    workers = [int(p.name) for p in Path('/proc').glob('[0-9]*') if text(p / 'comm') == 'gpu-worker']
    if not workers:
        raise RuntimeError('no GPU retirement worker found; service will retry')
    symbols = {}
    for line in text('/proc/kallsyms').splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[2] in ('drm_sched_fence_ops_finished', 'msm_fence_ops'):
            symbols[fields[2]] = int(fields[0], 16)
    if len(symbols) != 2 or not all(symbols.values()):
        raise RuntimeError('fence types unavailable')
    probes = definitions(layout, symbols, workers)
    out = args.output / datetime.datetime.now().strftime('%Y%m%dT%H%M%S.%f')
    out.mkdir(mode=0o700)
    (out / 'start.json').write_text(json.dumps(stamp() | {'release': os.uname().release,
        'collector': GROUP, 'continuous': args.continuous,
        'boot_id': text('/proc/sys/kernel/random/boot_id'),
        'seconds': None if args.continuous else args.seconds,
        'workers': workers, 'definitions': probes, 'fence_ops': symbols,
        'layout': {name: layout[name] for name in ('dma_resv', 'ww_mutex', 'mutex', 'drm_gem_object',
                   'dma_fence', 'drm_sched_fence', 'msm_fence', 'msm_fence_context', 'msm_gem_submit',
                    'task_struct')}}, indent=2))
    if args.continuous:
        prune_runs(args.output, out)
    trace, tracker, writer = Trace(), Dependency(), Rolling(out)
    reason = None
    def stopping(signum, _):
        nonlocal reason
        reason = 'manual-capture' if signum == signal.SIGUSR1 else 'stopped'
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
        signal.signal(sig, stopping)
    fd = None
    pending = b''
    def drain():
        nonlocal pending
        # Return regularly to the watchdog even under a very busy GPU.
        until = time.monotonic() + 0.15
        while time.monotonic() < until:
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                break
            if not data:
                break
            pending += data
            lines = pending.split(b'\n')
            pending = lines.pop()
            if lines:
                writer.write(b'\n'.join(lines) + b'\n')
                for line in lines:
                    tracker.feed(line.decode(errors='replace'))
    error = None
    try:
        trace.configure(probes, workers)
        fd = os.open(trace.instance / 'trace_pipe', os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        (trace.instance / 'tracing_on').write_text('1')
        limit = 'continuous; no time/write limit' if args.continuous else f'maximum {args.seconds}s'
        print(f'GPU lock/fence trace armed: {out}; {limit}', flush=True)
        start = last_check = last_flush = time.monotonic()
        while reason is None:
            select.select([fd], [], [], 0.1)
            drain()
            now = time.monotonic()
            if now - last_check >= 0.5:
                if any(tracker.pending_worker(pid, now) for pid in workers):
                    reason = 'retirement-blocked'
                if not all((Path('/proc') / str(pid)).exists() for pid in workers):
                    reason = 'worker-exited'
                last_check = now
            if now - last_flush >= 2:
                writer.flush()
                temporary = out / '.dependency-state.tmp'
                with temporary.open('w') as stream:
                    json.dump(tracker.state(), stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(out / 'dependency-state.json')
                last_flush = now
            reason = reason or limit_reason(args.continuous, now - start, args.seconds, writer.total)
    except Exception as exc:
        reason, error = 'collector-error', f'{type(exc).__name__}: {exc}'
    finally:
        try:
            trace.disable()
            if fd is not None:
                # Bounded ring: drain the remaining records after producers stop.
                for _ in range(100):
                    previous = writer.total
                    drain()
                    if writer.total == previous:
                        break
            if trace.owned:
                stats = {p.parent.name: p.read_text() for p in (trace.instance / 'per_cpu').glob('cpu*/stats')}
                (out / 'loss-counters.json').write_text(json.dumps(stats, indent=2))
                # Keep complete profile output: kernels format names either
                # with or without the group. Filtering can hide miss counters.
                (out / 'kprobe-profile.txt').write_text(text(ROOT / 'kprobe_profile') + '\n')
        except Exception as exc:
            error = error or f'{type(exc).__name__}: {exc}'
        finally:
            # Storage exhaustion or a failed flush must never leave probes on.
            try:
                if fd is not None:
                    os.close(fd)
                writer.close()
            except OSError as exc:
                error = error or f'{type(exc).__name__}: {exc}'
            finally:
                cleanup_errors = trace.cleanup()
        snapshot(out)
        (out / 'dependency-state.json').write_text(json.dumps(tracker.state(), indent=2))
        (out / 'end.json').write_text(json.dumps(stamp() | {'reason': reason, 'error': error,
            'cleanup_errors': cleanup_errors, 'total_trace_bytes': writer.total,
            'chunks': [p.name for p in writer.paths]}, indent=2))
        print(f'Capture saved ({reason}); candidate chains: {len(tracker.candidates)}; cleanup errors: {len(cleanup_errors)}', flush=True)
    if error or cleanup_errors:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=21600,
                        help='one-shot duration, ignored in continuous mode')
    parser.add_argument('--continuous', action='store_true',
                        help='no time/write expiry; service restarts after captures; keep eight runs')
    parser.add_argument('--cleanup', action='store_true', help='service stop cleanup of owned tracefs probes')
    parser.add_argument('--output', type=Path, default=Path('/var/tmp/houji-gpu-lock-traces'))
    args = parser.parse_args()
    if os.geteuid():
        parser.error('root is required')
    if not args.cleanup and os.uname().release not in SUPPORTED:
        parser.error('a validated Houji kernel is required; revalidate function semantics for another kernel')
    if not 1 <= args.seconds <= 21600:
        parser.error('duration must be between 1 and 21600 seconds')
    os.umask(0o077)
    with LOCK.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if args.cleanup:
                print('Another collector is active; leaving its probes intact', flush=True)
                return
            parser.error('another collector is active')
        if args.cleanup:
            errors = Trace().cleanup_stale()
            if errors:
                raise RuntimeError('; '.join(errors))
        else:
            collect(args)


if __name__ == '__main__':
    main()
