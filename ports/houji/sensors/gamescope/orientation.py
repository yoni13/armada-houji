#!/usr/bin/python3
"""Forward the corrected SSC orientation to Gamescope's internal display.

Screen orientation uses accelerometer gravity. Gyroscope gamepad/aim emulation
is separate. KWin continues to consume SensorProxy directly in Plasma.
"""
import os
from pathlib import Path
import signal
import stat
import subprocess
import time
from gi.repository import Gio, GLib

ORIENTATIONS = {
    "normal": "normal", "bottom-up": "upsidedown",
    "left-up": "left", "right-up": "right",
}
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
HELPER = "/usr/libexec/armada/houji-gamescope-rotate"
# Written by Houji Settings: one of ORIENTATIONS' values, or absent to follow the sensor.
LOCK = Path("/etc/armada/houji-rotation-lock")
# The orientation last applied, for Houji Settings to show and lock to.
STATE = Path("/run/houji-orientation/current")


def locked_orientation(path=None):
    try:
        value = (LOCK if path is None else path).read_text().strip()
    except OSError:
        return None
    return value if value in ORIENTATIONS.values() else None


def compositor():
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            args = (proc / "cmdline").read_bytes().split(b"\0")
            if not args or args[0] != b"/usr/bin/gamescope":
                continue
            # This panel exceeds the DPU inline rotator's height limit.
            if b"--force-composition-rotation" not in args:
                return None
            for socket in sorted(RUNTIME.glob("gamescope-[0-9]*")):
                if stat.S_ISSOCK(socket.stat().st_mode) and socket.name.rsplit("-", 1)[-1].isdigit():
                    return int(proc.name), str(socket), socket.stat().st_ino
        except (OSError, ProcessLookupError):
            continue
    return None


class Orientation:
    def __init__(self):
        self.proxy = None
        self.claimed = False
        self.owner = None
        self.session = None
        self.pending = None
        self.since = 0.0
        self.applied = None
        self.retry_at = 0.0
        self.loop = GLib.MainLoop()

    def unclaim(self):
        if self.claimed and self.proxy:
            try:
                self.proxy.call_sync("ReleaseAccelerometer", None, Gio.DBusCallFlags.NONE, 2000, None)
            except GLib.Error:
                pass
        self.claimed = False

    def release(self):
        self.unclaim()
        self.pending = self.applied = None

    def apply(self, session, orientation):
        env = dict(os.environ, WAYLAND_DISPLAY=session[1])
        subprocess.run([HELPER, orientation], env=env, check=True, timeout=4)
        self.applied = orientation
        try:
            STATE.write_text(orientation + "\n")
        except OSError:
            pass
        print("Gamescope orientation: " + orientation, flush=True)

    def tick(self):
        current = compositor()
        if current != self.session:
            self.release()
            self.session = current
            print("Gamescope orientation: " + ("session ready" if current else "waiting for shader-rotation session"), flush=True)
        if not current or time.monotonic() < self.retry_at:
            return True
        lock = locked_orientation()
        if lock:
            # A locked screen needs no accelerometer; releasing it lets the sensor idle.
            self.unclaim()
            self.pending = None
            if lock != self.applied:
                try:
                    self.apply(current, lock)
                except (OSError, subprocess.SubprocessError) as exc:
                    print("Gamescope orientation retry: " + str(exc), flush=True)
                    self.retry_at = time.monotonic() + 5
            return True
        try:
            if not self.proxy:
                self.proxy = Gio.DBusProxy.new_for_bus_sync(
                    Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                    "net.hadess.SensorProxy", "/net/hadess/SensorProxy",
                    "net.hadess.SensorProxy", None)
            owner = self.proxy.get_name_owner()
            if owner != self.owner:
                self.claimed = False
                self.owner = owner
                self.applied = None
            if not owner:
                return True
            if not self.claimed:
                self.proxy.call_sync("ClaimAccelerometer", None, Gio.DBusCallFlags.NONE, 3000, None)
                self.claimed = True
            prop = self.proxy.get_cached_property("AccelerometerOrientation")
            orientation = ORIENTATIONS.get(prop.unpack() if prop else "")
            # Undefined/face-up readings retain the previous orientation.
            if not orientation:
                self.pending = None
                return True
            if orientation != self.pending:
                self.pending, self.since = orientation, time.monotonic()
            elif orientation != self.applied and time.monotonic() - self.since >= 0.75:
                self.apply(current, orientation)
        except (GLib.Error, OSError, subprocess.SubprocessError) as exc:
            print("Gamescope orientation retry: " + str(exc), flush=True)
            self.release()
            self.proxy = None
            self.retry_at = time.monotonic() + 5
        return True

    def stop(self, *_):
        self.release()
        self.loop.quit()
        return False

    def run(self):
        GLib.timeout_add(500, self.tick)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.stop)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.stop)
        self.loop.run()


if __name__ == "__main__":
    Orientation().run()
