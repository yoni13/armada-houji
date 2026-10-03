#!/usr/bin/python3
"""Keep the charger thermal loop running during an attached charging session."""
import os
import signal
import subprocess
import sys

POLICY = '/usr/libexec/armada/houji-charge-policy'
LIGHT_SLEEP = '/usr/libexec/armada/fake-suspend'
DISPATCH = '/usr/libexec/armada/suspend-dispatch'
# suspend-dispatch runs this in place of systemd-sleep for native sleep. It
# keeps sensor DSP wakeups from lighting the screen; see houji-sleep.
NATIVE_SLEEP = '/usr/libexec/armada/houji-sleep'


def charging_active():
    try:
        return (subprocess.run(['systemctl', 'is-active', '--quiet', 'houji-charging'],
                               timeout=3).returncode == 0
                and subprocess.run([POLICY, '--needs-light-sleep'], timeout=3).returncode == 0)
    except (OSError, subprocess.TimeoutExpired):
        return False


def sleep_while_charging(active=charging_active, popen=subprocess.Popen,
                         run=subprocess.run):
    """Return True to continue into native sleep after detach/full/daemon loss."""
    child = popen([LIGHT_SLEEP, 'sleep'])
    try:
        while True:
            try:
                status = child.wait(timeout=2)
                if status:
                    raise RuntimeError(f'light sleep failed: {status}')
                return False  # User pressed Power: finish the sleep lifecycle.
            except subprocess.TimeoutExpired:
                if active():
                    continue
                if child.poll() is not None:
                    if child.returncode:
                        raise RuntimeError(f'light sleep failed: {child.returncode}')
                    return False
                # Let the existing handler thaw apps and release the power
                # key before systemd-sleep freezes processes for native sleep.
                # Detach can happen before wait_for_wake has cleared an old
                # wake flag. Reissue until it is listening or has exited.
                for attempt in range(30):
                    run([LIGHT_SLEEP, 'wake'], check=True, timeout=3)
                    try:
                        status = child.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        continue
                    if status:
                        raise RuntimeError(f'light-sleep cleanup failed: {status}')
                    return True
                raise RuntimeError('light sleep did not finish cleanup')
    finally:
        if child.poll() is None:
            child.terminate()  # Its TERM trap restores the display/input/apps.
            child.wait(timeout=30)


def main():
    # SIGTERM must run the child cleanup even outside systemd's whole-cgroup
    # shutdown. Never strand fake-suspend's input grabs or frozen processes.
    def terminated(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminated)
    signal.signal(signal.SIGINT, terminated)
    if charging_active():
        print('Houji: light sleep keeps charging thermal monitoring active', flush=True)
        if not sleep_while_charging():
            return
        print('Houji: charging ended; returning to configured sleep mode', flush=True)
    if os.access(NATIVE_SLEEP, os.X_OK):
        os.environ.setdefault('ARMADA_SYSTEMD_SLEEP', NATIVE_SLEEP)
    os.execv(DISPATCH, [DISPATCH, *sys.argv[1:]])


if __name__ == '__main__':
    main()
