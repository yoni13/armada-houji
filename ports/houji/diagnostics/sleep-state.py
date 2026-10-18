#!/usr/bin/python3
"""Opt-in, fsynced sleep ledger independent of journald and wall-clock ordering."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path('/var/lib/armada/sleep-debug')
ENABLED = Path('/etc/armada/houji-sleep-debug')
MAX_BYTES = 8 << 20
KEEP = 4


def text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def append(root, row, maximum=MAX_BYTES, keep=KEEP):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lockfd = os.open(root / '.lock', os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC, 0o600)
    with os.fdopen(lockfd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / 'events.jsonl'
        if path.exists() and path.stat().st_size >= maximum:
            (root / f'events.{keep - 1}.jsonl').unlink(missing_ok=True)
            for number in range(keep - 2, 0, -1):
                old = root / f'events.{number}.jsonl'
                if old.exists():
                    old.replace(root / f'events.{number + 1}.jsonl')
            path.replace(root / 'events.1.jsonl')
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(row, separators=(',', ':')) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(root, os.O_DIRECTORY | os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def record(event, **details):
    if not ENABLED.exists():
        return
    try:
        row = {'event': event, 'time': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
               'boot_id': text('/proc/sys/kernel/random/boot_id'), 'mono': time.monotonic(),
               'boottime': time.clock_gettime(time.CLOCK_BOOTTIME),
               'rtc': text('/sys/class/rtc/rtc0/since_epoch'), 'pid': os.getpid(), 'details': details}
        row['supplies'] = {p.parent.name: text(p) for p in Path('/sys/class/power_supply').glob('*/online')}
        row['battery'] = {name: text('/sys/class/power_supply/battery/' + name)
                          for name in ('capacity', 'status', 'voltage_now', 'temp')}
        row['suspend_stats'] = {p.name: text(p) for p in Path('/sys/power/suspend_stats').glob('*') if p.is_file()}
        row['inputs'] = [line for line in (text('/proc/interrupts') or '').splitlines()
                         if any(name in line for name in ('pmic_pwrkey', 'pmic_resin'))]
        if event == 'boot':
            cmdline = (text('/proc/cmdline') or '').split()
            row['reset'] = [value for value in cmdline if value.startswith(('bootinfo.pdreason=', 'bootinfo.pureason='))]
        append(ROOT, row)
        # Keep an independent clock/boot anchor in the RAM crash console too.
        if event in ('boot', 'kernel-entry', 'kernel-return', 'kernel-error'):
            try:
                fd = os.open('/dev/kmsg', os.O_WRONLY | os.O_CLOEXEC)
                try:
                    message = ('<6>houji-sleep-debug: boot_id=%s event=%s time=%s mono=%.6f boot=%.6f rtc=%s\n'
                               % (row['boot_id'], event, row['time'], row['mono'], row['boottime'], row['rtc']))
                    os.write(fd, message.encode())
                finally:
                    os.close(fd)
            except OSError:
                pass
    except (OSError, ValueError) as error:
        # Debugging must never prevent sleep or its cleanup when storage fails.
        print('Houji sleep ledger unavailable: ' + str(error), file=sys.stderr, flush=True)


if __name__ == '__main__':
    record(sys.argv[1] if len(sys.argv) > 1 else 'snapshot')
