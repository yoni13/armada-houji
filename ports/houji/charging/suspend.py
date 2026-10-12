#!/usr/bin/python3
"""Keep light sleep for the entire external-power attachment, even when full."""
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
DETACH_SAMPLES = 3  # Ignore a brief offline report during USB-PD renegotiation.


def charger_connected():
    try:
        # Exit 1 means all supplies are known offline. A failed query is not a
        # detach, and restarting the charging daemon must not change sleep mode.
        result = subprocess.run([POLICY, '--needs-light-sleep'],
                                capture_output=True, text=True, timeout=3)
        return not (result.returncode == 1 and result.stdout.strip() == 'offline')
    except (OSError, subprocess.TimeoutExpired):
        return True


def sleep_while_connected(connected=charger_connected, popen=subprocess.Popen,
                          run=subprocess.run):
    """Return True after confirmed detach; False after the user wakes."""
    child = popen([LIGHT_SLEEP, 'sleep'])
    detached = 0
    try:
        while True:
            try:
                status = child.wait(timeout=2)
                if status:
                    raise RuntimeError(f'light sleep failed: {status}')
                return False  # User pressed Power: finish the sleep lifecycle.
            except subprocess.TimeoutExpired:
                if connected():
                    detached = 0
                    continue
                detached += 1
                if detached < DETACH_SAMPLES:
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
    # Recheck after cleanup too: a charger can reconnect while the light-sleep
    # child is releasing input and thawing apps. Do not enter native sleep then.
    while charger_connected():
        print('Houji: external power connected; using light sleep, including at full charge', flush=True)
        if not sleep_while_connected():
            return
        print('Houji: external power detached; rechecking before configured sleep mode', flush=True)
    if os.access(NATIVE_SLEEP, os.X_OK):
        os.environ.setdefault('ARMADA_SYSTEMD_SLEEP', NATIVE_SLEEP)
    os.execv(DISPATCH, [DISPATCH, *sys.argv[1:]])


if __name__ == '__main__':
    main()
