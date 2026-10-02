#!/usr/bin/env python3
"""Ensure native sleep drains SSC consumers and restores only active services."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / 'ports/houji/runtime/usr/lib/systemd/system-sleep/61-houji-sensors'


class SensorsSuspendTests(unittest.TestCase):
    def check_services(self, active):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for service in active:
                (root / (service + '.service')).touch()
            hook = root / 'hook'
            hook.write_text(HOOK.read_text().replace('/run/armada/houji-sensors-resume', str(root / 'resume')))
            stub = root / 'systemctl'
            stub.write_text('''#!/bin/sh
case "$1" in
is-active) test -f "$TEST_ROOT/$3" ;;
stop) echo "stop $2" >>"$TEST_ROOT/log"; rm -f "$TEST_ROOT/$2" ;;
--no-block)
    test "$2" = start || exit 1
    echo "start $3" >>"$TEST_ROOT/log"; touch "$TEST_ROOT/$3" ;;
*) exit 1 ;;
esac
''')
            stub.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'], TEST_ROOT=str(root))
            for phase in ['pre', 'post']:
                result = subprocess.run(['sh', str(hook), phase, 'suspend'], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                if phase == 'pre':
                    self.assertFalse(list(root.glob('*.service')))
            self.assertEqual({p.stem for p in root.glob('*.service')}, set(active))
            if active:
                calls = (root / 'log').read_text().splitlines()
                stop = [s for s in ['houji-gamescope-orientation', 'iio-sensor-proxy'] if s in active]
                self.assertEqual(calls, ['stop ' + s + '.service' for s in stop] +
                                 ['start ' + s + '.service' for s in reversed(stop)])
            self.assertFalse((root / 'resume').exists())

    def test_both_running(self):
        self.check_services(['houji-gamescope-orientation', 'iio-sensor-proxy'])

    def test_only_proxy_running(self):
        self.check_services(['iio-sensor-proxy'])

    def test_inactive_stays_inactive(self):
        self.check_services([])


if __name__ == '__main__':
    unittest.main()
