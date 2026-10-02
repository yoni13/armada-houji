#!/usr/bin/python3
import importlib.util
from pathlib import Path
import subprocess
import unittest
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


if __name__ == '__main__':
    unittest.main()
