#!/usr/bin/env python3
"""Check native display handoff, saved state and failed-start recovery."""
import getpass
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'ports/houji/runtime'
HELPER = RUNTIME / 'usr/libexec/armada/houji-gamescope-sleep'


class DisplaySleepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.connector = self.root / 'card0-DSI-1'
        self.connector.mkdir()
        self.enabled = self.connector / 'enabled'
        self.enabled.write_text('enabled\n')
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(str(self.root / 'gamescope-0'))
        self.addCleanup(self.sock.close)
        device_env = self.root / 'device-env'
        device_env.write_text('#!/bin/sh\necho ARMADA_SUSPEND_MODE=${TEST_MODE:-s2idle}\n')
        device_env.chmod(0o755)
        stub = self.root / 'runuser'
        stub.write_text('''#!/bin/bash
value=${@: -1}
echo "$value" >> "$TEST_ROOT/calls"
if [[ "$value" == 1 ]]; then
    echo disabled > "$TEST_ROOT/card0-DSI-1/enabled"
    [[ "${FAIL_OFF:-0}" != 1 ]]
else
    [[ "${FAIL_ON:-0}" != 1 ]] || exit 1
    echo enabled > "$TEST_ROOT/card0-DSI-1/enabled"
fi
''')
        stub.chmod(0o755)
        self.helper = self.root / 'helper'
        self.helper.write_text(HELPER.read_text()
            .replace('state=/run/armada/houji-gamescope-sleep', f'state={self.state}')
            .replace('runtime=/run/user/$session_uid', f'runtime={self.root}')
            .replace('/sys/class/drm/card*-DSI-1', str(self.connector))
            .replace('/usr/libexec/armada/device-env', str(device_env)))
        self.env = dict(os.environ, PATH=str(self.root) + ':' + os.environ['PATH'],
                        ARMADA_SESSION_USER=getpass.getuser(), TEST_ROOT=str(self.root))

    def invoke(self, phase, expected=0, **env):
        result = subprocess.run(['bash', str(self.helper), phase],
                                env=dict(self.env, **env), text=True, capture_output=True)
        self.assertEqual(result.returncode, expected, result.stderr)

    def test_round_trip_and_duplicate_resume(self):
        self.invoke('suspend')
        self.assertEqual(self.enabled.read_text().strip(), 'disabled')
        self.invoke('resume')
        self.invoke('resume')
        self.assertEqual(self.enabled.read_text().strip(), 'enabled')
        self.assertEqual((self.root / 'calls').read_text().splitlines(), ['1', '0'])
        self.assertFalse(self.state.exists())

    def test_inactive_display_stays_off(self):
        self.enabled.write_text('disabled\n')
        self.invoke('suspend')
        self.invoke('resume')
        self.assertFalse((self.root / 'calls').exists())

    def test_light_sleep_keeps_its_existing_handler(self):
        self.invoke('suspend', TEST_MODE='fake')
        self.invoke('resume')
        self.assertFalse((self.root / 'calls').exists())

    def test_plasma_without_gamescope_is_untouched(self):
        (self.root / 'gamescope-0').unlink()
        self.invoke('suspend')
        self.assertFalse(self.state.exists())

    def test_failed_off_request_is_restored(self):
        self.invoke('suspend', expected=1, FAIL_OFF='1')
        self.assertTrue(self.state.exists())
        self.invoke('resume')
        self.assertEqual(self.enabled.read_text().strip(), 'enabled')

    def test_failed_restore_is_retried(self):
        self.invoke('suspend')
        self.invoke('resume', expected=1, FAIL_ON='1')
        self.assertTrue(self.state.exists())
        self.invoke('resume')
        self.assertFalse(self.state.exists())

    def test_ordering_and_failed_start_cleanup(self):
        unit = (RUNTIME / 'usr/lib/systemd/system/houji-gamescope-sleep.service').read_text()
        self.assertIn('Before=sleep.target', unit)
        self.assertIn('StopWhenUnneeded=yes', unit)
        self.assertIn('ExecStopPost=/usr/libexec/armada/houji-gamescope-sleep resume', unit)


if __name__ == '__main__':
    unittest.main()
