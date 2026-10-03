#!/usr/bin/python3
import importlib.util
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('charging_suspend', Path(__file__).with_name('suspend.py'))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


class ChargingSleepTest(unittest.TestCase):
    def test_power_wake_returns_without_native_suspend(self):
        child = Mock()
        child.wait.return_value = 0
        child.poll.return_value = 0
        active, run = Mock(), Mock()
        self.assertFalse(s.sleep_while_charging(active, Mock(return_value=child), run))
        active.assert_not_called()
        run.assert_not_called()

    def test_detach_or_full_charge_cleans_up_then_uses_native_sleep(self):
        child = Mock()
        child.wait.side_effect = [subprocess.TimeoutExpired('sleep', 2),
                                  subprocess.TimeoutExpired('sleep', 2), 0]
        child.poll.side_effect = [None, 0]
        active, run = Mock(side_effect=[True, False]), Mock()
        self.assertTrue(s.sleep_while_charging(active, Mock(return_value=child), run))
        run.assert_called_once_with([s.LIGHT_SLEEP, 'wake'], check=True, timeout=3)
        child.terminate.assert_not_called()

    def test_failed_cleanup_never_enters_native_sleep(self):
        child = Mock()
        child.wait.side_effect = [subprocess.TimeoutExpired('sleep', 2), 1]
        child.poll.side_effect = [None, 1]
        with self.assertRaises(RuntimeError):
            s.sleep_while_charging(Mock(return_value=False), Mock(return_value=child), Mock())

    def test_detach_during_entry_reissues_wake_after_handler_is_ready(self):
        child = Mock()
        child.wait.side_effect = [subprocess.TimeoutExpired('sleep', 2),
                                  subprocess.TimeoutExpired('sleep', 1), 0]
        child.poll.side_effect = [None, 0]
        run = Mock()
        self.assertTrue(s.sleep_while_charging(Mock(return_value=False), Mock(return_value=child), run))
        self.assertEqual(run.call_count, 2)

    def test_exception_terminates_light_sleep_for_its_cleanup(self):
        child = Mock()
        child.wait.side_effect = [subprocess.TimeoutExpired('sleep', 2), 0]
        child.poll.return_value = None
        with self.assertRaises(RuntimeError):
            s.sleep_while_charging(Mock(side_effect=RuntimeError('test')), Mock(return_value=child), Mock())
        child.terminate.assert_called_once()

    def run_main(self, env):
        with mock.patch.object(s, 'charging_active', return_value=False), \
             mock.patch.object(s.os, 'access', return_value=True), \
             mock.patch.object(s.os, 'execv') as execv, \
             mock.patch.dict(os.environ, env, clear=True):
            s.main()
            return execv, dict(os.environ)

    def test_native_sleep_runs_the_dark_resume_step(self):
        execv, env = self.run_main({})
        execv.assert_called_once_with(s.DISPATCH, [s.DISPATCH])
        self.assertEqual(env['ARMADA_SYSTEMD_SLEEP'], s.NATIVE_SLEEP)

    def test_an_explicit_sleep_override_is_respected(self):
        _, env = self.run_main({'ARMADA_SYSTEMD_SLEEP': '/custom'})
        self.assertEqual(env['ARMADA_SYSTEMD_SLEEP'], '/custom')

    def test_a_missing_dark_resume_step_leaves_the_dispatch_default(self):
        with mock.patch.object(s, 'charging_active', return_value=False), \
             mock.patch.object(s.os, 'access', side_effect=lambda path, mode: path != s.NATIVE_SLEEP), \
             mock.patch.object(s.os, 'execv'), \
             mock.patch.dict(os.environ, {}, clear=True):
            s.main()
            self.assertNotIn('ARMADA_SYSTEMD_SLEEP', os.environ)


if __name__ == '__main__':
    unittest.main()
