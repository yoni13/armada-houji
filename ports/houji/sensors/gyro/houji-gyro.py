#!/usr/bin/python3
"""Feed the phone's motion sensors to InputPlumber for Steam's gyro aiming.

Houji Settings turns gyro aiming on by creating SETTING. While it is on, a
controller whose InputPlumber config lists the houji-imu device is connected
with a gamepad target, and the phone is not in light sleep, this service loads
the houji-imu module, streams the SSC gyroscope and accelerometer through
libssc and writes each sample to the module's IIO device. InputPlumber adds the
motion to that controller's virtual Steam Deck controller. Otherwise the
sensors are closed and the module unloaded, so neither the sensor DSP nor
InputPlumber does any work. The sleep hook stops this service before native
sleep.
"""
import ctypes
import json
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
from gi.repository import Gio, GLib

SETTING = Path('/etc/armada/houji-gyro-aim')
STATE = Path('/run/houji-gyro/state')
LIGHT_SLEEP = Path('/run/armada/fake-suspend.active')
ORIENTATION = Path('/run/houji-orientation/current')
CONFIG_DIRS = (Path('/etc/inputplumber/devices.d'), Path('/usr/share/inputplumber/devices'))
IIO_NAME = 'bmi-houji-imu'
MODULE = 'houji_imu'
RATES = {'gyroscope': 200.0, 'accelerometer': 50.0}
# Steam Deck units: 16384 per g and 16.384 per degree/s. InputPlumber passes
# the accelerometer through and multiplies the gyroscope (scale 1e-6) by
# 180/pi * 12, so the gyroscope gets rad/s * 16.384/12.
ACCEL_UNIT = 16384 / 9.80665
GYRO_UNIT = 1e6 * 16.384 / 12
# SSC reports in Android's device frame: x right and y up in portrait, z out of
# the screen. A Steam Deck uses the same axes relative to its screen, so rotate
# into the orientation the screen currently uses.
ROTATIONS = {
    'normal': lambda x, y, z: (x, y, z),
    'left': lambda x, y, z: (y, -x, z),      # USB port on the left
    'right': lambda x, y, z: (-y, x, z),     # USB port on the right
    'upsidedown': lambda x, y, z: (-x, -y, z),
}
IP = 'org.shadowblip.InputPlumber'
IP_ROOT = '/org/shadowblip/InputPlumber'
COMPOSITE = 'org.shadowblip.Input.CompositeDevice'
SSC_ENABLE_CONTINUOUS = 513


def motion_controllers(dirs=CONFIG_DIRS):
    """Names of the InputPlumber configs that take the houji-imu device."""
    names = set()
    for directory in dirs:
        for path in directory.glob('*.yaml'):
            try:
                text = path.read_text()
            except OSError:
                continue
            match = re.search(r'^name:\s*(.+?)\s*$', text, re.M)
            if IIO_NAME in text and match:
                names.add(match.group(1).strip('"\''))
    return names


def wants_motion(props, names):
    """A configured controller, really connected, feeding a gamepad target.

    A persistent composite device outlives its controller with no sources; the
    X2s in Plasma has only a keyboard target. Neither needs motion, and the
    houji-imu device alone must not recreate a composite device.
    """
    return (props.get('Name') in names
            and any(path.startswith(('/dev/input/', '/dev/hidraw'))
                    for path in props.get('SourceDevicePaths', []))
            and any('/target/gamepad' in path for path in props.get('TargetDevices', [])))


def controller_connected(bus, names):
    flags = Gio.DBusCallFlags.NO_AUTO_START
    try:
        xml, = bus.call_sync(IP, IP_ROOT, 'org.freedesktop.DBus.Introspectable', 'Introspect',
                             None, GLib.VariantType('(s)'), flags, 2000, None).unpack()
    except GLib.Error:
        return False
    for node in re.findall(r'<node name="(CompositeDevice[0-9]+)"', xml):
        try:
            props, = bus.call_sync(IP, f'{IP_ROOT}/{node}', 'org.freedesktop.DBus.Properties',
                                   'GetAll', GLib.Variant('(s)', (COMPOSITE,)),
                                   GLib.VariantType('(a{sv})'), flags, 2000, None).unpack()
        except GLib.Error:
            continue
        if wants_motion(props, names):
            return True
    return False


def orientation(path=ORIENTATION):
    try:
        value = path.read_text().strip()
    except OSError:
        return 'normal'
    return value if value in ROTATIONS else 'normal'


def deck_sample(accel, gyro, rotate):
    """One houji-imu sample line from SSC m/s^2 and rad/s readings."""
    values = ([round(v * ACCEL_UNIT) for v in rotate(*accel)]
              + [round(v * GYRO_UNIT) for v in rotate(*gyro)])
    return ' '.join(map(str, values)) + '\n'


class GError(ctypes.Structure):
    _fields_ = [('domain', ctypes.c_uint32), ('code', ctypes.c_int), ('message', ctypes.c_char_p)]


MEASUREMENT = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_float, ctypes.c_float,
                               ctypes.c_float, ctypes.c_void_p)


class Sensors:
    """The SSC gyroscope and accelerometer through libssc.

    Each sensor object is created once and only closed and reopened: libssc
    queues measurements on the main loop with an unowned pointer to it.
    """

    def __init__(self, on_sample):
        self.ssc = ctypes.CDLL('libssc.so.2')
        self.glib = ctypes.CDLL('libglib-2.0.so.0')
        gobject = ctypes.CDLL('libgobject-2.0.so.0')
        error = ctypes.POINTER(ctypes.POINTER(GError))
        self.glib.g_bytes_new.restype = ctypes.c_void_p
        self.glib.g_bytes_new.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        self.glib.g_bytes_unref.argtypes = [ctypes.c_void_p]
        self.ssc.ssc_sensor_send.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        gobject.g_signal_connect_data.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p,
                                                  ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        self.calls, self.sensors, self.callbacks = {}, {}, []
        self.latest = {name: (0.0, 0.0, 0.0) for name in RATES}
        for name in RATES:
            calls = {}
            for verb in ('new', 'open', 'close'):
                function = getattr(self.ssc, f'ssc_sensor_{name}_{verb}_sync')
                if verb == 'new':
                    function.restype, function.argtypes = ctypes.c_void_p, [ctypes.c_void_p, error]
                else:
                    function.restype = ctypes.c_int
                    function.argtypes = [ctypes.c_void_p, ctypes.c_void_p, error]
                calls[verb] = function
            self.calls[name] = calls

            def measured(_sensor, x, y, z, _data, name=name):
                self.latest[name] = (x, y, z)
                if name == 'gyroscope':
                    on_sample(self.latest['accelerometer'], self.latest['gyroscope'])
            self.callbacks.append(MEASUREMENT(measured))
            failure = ctypes.POINTER(GError)()
            sensor = calls['new'](None, ctypes.byref(failure))
            if not sensor:
                raise RuntimeError(f'SSC {name} unavailable: {failure.contents.message.decode()}')
            gobject.g_signal_connect_data(sensor, b'measurement',
                                          ctypes.cast(self.callbacks[-1], ctypes.c_void_p),
                                          None, None, 0)
            self.sensors[name] = sensor
        self.opened = set()

    def open(self):
        for name, rate in RATES.items():
            failure = ctypes.POINTER(GError)()
            if not self.calls[name]['open'](self.sensors[name], None, ctypes.byref(failure)):
                self.close()
                raise RuntimeError(f'SSC {name} did not open: {failure.contents.message.decode()}')
            self.opened.add(name)
            # libssc opens at the first advertised rate (about 25 Hz). Enable
            # again with SscEnableConfigRequest.sample_rate (field 1, float).
            payload = b'\x0d' + struct.pack('<f', rate)
            data = self.glib.g_bytes_new(payload, len(payload))
            self.ssc.ssc_sensor_send(self.sensors[name], SSC_ENABLE_CONTINUOUS, data,
                                     None, None, None)
            self.glib.g_bytes_unref(data)

    def close(self):
        for name in list(self.opened):
            self.calls[name]['close'](self.sensors[name], None, None)
            self.opened.discard(name)
        self.latest = {name: (0.0, 0.0, 0.0) for name in RATES}


def imu_sample_path(root=Path('/sys/bus/iio/devices')):
    for device in root.glob('iio:device*'):
        try:
            if (device / 'name').read_text().strip() == IIO_NAME:
                return device / 'sample'
        except OSError:
            continue
    return None


def publish(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_name('.state.tmp')
    temporary.write_text(json.dumps({'state': state}) + '\n')
    temporary.replace(STATE)


class Gyro:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.sensors = None
        self.fd = None
        self.rotate = ROTATIONS['normal']
        self.state = None
        self.retry_at = 0
        self.loop = GLib.MainLoop()

    def write(self, accel, gyro):
        if self.fd is not None:
            try:
                os.write(self.fd, deck_sample(accel, gyro, self.rotate).encode())
            except OSError:
                pass

    def start(self):
        subprocess.run(['modprobe', MODULE], check=True, timeout=10)
        for _ in range(20):
            path = imu_sample_path()
            if path:
                break
            GLib.usleep(100_000)
        else:
            raise RuntimeError('houji-imu device did not appear')
        self.fd = os.open(path, os.O_WRONLY | os.O_CLOEXEC)
        if self.sensors is None:
            self.sensors = Sensors(self.write)
        self.sensors.open()

    def stop(self):
        if self.sensors:
            self.sensors.close()
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        subprocess.run(['modprobe', '-r', MODULE], check=False, timeout=10)

    def tick(self):
        if not SETTING.exists():
            wanted = 'off'
        elif LIGHT_SLEEP.exists() or not controller_connected(self.bus, motion_controllers()):
            wanted = 'waiting'
        else:
            wanted = 'active'
        self.rotate = ROTATIONS[orientation()]
        if self.state == 'error' and wanted != 'off' and GLib.get_monotonic_time() < self.retry_at:
            return True
        if wanted != self.state:
            try:
                if wanted == 'active':
                    self.start()
                elif self.state == 'active' or self.state is None:
                    self.stop()
                print('Gyro aiming: ' + {'off': 'off', 'waiting': 'waiting for a controller',
                                         'active': 'streaming'}[wanted], flush=True)
                self.state = wanted
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                print('Gyro aiming unavailable: ' + str(error), flush=True)
                self.stop()
                wanted = self.state = 'error'
                self.retry_at = GLib.get_monotonic_time() + 30_000_000
            publish(wanted)
        return True

    def quit(self):
        self.stop()
        publish('off')
        self.loop.quit()
        return False

    def run(self):
        self.tick()
        GLib.timeout_add(1000, self.tick)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.quit)
        self.loop.run()


if __name__ == '__main__':
    if os.geteuid():
        raise SystemExit('houji-gyro must run as root')
    Gyro().run()
