#!/usr/bin/env python3
"""Load Houji's stock speaker protection and factory calibration, leaving it muted.

Called by the Houji UCM profile before enabling the speaker path.
The calibration file belongs to this phone; never copy it between phones.
"""
import hashlib
from pathlib import Path
import struct
import subprocess
import time


CARD = "Xiaomi14"
FIRMWARE_HASH = "1bb7ea70dd6b8e01ca5563cb051c025e1c8f361c57c28780ac559c0fc6b55f87"
COEFFICIENT_HASHES = {
    "T": "03f862ba7dd720d046ec659e48552436b042e8ef654bf55c96059daf2a83016f",
    "B": "9c2ee7b8fba1467f543608d8fce5b48e2b8606cf10ab9f6de4b85c6e62252ac2",
}


def cset(name, value):
    subprocess.run(["amixer", "-q", "-c", CARD, "cset", "name=" + name,
                    str(value)], check=True, timeout=5)


def prepare():
    for side in COEFFICIENT_HASHES:
        cset(side + " PCM Source", "None")
        cset(side + " Digital PCM Volume", 0)
        cset(side + " Analog PCM Volume", 0)
        stem = Path("/usr/lib/firmware/cirrus") / (
            "cs35l41-dsp1-spk-prot-xiaomi-houji-" + side.lower())
        for extension, expected in [(".wmfw", FIRMWARE_HASH),
                                    (".bin", COEFFICIENT_HASHES[side])]:
            actual = hashlib.sha256(Path(str(stem) + extension).read_bytes()).hexdigest()
            if actual != expected:
                raise RuntimeError("Unexpected protection firmware: " + str(stem) + extension)

    calibration = Path("/run/houji/persist/audio/crus_calr.bin").read_bytes()
    if len(calibration) != 8:
        raise RuntimeError("Missing or invalid factory calibration")
    values = struct.unpack("<II", calibration)
    if not all(0 < value < 65536 for value in values):
        raise RuntimeError("Invalid factory calibration values")

    for side, cal_r in zip(["T", "B"], values):
        cset(side + " DSP1 Firmware", "Protection")
        cset(side + " DSP1 Preload Switch", 1)
        # The preload workqueue creates the firmware controls asynchronously.
        for attempt in range(30):
            control = "name=" + side + " DSP1 Protection cd CAL_R"
            result = subprocess.run(["amixer", "-c", CARD, "cget", control],
                                    capture_output=True, timeout=5)
            if result.returncode == 0:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError(side + " protection controls did not appear")
        # Exact wm_halo_apply_calibration sequence from the stock driver.
        for algorithm, name, value in [
            ("400a4", "MAX_LRCLK_DELAY", 32),
            ("cd", "CAL_R", cal_r),
            ("cd", "CAL_CHECKSUM", cal_r + 1),
            ("cd", "CAL_STATUS", 1),
        ]:
            control = f"{side} DSP1 Protection {algorithm} {name}"
            data = ",".join(f"0x{x:02x}" for x in struct.pack(">I", value))
            cset(control, data)
            output = subprocess.check_output(
                ["amixer", "-c", CARD, "cget", "name=" + control], text=True, timeout=5)
            if ": values=" + data not in output:
                raise RuntimeError("Calibration readback failed: " + control)
        print(side + ": protection and factory calibration ready; output muted")


if __name__ == "__main__":
    prepare()
