#!/usr/bin/env python3
import unittest
from thermal_policy import (Monitor, Policy, cap_state, gpu_state, ss_target,
                            step_towards, NORMAL_CPU)

# Linux tables on the development handset (descending).
PRIME = (3052800, 2995200, 2937600, 2880000, 2803200, 2745600, 2688000, 2630400,
         2553600, 2496000, 2438400, 2380800, 2304000, 2246400, 2169600, 2112000,
         2035200, 1939200, 1824000, 1708800, 1593600, 1478400, 1363200, 1248000,
         1132800, 1017600, 902400, 787200, 672000, 576000, 480000)
GPU = tuple(f * 1_000_000 for f in (834, 770, 720, 680, 629, 578, 500, 422, 366, 310, 231))


class ThermalPolicyTest(unittest.TestCase):
    def test_ss_uses_the_highest_trigger_reached(self):
        trig, targets = NORMAL_CPU[7]
        self.assertIsNone(ss_target(trig, targets, 24999))
        self.assertEqual(ss_target(trig, targets, 25000), 2630400)
        self.assertEqual(ss_target(trig, targets, 40500), 1363200)
        self.assertEqual(ss_target(trig, targets, 60000), 1017600)
        # No hysteresis: cooling below a trigger releases that step at once.
        self.assertEqual(ss_target(trig, targets, 39999), 1708800)

    def test_monitor_hysteresis_and_contiguous_levels(self):
        m = Monitor((46000, 48000), (44000, 45000), ('a', 'b'))
        self.assertIsNone(m.update(45999))
        self.assertEqual(m.update(46000), 'a')
        self.assertEqual(m.update(44500), 'a')
        self.assertEqual(m.update(48000), 'b')
        self.assertEqual(m.update(45500), 'b')
        self.assertEqual(m.update(45000), 'a')
        self.assertIsNone(m.update(44000))
        # A higher threshold alone is not a contiguous level.
        m = Monitor((10, 20), (5, 15), ('a', 'b'))
        m.active = [False, True]
        self.assertIsNone(m.update(9))

    def test_reverse_monitor_counts_from_the_low_end(self):
        m = Monitor((1, 3), (2, 4), ('severe', 'mild'), reverse=True)
        self.assertIsNone(m.update(50))
        self.assertEqual(m.update(3), 'mild')
        self.assertEqual(m.update(1), 'severe')
        self.assertEqual(m.update(2), 'mild')
        self.assertEqual(m.update(3), 'mild')
        self.assertIsNone(m.update(4))
        with self.assertRaises(ValueError):
            Monitor((1,), (1,), ('x',), reverse=True)
        with self.assertRaises(ValueError):
            Monitor((10,), (12,), ('x',))

    def test_cooling_states(self):
        self.assertEqual(cap_state(PRIME, None), 0)
        self.assertEqual(cap_state(PRIME, 4000000), 0)
        self.assertEqual(cap_state(PRIME, 2630400), 7)
        self.assertEqual(cap_state(PRIME, 2600000), 8)
        self.assertEqual(cap_state(PRIME, 1), len(PRIME) - 1)
        self.assertEqual(gpu_state(GPU, 0), 0)
        self.assertEqual(gpu_state(GPU, 2), 1)   # stock 770 MHz
        self.assertEqual(gpu_state(GPU, 3), 2)   # stock 720 MHz
        self.assertEqual(gpu_state(GPU, 6), 5)   # stock 578 MHz
        self.assertEqual(gpu_state(GPU, 1), 0)   # 834 is the Linux top
        self.assertEqual(step_towards(3, 7), 4)
        self.assertEqual(step_towards(7, 3), 6)
        self.assertEqual(step_towards(5, 5), 5)

    def test_normal_profile(self):
        p = Policy()
        caps, gpu, paused = p.update(24000, 80)
        self.assertEqual(caps, {0: None, 2: None, 5: None, 7: None})
        self.assertEqual((gpu, paused), (2, set()))
        caps, gpu, paused = p.update(46500, 80)
        self.assertEqual(caps, {0: 1344000, 2: 1286400, 5: 1286400, 7: 1132800})
        self.assertEqual((gpu, paused), (2, set()))
        caps, gpu, paused = p.update(50000, 80)
        self.assertEqual(paused, {3, 4, 7})
        _, _, paused = p.update(49000, 80)
        self.assertEqual(paused, {3, 4, 7})
        _, _, paused = p.update(48000, 80)
        self.assertEqual(paused, set())
        self.assertEqual(p.update(12000, 80)[1], 0)

    def test_game_mode_prefers_the_gpu(self):
        p = Policy(game=True)
        caps, gpu, _ = p.update(45000, 80)
        self.assertEqual(gpu, 0)
        self.assertEqual(caps[7], 1132800)
        self.assertEqual(p.update(46000, 80)[1], 2)
        self.assertEqual(p.update(48000, 80)[1], 3)
        self.assertEqual(p.update(45000, 80)[1], 2)
        self.assertEqual(p.update(44000, 80)[1], 0)

    def test_low_battery_combines_with_heat(self):
        p = Policy()
        caps, gpu, paused = p.update(26000, 3)
        self.assertEqual(caps, {0: 2035200, 2: 1286400, 5: 1286400, 7: 2630400})
        self.assertEqual(paused, {4, 7})
        caps, _, paused = p.update(48000, 1)
        self.assertEqual(caps[0], 787200)
        self.assertEqual(caps[2], 844800)
        self.assertEqual(paused, {3, 4, 7})
        g = Policy(game=True)
        self.assertEqual(g.update(30000, 1)[1], 6)
        self.assertEqual(g.update(30000, 1)[2], {2, 3, 4, 7})

    def test_rejects_bad_readings(self):
        with self.assertRaises(ValueError):
            Policy().update(150000, 50)
        with self.assertRaises(ValueError):
            Policy().update(30000, 101)
        with self.assertRaises(ValueError):
            Policy().update(30000.5, 50)


if __name__ == '__main__':
    unittest.main()
