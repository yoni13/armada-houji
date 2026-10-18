#!/usr/bin/python3
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('charging_suspend', Path(__file__).with_name('suspend.py'))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


class ChargingSleepTest(unittest.TestCase):
    def setUp(self):
        environment = mock.patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        counter = mock.patch.object(s, 'power_key_count', return_value=10)
        self.counter = counter.start()
        self.addCleanup(counter.stop)

    def test_hardware_power_count_uses_all_cpus(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'interrupts'
            path.write_text(' CPU0 CPU1\n 21: 4 8 spmi-pmic-arb pmic_pwrkey\n'
                            ' 22: 2 9 spmi-pmic-arb pmic_resin\n')
            # Call the original function through a freshly loaded module.
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(module.power_key_count(path), 12)
            path.write_text(' 21: invalid spmi-pmic-arb pmic_pwrkey\n')
            self.assertIsNone(module.power_key_count(path))
            path.unlink()
            self.assertIsNone(module.power_key_count(path))

    def test_power_wake_returns_without_native_suspend(self):
        child = Mock()
        child.wait.return_value = 0
        child.poll.return_value = 0
        active, run = Mock(), Mock()
        self.assertFalse(s.sleep_while_connected(active, Mock(return_value=child), run))
        active.assert_not_called()
        run.assert_not_called()

    def test_confirmed_detach_cleans_up_then_uses_native_sleep(self):
        child = Mock()
        child.wait.side_effect = [subprocess.TimeoutExpired('sleep', 2)] * 4 + [0]
        child.poll.side_effect = [None, 0]
        active, run = Mock(side_effect=[True, False, False, False]), Mock()
        self.assertTrue(s.sleep_while_connected(active, Mock(return_value=child), run))
        run.assert_called_once_with([s.LIGHT_SLEEP, 'wake'], check=True, timeout=3)
        child.terminate.assert_not_called()

    def test_failed_cleanup_never_enters_native_sleep(self):
        child = Mock()
        child.wait.side_effect = [subprocess.TimeoutExpired('sleep', 2)] * 3 + [1]
        child.poll.side_effect = [None, 1]
        with self.assertRaises(RuntimeError):
            s.sleep_while_connected(Mock(return_value=False), Mock(return_value=child), Mock())

    def test_detach_during_entry_reissues_wake_after_handler_is_ready(self):
        child = Mock()
        child.wait.side_effect = [subprocess.TimeoutExpired('sleep', 2)] * 3 + [
                                  subprocess.TimeoutExpired('sleep', 1), 0]
        child.poll.side_effect = [None, 0]
        run = Mock()
        self.assertTrue(s.sleep_while_connected(Mock(return_value=False), Mock(return_value=child), run))
        self.assertEqual(run.call_count, 2)

    def test_exception_terminates_light_sleep_for_its_cleanup(self):
        child = Mock()
        child.wait.side_effect = [subprocess.TimeoutExpired('sleep', 2), 0]
        child.poll.return_value = None
        with self.assertRaises(RuntimeError):
            s.sleep_while_connected(Mock(side_effect=RuntimeError('test')), Mock(return_value=child), Mock())
        child.terminate.assert_called_once()

    def run_main(self, env):
        with mock.patch.object(s, 'charger_connected', return_value=False), \
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
        with mock.patch.object(s, 'charger_connected', return_value=False), \
             mock.patch.object(s.os, 'access', side_effect=lambda path, mode: path != s.NATIVE_SLEEP), \
             mock.patch.object(s.os, 'execv'), \
             mock.patch.dict(os.environ, {}, clear=True):
            s.main()
            self.assertNotIn('ARMADA_SYSTEMD_SLEEP', os.environ)

    def test_query_failure_and_daemon_restart_do_not_allow_native_sleep(self):
        for status, output in [(0, 'light\n'), (1, 'offline\n'), (1, ''), (2, ''), (-15, '')]:
            with mock.patch.object(s.subprocess, 'run', return_value=Mock(returncode=status, stdout=output)) as run:
                self.assertEqual(s.charger_connected(), not (status == 1 and output == 'offline\n'))
                run.assert_called_once_with([s.POLICY, '--needs-light-sleep'], capture_output=True, text=True, timeout=3)
        for error in [OSError(), subprocess.TimeoutExpired('policy', 3)]:
            with mock.patch.object(s.subprocess, 'run', side_effect=error):
                self.assertTrue(s.charger_connected())

    def test_brief_offline_reports_do_not_leave_light_sleep(self):
        child = Mock()
        child.wait.side_effect = [subprocess.TimeoutExpired('sleep', 2)] * 5 + [0]
        child.poll.return_value = 0
        connected = Mock(side_effect=[False, False, True, False, False])
        run = Mock()
        self.assertFalse(s.sleep_while_connected(connected, Mock(return_value=child), run))
        run.assert_not_called()
        child.terminate.assert_not_called()

    def test_reconnection_during_cleanup_reenters_light_sleep(self):
        with mock.patch.object(s, 'charger_connected', side_effect=[True, True]), \
                mock.patch.object(s, 'sleep_while_connected', side_effect=[True, False]) as sleep, \
                mock.patch.object(s.os, 'execv') as execv:
            s.main()
            self.assertEqual(sleep.call_count, 2)
            execv.assert_not_called()

    def test_confirmed_detach_dispatches_native_sleep(self):
        with mock.patch.object(s, 'charger_connected', side_effect=[True, False]), \
                mock.patch.object(s, 'sleep_while_connected', return_value=True), \
                mock.patch.object(s.os, 'access', return_value=True), \
                mock.patch.object(s.os, 'execv') as execv, \
                mock.patch.dict(os.environ, {}, clear=True):
            s.main()
            self.assertEqual(os.environ['ARMADA_SYSTEMD_SLEEP'], s.NATIVE_SLEEP)
            self.assertEqual(os.environ[s.HANDOFF_POWER], '10')
            execv.assert_called_once_with(s.DISPATCH, [s.DISPATCH])

    def test_power_during_detach_cleanup_prevents_native_sleep(self):
        for counts in ([10, 11], [None], [10, None]):
            with self.subTest(counts=counts), \
                    mock.patch.object(s, 'charger_connected', return_value=True), \
                    mock.patch.object(s, 'sleep_while_connected', return_value=True), \
                    mock.patch.object(s, 'power_key_count', side_effect=counts), \
                    mock.patch.object(s.os, 'execv') as execv:
                s.main()
                execv.assert_not_called()

    def test_power_during_final_charger_recheck_prevents_native_sleep(self):
        with mock.patch.object(s, 'charger_connected', side_effect=[True, False]), \
                mock.patch.object(s, 'sleep_while_connected', return_value=True), \
                mock.patch.object(s, 'power_key_count', side_effect=[10, 10, 11]), \
                mock.patch.object(s.os, 'execv') as execv:
            s.main()
            execv.assert_not_called()

    def test_power_on_reconnection_keeps_original_handoff_baseline(self):
        # A press after the reconnect check must still cancel the next detach.
        with mock.patch.object(s, 'charger_connected', side_effect=[True, True, False]), \
                mock.patch.object(s, 'sleep_while_connected', return_value=True) as sleep, \
                mock.patch.object(s, 'power_key_count', side_effect=[10, 10, 10, 11, 11]), \
                mock.patch.object(s.os, 'execv') as execv:
            s.main()
            self.assertEqual(sleep.call_count, 2)
            execv.assert_not_called()


if __name__ == '__main__':
    unittest.main()
