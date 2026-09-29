#!/usr/bin/python3
"""Regression cases for the Steam vpower protocol, including its shutdown sentinel."""
import runpy
from pathlib import Path
import unittest

values = runpy.run_path(str(Path(__file__).with_name('houji-steam-power')))['values']


class SteamPowerTests(unittest.TestCase):
    def sample(self, **changes):
        sample = dict(IsPresent=True, Percentage=35.0, State=1,
                      TimeToFull=5520, TimeToEmpty=0)
        sample.update(changes)
        return sample

    def test_live_charging_sample_does_not_request_shutdown(self):
        result = values(self.sample(), False)
        self.assertEqual(result['battery_status'], 'Charging')
        self.assertEqual(result['secs_until_battery_full'], '5520')
        self.assertEqual(result['secs_until_shutdown_request'], '-1')
        self.assertEqual(result['battery_percent'], '35')

    def test_plugged_but_discharging_keeps_runtime(self):
        result = values(self.sample(State=2, TimeToFull=0, TimeToEmpty=4120), False)
        self.assertEqual(result['ac_status'], 'Connected')
        self.assertEqual(result['battery_status'], 'Discharging')
        self.assertEqual(result['secs_until_shutdown_request'], '4120')
        self.assertEqual(result['secs_until_battery_full'], '-1')

    def test_unknown_estimate_and_full_never_request_shutdown(self):
        for state in range(7):
            with self.subTest(state=state):
                result = values(self.sample(State=state, TimeToFull=0), False)
                self.assertEqual(result['secs_until_shutdown_request'], '-1')

    def test_missing_or_invalid_battery_is_not_published(self):
        for changes in [dict(IsPresent=False), dict(Percentage=float('nan')),
                        dict(Percentage=-1), dict(Percentage=101)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                values(self.sample(**changes), False)


if __name__ == '__main__':
    unittest.main()
