#!/usr/bin/env python3
import importlib.util
import math
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('houji_gyro', Path(__file__).with_name('houji-gyro.py'))
gyro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gyro)


class ControllerTest(unittest.TestCase):
    def test_only_configs_with_the_imu_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a.yaml').write_text('name: GameSir X5 Lite\nsource_devices:\n'
                                         '  - group: imu\n    iio:\n      name: bmi-houji-imu\n')
            (root / 'b.yaml').write_text('name: "Other pad"\n')
            self.assertEqual(gyro.motion_controllers([root]), {'GameSir X5 Lite'})

    def test_needs_a_connected_controller_with_a_gamepad_target(self):
        names = {'GameSir X5 Lite'}
        live = {'Name': 'GameSir X5 Lite', 'SourceDevicePaths': ['/dev/input/event6'],
                'TargetDevices': ['/org/shadowblip/InputPlumber/devices/target/gamepad0']}
        self.assertTrue(gyro.wants_motion(live, names))
        # A persistent composite device left with only the IMU, or none.
        self.assertFalse(gyro.wants_motion(dict(live, SourceDevicePaths=['/dev/iio:device2']), names))
        self.assertFalse(gyro.wants_motion(dict(live, SourceDevicePaths=[]), names))
        # The X2s in Plasma: keyboard target only.
        self.assertFalse(gyro.wants_motion(
            dict(live, TargetDevices=['/org/shadowblip/InputPlumber/devices/target/keyboard0']), names))
        self.assertFalse(gyro.wants_motion(dict(live, Name='Other pad'), names))


class SampleTest(unittest.TestCase):
    def test_deck_units(self):
        line = gyro.deck_sample((0, 0, 9.80665), (math.radians(1), 0, 0), gyro.ROTATIONS['normal'])
        ax, ay, az, gx, gy, gz = map(int, line.split())
        self.assertEqual((ax, ay, az), (0, 0, 16384))
        # InputPlumber multiplies by scale 1e-6, 180/pi and 12: 16.384 per degree/s.
        self.assertAlmostEqual(gx * 1e-6 * 180 / math.pi * 12, 16.384, places=2)
        self.assertEqual((gy, gz), (0, 0))

    def test_upright_screen_reads_gravity_up_in_every_orientation(self):
        g = 9.80665
        device_up = {'normal': (0, g, 0), 'left': (-g, 0, 0),
                     'right': (g, 0, 0), 'upsidedown': (0, -g, 0)}
        for name, accel in device_up.items():
            x, y, z = gyro.ROTATIONS[name](*accel)
            self.assertEqual((x, y, z), (0, g, 0), name)

    def test_unknown_orientation_is_portrait(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'current'
            path.write_text('sideways\n')
            self.assertEqual(gyro.orientation(path), 'normal')
            path.write_text('left\n')
            self.assertEqual(gyro.orientation(path), 'left')
            self.assertEqual(gyro.orientation(Path(tmp) / 'missing'), 'normal')


if __name__ == '__main__':
    unittest.main()
