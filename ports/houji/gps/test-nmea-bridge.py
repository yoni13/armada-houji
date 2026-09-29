#!/usr/bin/env python3
"""Verify formatting and privacy using synthetic/reference NMEA examples.

No test coordinates in this file were captured from the development handset.
"""
import datetime as dt
import importlib.util
import json
import math
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("bridge", Path(__file__).with_name("nmea-bridge.py"))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class NmeaTest(unittest.TestCase):
    def test_raw_uncertainty_preserved_in_json(self):
        record = {"fix": True, "lat": 48.1173, "lon": 11.5167, "utc_ms": 0,
                  "horizontal_uncertainty_m": 42.5, "horizontal_semi_major_m": 50,
                  "horizontal_semi_minor_m": 20, "horizontal_confidence_percent": 68}
        self.assertEqual(json.loads(bridge.json_frame(record)), record)
        record["fix"] = False
        self.assertEqual(json.loads(bridge.json_frame(record)), {"fix": False})

    def test_unknown_accuracy_is_not_five_metres(self):
        record = json.loads(bridge.json_frame({"fix": True, "lat": 48, "lon": 11, "utc_ms": 0}))
        self.assertIsNone(record["horizontal_uncertainty_m"])
        self.assertIsNone(record["horizontal_confidence_percent"])

    def test_reject_invalid_accuracy(self):
        for name, value in [("horizontal_uncertainty_m", math.nan),
                            ("horizontal_semi_major_m", math.inf),
                            ("horizontal_semi_minor_m", -1),
                            ("horizontal_confidence_percent", 100),
                            ("horizontal_confidence_percent", True)]:
            with self.assertRaises(ValueError):
                bridge.json_frame({"fix": True, "lat": 48, "lon": 11, "utc_ms": 0, name: value})

    def test_reference_position(self):
        utc = int(dt.datetime(1994, 3, 23, 12, 35, 19, tzinfo=dt.timezone.utc).timestamp() * 1000)
        self.assertEqual(bridge.rmc({"fix": True, "lat": 48.1173, "lon": 11.516666666667, "utc_ms": utc}),
                         b"$GNRMC,123519.000,A,4807.038000,N,01131.000000,E,,,230394,,,A*70\r\n")

    def test_no_fix_does_not_forward_stale_coordinates(self):
        self.assertEqual(bridge.rmc({"fix": False, "lat": 48.1173, "lon": 11.5167}),
                         b"$GNRMC,,V,,,,,,,,,,N*4D\r\n")

    def test_minutes_carry_and_hemispheres(self):
        self.assertEqual(bridge.coordinate(12.9999999999, 2), "1300.000000")
        sentence = bridge.rmc({"fix": True, "lat": -90, "lon": -180, "utc_ms": 0}).decode()
        self.assertIn(",A,9000.000000,S,18000.000000,W,", sentence)

    def test_invalid_coordinates_and_timestamp(self):
        for lat, lon, timestamp in [(math.nan, 0, 0), (0, math.inf, 0), (91, 0, 0),
                                    (0, -181, 0), (0, 0, -1), (0, 0, True)]:
            with self.assertRaises(ValueError):
                bridge.rmc({"fix": True, "lat": lat, "lon": lon, "utc_ms": timestamp})

    def socket_delivery(self, output_format):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            probe = root / "probe"
            probe.write_text("#!/usr/bin/env python3\nimport os,sys,time\n"
                             "for i in range(20):\n"
                             " os.write(int(sys.argv[-1]), b'{\"fix\":true,\"utc_ms\":0,\"lat\":48,\"lon\":11,\"horizontal_uncertainty_m\":42.5}\\n'); time.sleep(0.05)\n")
            probe.chmod(0o700)
            path = root / "nmea.sock"
            process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("nmea-bridge.py")),
                                        "--probe", str(probe), "--socket", str(path), "--seconds", "10",
                                        "--format", output_format],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(3)
                    client.connect(str(path))
                    received = b""
                    while b"\n" not in received:
                        received += client.recv(4096)
                    first = received.splitlines()[0]
                    if output_format == "json":
                        self.assertEqual(json.loads(first)["horizontal_uncertainty_m"], 42.5)
                    else:
                        self.assertIn(b"$GNRMC,000000.000,A,4800.000000,N,01100.000000,E,", first)
                self.assertEqual(process.wait(timeout=5), 0, process.stderr.read().decode())
                self.assertFalse(path.exists())
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                process.stderr.close()

    def test_nmea_socket_delivery_and_cleanup(self):
        self.socket_delivery("nmea")

    def test_json_socket_delivery_and_cleanup(self):
        self.socket_delivery("json")


if __name__ == "__main__":
    unittest.main()
