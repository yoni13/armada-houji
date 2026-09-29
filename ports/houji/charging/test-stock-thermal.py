#!/usr/bin/env python3
import unittest
from stock_thermal import WirelessThermal, WiredThermal, SicThermal, trunc_div


class ThermalTest(unittest.TestCase):
    def test_wired_table_screen_on_and_off(self):
        for temp, expected in [(33000, 0), (34000, 5), (36000, 7),
                               (37500, 11), (45000, 15)]:
            self.assertEqual(WiredThermal().update(temp, True), expected)
            self.assertEqual(WiredThermal().update(temp, False), 0)

    def test_wireless_thresholds_and_display_encoding(self):
        for temp, on, off in [(36000, 0, 0), (36800, 3, 1), (37900, 4, 2),
                              (41300, 10, 8), (44500, 15, 15), (50000, 15, 15)]:
            self.assertEqual(WirelessThermal().update(temp, True), on)
            self.assertEqual(WirelessThermal().update(temp, False), off)

    def test_wireless_cooling_hysteresis(self):
        thermal = WirelessThermal()
        self.assertEqual(thermal.update(37900, True), 4)
        self.assertEqual(thermal.update(37000, True), 4)
        self.assertEqual(thermal.update(36800, True), 3)
        self.assertEqual(thermal.update(36001, False), 1)
        self.assertEqual(thermal.update(36000, False), 0)

    def test_sic_constant_temperature_and_delta(self):
        self.assertEqual(SicThermal().update((33000,) * 3, 500000), 15600000)
        self.assertEqual(SicThermal().update((37200,) * 3, 6000000), 6000000)
        # At 37.0 C: 0.2 C below target adds 20 mA; 0.1 C warming subtracts 650 mA.
        self.assertEqual(SicThermal().update((37000, 36900, 36800), 6000000), 5370000)
        # Large positive/negative deltas must clamp to the stock stage limits.
        self.assertEqual(SicThermal().update((36000, 37000, 38000), 12000000), 13500000)
        self.assertEqual(SicThermal().update((38000, 37000, 36000), 5000000), 4600000)

    def test_sic_hot_stages_and_recovery(self):
        thermal = SicThermal()
        for temp, expected in [(46000, 300000), (45501, 300000), (45500, 500000),
                               (44500, 1000000), (44000, 2500000)]:
            self.assertEqual(thermal.update((temp,) * 3, 500000), expected)

    def test_invalid_samples_and_c_integer_math(self):
        self.assertEqual(trunc_div(-1999, 1000), -1)
        for history in [(32000,), (32000, None, 32000), (32000, 200000, 32000)]:
            with self.assertRaises(ValueError): SicThermal().update(history, 500000)
        with self.assertRaises(ValueError): WirelessThermal().update(32000, None)


if __name__ == '__main__':
    unittest.main()
