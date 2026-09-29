#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import subprocess

spec = importlib.util.spec_from_file_location('policy', Path(__file__).with_name('charge-policy.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

class PolicyTest(unittest.TestCase):
    def test_wireless_source_precedence_and_no_pd_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bat, usb, wls, aux = (root / x for x in ['bat', 'usb', 'wls', 'aux'])
            for d in [bat, usb, wls, aux]: d.mkdir()
            (wls / 'online').write_text('1')
            (usb / 'online').write_text('1'); (usb / 'usb_type').write_text('[PD_PPS]')
            self.assertFalse(p.wireless_selected(usb, wls))
            (usb / 'usb_type').write_text('[Unknown] PD')
            self.assertTrue(p.wireless_selected(usb, wls))
            (usb / 'online').write_text('0')
            for key, value in {'temp': '320', 'health': 'Good', 'constant_charge_current': '1000000'}.items():
                (bat / key).write_text(value)
            state = aux / 'houji_charger_state'
            state.write_text('verify_process=0\npd_verified=0\nbattery_authentic=1\n')
            _, message = p.tick(bat, usb, str(state), current_limit=500000, wireless=True)
            self.assertIn('wireless policy active', message)
            self.assertEqual((bat / 'constant_charge_current').read_text(), '500000\n')
            self.assertFalse((aux / 'houji_charger_verify_idle').exists())
            (wls / 'online').write_text('0')
            self.assertFalse(p.wireless_selected(usb, wls))

    def test_wireless_authenticates_only_the_battery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bat, usb, wls, aux = (root / x for x in ['bat', 'usb', 'wls', 'aux'])
            for d in [bat, usb, wls, aux / 'houji_charger']: d.mkdir(parents=True)
            for key, value in {'temp': '320', 'health': 'Good', 'constant_charge_current': '500000'}.items():
                (bat / key).write_text(value)
            (usb / 'online').write_text('0'); (usb / 'usb_type').write_text('[Unknown]')
            (wls / 'online').write_text('1'); (aux / 'houji_charger_state').touch()
            authentic = aux / 'houji_charger/authentic'; authentic.write_text('0')
            (aux / 'houji_charger/verify_process').write_text('0')
            agent, blob = root / 'agent', root / 'blob'; agent.touch(); blob.touch()
            def authenticate(command, **kwargs):
                self.assertEqual(command[2], '--gauge')
                authentic.write_text('1')
                return SimpleNamespace(returncode=0)
            runner = Mock(side_effect=authenticate)
            args = dict(bat=bat, usb=usb, wls=wls, aux_pattern=str(aux / 'houji_charger_state'),
                        agent=agent, blob=blob, runner=runner)
            auth = p.StockAuthentication()
            self.assertIn('wireless', auth.step(**args, now=1))
            self.assertIn('wireless', auth.step(**args, now=2))
            self.assertEqual(runner.call_count, 1)

    def test_wireless_ramp_uses_its_own_ceiling(self):
        values = dict(pa_therm0=30000, quiet_therm=30000, charger_therm0=30000,
                      cpu_therm=30000, battery=30000, wifi_therm=30000, usb_therm=30000)
        control = p.StockChargingControl(); state = {'battery_authentic': 1}
        for t in range(20):
            self.assertEqual(control.step(values, state, 500000, 500000, False, t, True, usb_online=False)[0], 500000)
        for t in range(20, 140):
            vote, _ = control.step(values, state, 10000000, 15600000, False, t, True, usb_online=False)
            self.assertLessEqual(vote, 10000000)
        self.assertEqual(vote, 10000000)
        self.assertEqual(control.step(values, {'battery_authentic': 1, 'pd_verified': 1},
                                      10000000, 15600000, False, 140, False)[0], 500000)

    def test_wireless_uses_monitor_levels_and_usb_online_gates_sic(self):
        # Warm virtual skin with a healthy battery: stock wireless mitigation
        # is level 2, while wired SIC would restrict FCC to its 4.6 A floor.
        values = dict(pa_therm0=40000, quiet_therm=40000, charger_therm0=40000,
                      cpu_therm=40000, battery=37000, wifi_therm=40000, usb_therm=40000)
        state = {'battery_authentic': 1}
        control = p.StockChargingControl()
        for t in range(100):
            vote, level = control.step(values, state, 500000, 10000000,
                                       False, t, True, usb_online=False)
        self.assertEqual(vote, 10000000)  # Firmware still enforces XM81 level 2.
        self.assertEqual(level, 2)
        # Both stock monitor tables remain current when the screen turns on.
        _, level = control.step(values, state, 500000, 10000000,
                                True, 100, True, usb_online=False)
        self.assertEqual((control.wired_level, level), (11, 4))
        # Even with wireless selected, a measured USB-online transition must
        # discard the old ramp and re-enable the stock USB-gated calculation.
        self.assertEqual(control.step(values, state, 500000, 10000000,
                                      True, 101, True, usb_online=True)[0], 500000)
        for t in range(102, 200):
            vote, _ = control.step(values, state, 500000, 10000000,
                                   True, t, True, usb_online=True)
        self.assertEqual(vote, 4600000)
        with self.assertRaises(ValueError):
            control.step(values, state, 500000, 10000000, True, 200, True, usb_online=None)

    def test_stock_controller_ramp_and_sensor_failure_recovery(self):
        values = dict(pa_therm0=30000, quiet_therm=30000, charger_therm0=30000,
                      cpu_therm=30000, battery=30000, wifi_therm=30000, usb_therm=30000)
        verified = {'battery_authentic': 1, 'pd_verified': 1}
        control = p.StockChargingControl()
        votes = [control.step(values, verified, 1000000, 3000000, False, t)[0]
                 for t in range(25)]
        self.assertEqual(votes[:4], [500000, 500000, 500000, 750000])
        self.assertEqual(max(votes), 3000000)
        self.assertTrue(all(b - a <= 250000 for a, b in zip(votes, votes[1:])))
        # A missed telemetry interval discards the previous high-current ramp.
        self.assertEqual(control.step(values, verified, 3000000, 3000000, False, 30)[0], 500000)
        for t in range(31, 60): control.step(values, verified, 3000000, 3000000, False, t)
        # A connector at its host limit immediately reduces the requested vote.
        hot = dict(values, usb_therm=45000)
        self.assertEqual(control.step(hot, verified, 3000000, 3000000, False, 60)[0], 500000)
        with self.assertRaises(ValueError):
            control.step(values, verified, 3000000, 16000000, False, 61)

    def test_stock_controller_authentication_and_ceiling(self):
        values = dict(pa_therm0=30000, quiet_therm=30000, charger_therm0=30000,
                      cpu_therm=30000, battery=30000, wifi_therm=30000, usb_therm=30000)
        control = p.StockChargingControl()
        verified = {'battery_authentic': 1, 'pd_verified': 1}
        for t in range(20):
            self.assertLessEqual(control.step(values, verified, 1000000, 1000000, False, t)[0], 1000000)
        self.assertEqual(control.step(values, {}, 1000000, 15600000, False, 20)[0], 500000)
        generic = p.StockChargingControl()
        for t in range(100):
            current, _ = generic.step(values, {'battery_authentic': 1, 'pd_verified': 0},
                                      5350000, 15600000, False, t)
            self.assertLessEqual(current, 5350000)
        self.assertEqual(current, 5350000)

    def test_one_amp_requires_all_sensors_and_real_authentication(self):
        values = dict(pa_therm0=30000, quiet_therm=30000, charger_therm0=30000,
                      cpu_therm=30000, battery=30000, wifi_therm=30000, usb_therm=30000)
        verified = {'battery_authentic': 1, 'pd_verified': 1}
        self.assertEqual(p.current_from_thermals(values, verified), 1000000)
        for state in [{}, {'battery_authentic': 0, 'pd_verified': 1},
                      {'battery_authentic': 1, 'pd_verified': 0}]:
            self.assertEqual(p.current_from_thermals(values, state), 500000)
        for name, temperature in [('battery', 14999), ('battery', 38001),
                                  ('usb_therm', 40000), ('cpu_therm', 45000),
                                  ('quiet_therm', 42000)]:
            self.assertEqual(p.current_from_thermals(dict(values, **{name: temperature}), verified), 500000)
        missing = dict(values); del missing['quiet_therm']
        with self.assertRaises(ValueError): p.current_from_thermals(missing, verified)

    def test_authentication_failure_retry_and_temperature(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bat, usb, aux = (root / x for x in ['bat', 'usb', 'aux'])
            for d in [bat, usb, aux / 'houji_charger']: d.mkdir(parents=True)
            for name, value in {'temp': '320', 'health': 'Good', 'constant_charge_current': '500000'}.items():
                (bat / name).write_text(value)
            (usb / 'online').write_text('1')
            (usb / 'usb_type').write_text('[PD]')
            (aux / 'houji_charger_state').touch()
            for name in ['authentic', 'pd_verifed', 'verify_process']:
                (aux / 'houji_charger' / name).write_text('0')
            agent, blob = root / 'agent', root / 'blob'
            agent.touch(); blob.touch()
            args = dict(bat=bat, usb=usb, aux_pattern=str(aux / 'houji_charger_state'), agent=agent, blob=blob)
            auth = p.StockAuthentication()
            failed = Mock(return_value=SimpleNamespace(returncode=2))
            auth.step(**args, now=1, runner=failed)
            self.assertEqual(failed.call_count, 1)  # Gauge failure must stop adapter authentication.
            self.assertEqual(failed.call_args.args[0][2], '--gauge')
            self.assertEqual((aux / 'houji_charger/pd_verifed').read_text(), '0')
            auth.step(**args, now=2, runner=failed)
            self.assertEqual(failed.call_count, 1)
            (bat / 'temp').write_text('401')
            auth.step(**args, now=70, runner=failed)
            self.assertEqual(failed.call_count, 1)
            (bat / 'temp').write_text('320')
            (aux / 'houji_charger/verify_process').write_text('1')
            timeout = Mock(side_effect=subprocess.TimeoutExpired('agent', 30))
            auth.step(**args, now=71, runner=timeout)
            self.assertEqual((aux / 'houji_charger/verify_process').read_text(), '0\n')
            self.assertEqual((aux / 'houji_charger/authentic').read_text(), '0')
            auth.step(**args, now=140, runner=failed)
            auth.step(**args, now=210, runner=failed)
            self.assertEqual(failed.call_count, 2)  # Three attempts, including timeout, exhaust budget.
            # A real battery verification result permits the standard PD path
            # without misreporting failed Xiaomi identity as an ongoing retry.
            (aux / 'houji_charger/authentic').write_text('1')
            (bat / 'constant_charge_current').write_text('3000000')
            message = auth.step(**args, now=211, runner=failed)
            self.assertIn('standard PD bound', message)
            self.assertEqual(failed.call_count, 2)
            (usb / 'online').write_text('0')
            auth.step(**args, now=220, runner=failed)
            self.assertEqual(auth.attempts, 0)

    def test_authentication_requires_firmware_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bat, usb, aux = (root / x for x in ['bat', 'usb', 'aux'])
            for d in [bat, usb, aux / 'houji_charger']: d.mkdir(parents=True)
            for name, value in {'temp': '320', 'health': 'Good', 'constant_charge_current': '500000'}.items():
                (bat / name).write_text(value)
            (usb / 'online').write_text('1'); (usb / 'usb_type').write_text('[PD]')
            state = aux / 'houji_charger_state'; state.touch()
            for name in ['authentic', 'pd_verifed', 'verify_process']:
                (aux / 'houji_charger' / name).write_text('0')
            agent, blob = root / 'agent', root / 'blob'
            agent.touch(); blob.touch()
            runner = Mock(return_value=SimpleNamespace(returncode=0))
            result = p.StockAuthentication().step(bat, usb, str(state), agent, blob, 1, runner)
            self.assertIn('failed', result)
            self.assertEqual(runner.call_count, 1)

    def test_temperature_and_adapter_bounds(self):
        for temp, expected in [(99, 0), (100, 500000), (320, 500000), (400, 500000), (401, 0)]:
            self.assertEqual(p.desired_current(1, '[PD]', temp, 'Good'), expected)
        self.assertIsNone(p.desired_current(0, '[PD]', 320, 'Good'))
        self.assertIsNone(p.desired_current(1, '[Unknown] PD', 320, 'Good'))
        self.assertEqual(p.desired_current(1, 'PD [PD_PPS]', 320, 'Good'), 500000)
        self.assertEqual(p.desired_current(1, 'PD [PD_PPS]', 401, 'Good'), 0)
        self.assertEqual(p.desired_current(1, '[PD]', 320, 'Overheat'), 0)

    def test_completion_is_idle_only(self):
        for state in [{}, {'verify_process': 1, 'pd_verified': 0},
                      {'verify_process': 0, 'pd_verified': 1}]:
            self.assertFalse(p.verification_idle_needed(state, 100000))
        self.assertTrue(p.verification_idle_needed({'verify_process': 0, 'pd_verified': 0}, 100000))
        self.assertFalse(p.verification_idle_needed({'verify_process': 0, 'pd_verified': 0}, 3000000))

    def test_sysfs_sequence_and_rate_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bat, usb, aux = (root / x for x in ['bat', 'usb', 'aux'])
            for d in [bat, usb, aux]: d.mkdir()
            for name, value in {'temp': '320', 'health': 'Good', 'constant_charge_current': '2000000'}.items():
                (bat / name).write_text(value)
            for name, value in {'online': '1', 'usb_type': '[PD]', 'input_current_limit': '100000'}.items():
                (usb / name).write_text(value)
            state = aux / 'houji_charger_state'
            state.write_text('verify_process=0\npd_verified=0\nbattery_authentic=0\n')
            next_idle, _ = p.tick(bat, usb, str(state), now=1)
            self.assertEqual((bat / 'constant_charge_current').read_text(), '500000\n')
            idle = aux / 'houji_charger_verify_idle'
            self.assertEqual(idle.read_text(), '0\n')
            idle.unlink()
            p.tick(bat, usb, str(state), next_idle, now=2)
            self.assertFalse(idle.exists())
            (bat / 'temp').write_text('410')
            p.tick(bat, usb, str(state), next_idle, now=32)
            self.assertEqual((bat / 'constant_charge_current').read_text(), '0\n')
            self.assertFalse(idle.exists())
            (bat / 'temp').write_text('bad')
            with self.assertRaises(ValueError): p.tick(bat, usb, str(state), now=60)
            self.assertFalse(idle.exists())

if __name__ == '__main__': unittest.main()
