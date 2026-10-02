#!/usr/bin/env python3
"""Exercise the actual switch helper with isolated SDDM state and services."""
import configparser
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'ports/houji/runtime/usr/libexec/armada/houji-session-switch'


class SessionSwitchTests(unittest.TestCase):
    def test_switch_roundtrip_overrides_upstream_and_legacy_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conf = root / 'conf'
            conf.mkdir()
            (conf / 'armada.conf').write_text('[Autologin]\nUser=armada\nRelogin=true\n')
            for name in ['zz-holo-autologin.conf', 'zz-steamos-autologin.conf',
                         'zzt-holo-temp-login.conf', 'zzt-steamos-temp-login.conf']:
                (conf / name).write_text('[Autologin]\nSession=armada-plasma.desktop\n')
            script = root / 'switch'
            script.write_text(SCRIPT.read_text().replace('/etc/sddm.conf.d', str(conf))
                              .replace('/run/houji-session-switch.lock', str(root / 'lock')))
            for name in ['systemctl', 'runuser']:
                stub = root / name
                stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >>"$TEST_LOG"\n')
                stub.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'],
                       TEST_LOG=str(root / 'calls'))
            for mode, expected in [('gamescope', 'gamescope-session-steam.desktop'),
                                   ('plasma', 'armada-plasma-mobile.desktop'),
                                   ('gamescope', 'gamescope-session-steam.desktop')]:
                result = subprocess.run(['bash', str(script), mode], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                settings = configparser.ConfigParser()
                settings.read(sorted(conf.glob('*.conf')))
                self.assertEqual(settings['Autologin']['Session'], expected)
                self.assertEqual(settings['Autologin']['User'], 'armada')
                self.assertFalse((conf / 'zz-holo-autologin.conf').exists())
                self.assertFalse(list(conf.glob('zzt-*-temp-login.conf')))
            calls = (root / 'calls').read_text()
            self.assertIn('stop graphical-session.target', calls)
            self.assertIn('unset-environment WAYLAND_DISPLAY DISPLAY XAUTHORITY', calls)


if __name__ == '__main__':
    unittest.main()
