#!/usr/bin/python3
"""Cooler benchmark for GTA V Enhanced on the handset. Run as root.

Each run is: quit GTA fully -> `cooldown` at idle until a fixed cold-start gate
-> (attach the cooler) -> launch GTA by hand -> `run`. The game is never
frozen or paused: a freeze left GTA and the launcher at the memory limit and
the kernel killed the whole session when it resumed.

  cooldown LABEL   Sample at idle (game closed) until the gate holds, or time out.
  run LABEL        With GTA live in the benchmark scene, log a fixed-duration
                   sustained load: temperatures, frequency caps, power, network
                   traffic, brightness and MangoHud frame times.
  check            Report whether the phone is in a state fit for a run.

Nothing here changes thermal policy, power profile or game settings. Safety
stops abort a run on high temperature, low battery or a missing game process.
"""
import argparse
import ctypes
import datetime
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, '/usr/libexec/armada')
from board_thermal import read_temperatures

HOME = Path('/var/home/armada')
ROOT = HOME / '.cache/houji-cooler-benchmark'
GAME = 'GTA5_Enhanced.e'
GAME_APP = '4022596578'
BACKLIGHT = Path('/sys/class/backlight/houji-n3')
EXTERNAL_POWER = ('qcom-battmgr-usb', 'ucsi-source-psy-pmic_glink.ucsi.01', 'qcom-battmgr-wls')
NET = 'wlp1s0'


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def integer(path):
    try:
        return int(read(path))
    except (TypeError, ValueError):
        return None


def game_pid():
    for proc in Path('/proc').glob('[0-9]*'):
        if read(proc / 'comm') == GAME:
            return int(proc.name)
    return None


def game_ticks(pid):
    text = read(f'/proc/{pid}/stat') if pid else None
    if not text:
        return None
    fields = text.rsplit(')', 1)[1].split()
    return int(fields[11]) + int(fields[12])


def cpu_ticks():
    fields = [int(v) for v in (read('/proc/stat') or 'cpu 0').splitlines()[0].split()[1:]]
    return {'total': sum(fields), 'idle': fields[3] + (fields[4] if len(fields) > 4 else 0)}


def net_bytes():
    for line in (read('/proc/net/dev') or '').splitlines():
        if line.strip().startswith(NET + ':'):
            fields = line.split(':', 1)[1].split()
            return {'rx': int(fields[0]), 'tx': int(fields[8])}
    return {'rx': None, 'tx': None}


def supply(name, *keys):
    base = Path('/sys/class/power_supply') / name
    return {key: read(base / key) for key in keys}


def external_power():
    return [n for n in EXTERNAL_POWER if read(f'/sys/class/power_supply/{n}/online') == '1']


def focused_app():
    result = subprocess.run(['runuser', '-u', 'armada', '--', 'env', 'DISPLAY=:0', 'xprop', '-root',
                             'GAMESCOPE_FOCUSED_APP'], capture_output=True, text=True)
    return result.stdout.split('=')[-1].strip() if result.returncode == 0 else None


def snapshot(phase, pid):
    board, skin = read_temperatures()
    zones = {}
    for zone in sorted(Path('/sys/class/thermal').glob('thermal_zone*')):
        zones[read(zone / 'type')] = integer(zone / 'temp')
    cpu = {}
    for policy in sorted(Path('/sys/devices/system/cpu/cpufreq').glob('policy*')):
        cpu[policy.name] = {'cap': integer(policy / 'scaling_max_freq'),
                            'cur': integer(policy / 'scaling_cur_freq')}
    gpu = {}
    for dev in Path('/sys/class/devfreq').glob('*gpu'):
        gpu = {'cur': integer(dev / 'cur_freq'), 'max': integer(dev / 'max_freq')}
    meminfo = {k: int(v.split()[0]) for k, v in
               (line.split(':', 1) for line in (read('/proc/meminfo') or '').splitlines())
               if k in ('MemAvailable', 'SwapTotal', 'SwapFree')}
    return {
        'time': datetime.datetime.now().astimezone().isoformat(),
        'mono': time.monotonic(), 'phase': phase,
        'skin': skin, 'board': board, 'zones': zones,
        'die_max': max(v for v in zones.values() if v is not None),
        'cpu': cpu, 'gpu': gpu, 'cpu_ticks': cpu_ticks(), 'game_ticks': game_ticks(pid),
        'brightness': integer(BACKLIGHT / 'brightness'), 'net': net_bytes(), 'mem': meminfo,
        'battery': supply('battery', 'status', 'capacity', 'temp', 'current_now', 'voltage_now',
                          'charge_counter', 'charge_full'),
        'gauge': {name: read(node / name) for node in Path('/sys/bus/auxiliary/devices').glob(
            'pmic_glink.power-supply.*/houji_charger') for name in ('fg1_rsoc', 'fb_blank_state')},
        'usb': supply('qcom-battmgr-usb', 'online', 'usb_type', 'current_now', 'voltage_now'),
    }


libc = ctypes.CDLL(None, use_errno=True)
libc.msgget.argtypes = [ctypes.c_int, ctypes.c_int]
libc.msgget.restype = ctypes.c_int
libc.msgsnd.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]


class Control(ctypes.Structure):
    _pack_ = 1
    _layout_ = 'ms'
    _fields_ = [('kind', ctypes.c_long), ('ctrl', ctypes.c_uint32), ('version', ctypes.c_uint32),
                ('display', ctypes.c_uint8), ('logging', ctypes.c_uint8),
                ('name', ctypes.c_char * 64), ('reload', ctypes.c_uint8)]


def mangohud_logging(value):
    queue = libc.msgget(-1, 0)
    if queue < 0:
        raise RuntimeError('MangoApp control queue unavailable')
    message = Control()
    message.kind = 2
    message.ctrl = 1
    message.version = 1
    message.logging = value
    size = ctypes.sizeof(message) - ctypes.sizeof(ctypes.c_long)
    if libc.msgsnd(queue, ctypes.byref(message), size, 0x800):
        raise OSError(ctypes.get_errno(), 'MangoApp control failed')


def screenshot(path):
    # Evidence only: a failed screenshot must not invalidate a benchmark run.
    try:
        subprocess.run(['runuser', '-u', 'armada', '--', 'env', 'XDG_RUNTIME_DIR=/run/user/1000',
                        'WAYLAND_DISPLAY=gamescope-0', 'gamescopectl', 'screenshot', str(path), '1'],
                       check=True, timeout=30, capture_output=True)
    except (subprocess.SubprocessError, OSError) as error:
        print('Screenshot skipped:', error, flush=True)


def safety(sample, args, pid, need_game=True):
    if need_game and (pid is None or not Path(f'/proc/{pid}').exists()):
        return 'GTA process exited'
    if sample['die_max'] >= args.abort_die * 1000:
        return f"die sensor {sample['die_max'] / 1000:.1f} C"
    if sample['skin'] >= args.abort_skin * 1000:
        return f"virtual skin {sample['skin'] / 1000:.1f} C"
    if sample['board']['battery'] >= args.abort_battery_temp * 1000:
        return f"battery {sample['board']['battery'] / 1000:.1f} C"
    if int(sample['battery']['capacity'] or 0) < args.min_battery:
        return f"battery capacity {sample['battery']['capacity']}%"
    return None


def at_gate(sample, args):
    return (sample['skin'] <= args.gate_skin * 1000
            and sample['board']['battery'] <= args.gate_battery_temp * 1000
            and sample['die_max'] <= args.gate_die * 1000)


def common_args(parser):
    parser.add_argument('--gate-skin', type=float, default=38.0)
    parser.add_argument('--gate-battery-temp', type=float, default=37.0)
    parser.add_argument('--gate-die', type=float, default=52.0)
    parser.add_argument('--abort-skin', type=float, default=49.0)
    # Stock Android's game profile throttles on the virtual skin sensor and has no die limit; the
    # kernel's critical trip for the CPU die zones is 110 C. This is only a backstop below that.
    parser.add_argument('--abort-die', type=float, default=105.0)
    parser.add_argument('--abort-battery-temp', type=float, default=45.0)
    parser.add_argument('--min-battery', type=int, default=12)
    parser.add_argument('--allow-charger', action='store_true',
                        help='Permit external power (default: refuse, so charging heat cannot vary)')
    parser.add_argument('--note', default='')


def refuse_external_power(args):
    external = external_power()
    if external and not args.allow_charger:
        sys.exit('External power is connected (' + ', '.join(external) + '); unplug it or use --allow-charger')


def metadata(args, pid):
    thermal = subprocess.run(['pidof', 'houji-thermal'], capture_output=True, text=True).stdout.split()
    return {
        'args': vars(args), 'started': datetime.datetime.now().astimezone().isoformat(),
        'boot_id': read('/proc/sys/kernel/random/boot_id'), 'kernel': read('/proc/sys/kernel/osrelease'),
        'gta_pid': pid,
        'thermal_cmdline': (read(f'/proc/{thermal[0]}/cmdline') or '').replace('\0', ' ') if thermal else None,
        'thermal_sha256': hashlib.sha256(Path('/usr/libexec/armada/houji-thermal').read_bytes()).hexdigest(),
        'power_state': read('/var/lib/armada/powerd.state'), 'result': 'started',
    }


def writer(run, meta, samples):
    def save():
        (run / 'meta.json').write_text(json.dumps(meta, indent=2))
        (run / 'samples.json').write_text(json.dumps(samples))
        subprocess.run(['chown', '-R', 'armada:armada', str(ROOT)], check=True)
    return save


def new_run(label):
    if not label.replace('-', '').replace('_', '').isalnum():
        sys.exit('Use letters, digits, hyphen and underscore in the label')
    ROOT.mkdir(exist_ok=True)
    run = ROOT / (label + '-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    run.mkdir(mode=0o700)
    (ROOT / 'current-run').write_text(str(run))
    return run


def command_check(args):
    pid = game_pid()
    first, time0 = net_bytes(), time.monotonic()
    time.sleep(10)
    second = net_bytes()
    rate = (second['rx'] - first['rx']) / (time.monotonic() - time0) / 1024
    snap = snapshot('check', pid)
    meminfo = snap['mem']
    swap_used = (meminfo['SwapTotal'] - meminfo['SwapFree']) / 1024
    brightness = {snapshot('check', pid)['brightness'] for _ in range(1)}
    report = {
        'external_power': external_power(), 'game_running': pid is not None,
        'wifi_receive_kib_per_s': round(rate, 1), 'swap_used_mib': round(swap_used),
        'mem_available_mib': round(meminfo['MemAvailable'] / 1024),
        'brightness': sorted(brightness), 'skin_c': snap['skin'] / 1000,
        'battery_temp_c': snap['board']['battery'] / 1000, 'battery_percent': snap['battery']['capacity'],
        'thermal_unit': snap['cpu'],
    }
    problems = []
    if report['external_power']:
        problems.append('phone is plugged in')
    if rate > 100:
        problems.append(f'network receiving {rate:.0f} KiB/s (a download is running)')
    if swap_used > 4000:
        problems.append(f'{swap_used:.0f} MiB of swap in use; quit GTA/launcher or restart the session')
    report['problems'] = problems
    print(json.dumps(report, indent=2))
    sys.exit(1 if problems else 0)


def command_cooldown(args):
    refuse_external_power(args)
    run = new_run(args.label)
    samples, meta = [], metadata(args, game_pid())
    meta['phase'] = 'cooldown'
    save = writer(run, meta, samples)
    start, quiet = time.monotonic(), 0
    try:
        while True:
            sample = snapshot('cooldown', None)
            samples.append(sample)
            reason = safety(sample, args, None, need_game=False)
            if reason:
                raise RuntimeError('Safety stop: ' + reason)
            quiet = quiet + 1 if at_gate(sample, args) else 0
            if quiet >= args.gate_samples:
                meta['gate_met'] = True
                break
            if time.monotonic() - start > args.cooldown_max_minutes * 60:
                meta['gate_met'] = False
                break
            if len(samples) % 12 == 0:
                save()
            time.sleep(10)
        meta['cooldown_seconds'] = round(time.monotonic() - start, 1)
        meta['result'] = 'cooled'
        last = samples[-1]
        print(f"Cooldown gate {'MET' if meta['gate_met'] else 'NOT MET'} after {meta['cooldown_seconds']} s: "
              f"skin {last['skin'] / 1000:.1f} C, battery {last['board']['battery'] / 1000:.1f} C, "
              f"die {last['die_max'] / 1000:.1f} C, brightness {last['brightness']}", flush=True)
    except BaseException as error:
        meta['result'] = str(error)
        raise
    finally:
        meta['finished'] = datetime.datetime.now().astimezone().isoformat()
        save()
        print('Run directory:', run, flush=True)
    if not meta.get('gate_met'):
        sys.exit(3)


def command_run(args):
    pid = game_pid()
    if pid is None:
        sys.exit('GTA is not running')
    refuse_external_power(args)
    run = Path(read(ROOT / 'current-run') or '')
    if not run.is_dir() or not run.name.startswith(args.label + '-'):
        sys.exit('No matching cooldown for label ' + args.label + '; run `cooldown` first')
    meta = json.loads((run / 'meta.json').read_text())
    samples = json.loads((run / 'samples.json').read_text())
    meta.update({'phase': 'run', 'run_args': vars(args), 'result': 'started', 'gta_pid': pid})
    save = writer(run, meta, samples)
    csv_before = set(HOME.glob('*.csv'))
    mango_on = False
    try:
        focus = focused_app()
        meta['focused_app_at_start'] = focus
        if focus != GAME_APP:
            raise RuntimeError(f'GTA is not the focused app (focus {focus}); check the launch')
        screenshot(run / 'start.png')
        end = time.monotonic() + args.warmup_seconds
        while time.monotonic() < end:
            sample = snapshot('warmup', pid)
            samples.append(sample)
            reason = safety(sample, args, pid)
            if reason:
                raise RuntimeError('Safety stop: ' + reason)
            time.sleep(2)
        meta['run_start_mono'] = time.monotonic()
        mangohud_logging(1)
        mango_on = True
        run_end = meta['run_start_mono'] + args.minutes * 60
        next_save = time.monotonic() + 60
        while time.monotonic() < run_end:
            sample = snapshot('run', pid)
            samples.append(sample)
            reason = safety(sample, args, pid)
            if reason:
                raise RuntimeError('Safety stop: ' + reason)
            if time.monotonic() >= next_save:
                save()
                next_save += 60
            time.sleep(2)
        meta['result'] = 'complete'
    except BaseException as error:
        meta['result'] = str(error)
        raise
    finally:
        try:
            if mango_on:
                mangohud_logging(2)
                time.sleep(1.5)
            for index, path in enumerate(sorted(set(HOME.glob('*.csv')) - csv_before)):
                path.rename(run / ('mangoapp-%d-%s' % (index, path.name)))
            if game_pid():
                screenshot(run / 'end.png')
        finally:
            meta['finished'] = datetime.datetime.now().astimezone().isoformat()
            save()
            print('Run saved:', run, meta['result'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    cool = sub.add_parser('cooldown')
    cool.add_argument('label')
    cool.add_argument('--cooldown-max-minutes', type=float, default=20)
    cool.add_argument('--gate-samples', type=int, default=6, help='consecutive 10 s samples at the gate')
    common_args(cool)
    run = sub.add_parser('run')
    run.add_argument('label')
    run.add_argument('--minutes', type=float, default=15)
    run.add_argument('--warmup-seconds', type=int, default=20)
    common_args(run)
    sub.add_parser('check')
    args = parser.parse_args()
    {'cooldown': command_cooldown, 'run': command_run, 'check': command_check}[args.command](args)


if __name__ == '__main__':
    main()
