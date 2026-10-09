#!/usr/bin/python3
"""Rotation lock in the Game Mode orientation service, without D-Bus or Gamescope."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


class GLibError(Exception):
    pass


gi = types.ModuleType('gi')
repository = types.ModuleType('gi.repository')
repository.Gio = types.SimpleNamespace(DBusCallFlags=types.SimpleNamespace(NONE=0))
repository.GLib = types.SimpleNamespace(Error=GLibError, MainLoop=lambda: None)
sys.modules['gi'] = gi
sys.modules['gi.repository'] = repository
spec = importlib.util.spec_from_file_location('orientation', Path(__file__).with_name('orientation.py'))
orientation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orientation)

SESSION = (1234, '/run/user/1000/gamescope-0', 1)


class Proxy:
    def __init__(self, reading='normal'):
        self.calls = []
        self.reading = reading

    def call_sync(self, method, *args):
        self.calls.append(method)

    def get_name_owner(self):
        return ':1.5'

    def get_cached_property(self, name):
        return types.SimpleNamespace(unpack=lambda: self.reading)


class Lock(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.lock = root / 'houji-rotation-lock'
        self.state = root / 'houji-orientation'
        for name, value in (('LOCK', self.lock), ('STATE', self.state)):
            patcher = mock.patch.object(orientation, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(orientation, 'compositor', return_value=SESSION)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.run = mock.patch.object(orientation.subprocess, 'run').start()
        self.addCleanup(mock.patch.stopall)

    def test_lock_values(self):
        self.assertIsNone(orientation.locked_orientation(self.lock))
        for value, expected in (('left\n', 'left'), ('upsidedown', 'upsidedown'),
                                ('left-up', None), ('', None), ('../etc', None)):
            self.lock.write_text(value)
            self.assertEqual(orientation.locked_orientation(self.lock), expected, value)

    def applied(self):
        return [call.args[0][1] for call in self.run.call_args_list]

    def test_lock_applies_once_releases_sensor_and_unlock_follows_it_again(self):
        service = orientation.Orientation()
        proxy = Proxy(reading='left-up')
        service.proxy, service.claimed = proxy, True
        service.session = SESSION
        self.lock.write_text('right\n')
        service.tick()
        service.tick()
        self.assertEqual(self.applied(), ['right'])
        self.assertEqual(proxy.calls, ['ReleaseAccelerometer'])
        self.assertFalse(service.claimed)
        self.assertEqual(self.state.read_text(), 'right\n')
        self.lock.write_text('normal')
        service.tick()
        self.assertEqual(self.applied(), ['right', 'normal'])
        # Unlocked: claim the sensor again and follow it once the reading is stable.
        self.lock.unlink()
        with mock.patch.object(orientation.time, 'monotonic', side_effect=[0, 0, 0, 1, 1]):
            service.tick()
            service.tick()
        self.assertEqual(proxy.calls, ['ReleaseAccelerometer', 'ClaimAccelerometer'])
        self.assertEqual(self.applied(), ['right', 'normal', 'left'])
        self.assertEqual(self.state.read_text(), 'left\n')

    def test_failed_rotation_while_locked_retries_later(self):
        service = orientation.Orientation()
        service.session = SESSION
        self.lock.write_text('left')
        self.run.side_effect = OSError('helper missing')
        service.tick()
        self.assertIsNone(service.applied)
        self.assertGreater(service.retry_at, 0)


if __name__ == '__main__':
    unittest.main()
