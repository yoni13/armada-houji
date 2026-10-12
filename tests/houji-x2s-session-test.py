#!/usr/bin/env python3
"""X2s session routing: native desktop identity, Game Mode mapping and hotplug."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'ports/houji/runtime/usr/libexec/armada/houji-x2s-session'
loader = importlib.machinery.SourceFileLoader('x2s', str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
x2s = importlib.util.module_from_spec(spec)
loader.exec_module(x2s)


class Routing(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in [('SOURCE', ROOT/'system_files/usr/share/inputplumber/devices/11-gamesir-x2s.yaml'),
                            ('OVERRIDE', self.root/'devices.d/10-houji-x2s-session.yaml')]:
            p = patch.object(x2s, name, value)
            p.start(); self.addCleanup(p.stop)

    def test_desktop_keeps_original_gamepad_and_consumes_record_power_key(self):
        text = x2s.profile('desktop')
        self.assertIn('group: gamepad\n    ignore: true', text)
        self.assertIn('group: keyboard', text)
        self.assertIn('capability_map_id: gamesir_x2s_record', text)
        self.assertTrue(text.endswith('target_devices:\n  - keyboard\n'))

    def test_game_mode_restores_exact_existing_x2s_mapping(self):
        self.assertEqual(x2s.profile('game'), x2s.HEADER + x2s.SOURCE.read_text())
        with self.assertRaises(ValueError): x2s.profile('unknown')

    def test_virtual_controller_survives_usb_reconnect_in_both_modes(self):
        for mode in ('game', 'desktop'):
            self.assertIn('  auto_manage: true\n  # A USB-PD', x2s.profile(mode))
            self.assertIn('\n  persist: true\n', x2s.profile(mode))

    def make_proc(self, number, executable, comm=None, args=()):
        binary = self.root/'bin'/executable
        binary.parent.mkdir(exist_ok=True)
        binary.touch()
        directory = self.root/str(number)
        directory.mkdir()
        (directory/'exe').symlink_to(binary)
        (directory/'comm').write_text((comm or executable)+'\n')
        (directory/'cmdline').write_bytes(b'\0'.join([str(binary).encode(), *args])+b'\0')
        return directory

    def test_plasma_wins_over_nested_gamescope_and_logout_keeps_mode(self):
        self.assertIsNone(x2s.session_mode(os.getuid(), self.root))
        self.make_proc(1, 'gamescope', args=(b'--steam',))
        self.assertEqual(x2s.session_mode(os.getuid(), self.root), 'game')
        self.make_proc(2, 'kwin_wayland')
        self.assertEqual(x2s.session_mode(os.getuid(), self.root), 'desktop')
        self.assertIsNone(x2s.session_mode(os.getuid()+1, self.root))

    def test_renamed_gamescope_thread_restores_game_mode(self):
        self.make_proc(1, 'gamescope', comm='gamescope-wl', args=(b'--steam',))
        self.assertEqual(x2s.session_mode(os.getuid(), self.root), 'game')

    def test_non_session_gamescope_and_exited_process_are_ignored(self):
        self.make_proc(1, 'gamescope', comm='gamescope-wl')
        for number in (2, 3):
            directory = self.root/str(number); directory.mkdir()
            (directory/'comm').write_text('gamescope\n')
        (self.root/'3/exe').symlink_to(self.root/'missing-binary')
        self.assertIsNone(x2s.session_mode(os.getuid(), self.root))

    def test_custom_override_is_preserved(self):
        x2s.OVERRIDE.parent.mkdir()
        x2s.OVERRIDE.write_text('custom configuration\n')
        with self.assertRaises(RuntimeError): x2s.write_profile(x2s.profile('desktop'))
        self.assertEqual(x2s.OVERRIDE.read_text(), 'custom configuration\n')

    def test_transition_publishes_profile_before_manager_rediscovery(self):
        def restart(*args):
            self.assertIn('ignore: true', x2s.OVERRIDE.read_text())
        with patch.object(x2s, 'run', side_effect=restart) as run:
            x2s.apply('desktop')
            run.assert_called_once_with('systemctl','restart','inputplumber.service')


if __name__ == '__main__':
    unittest.main()
