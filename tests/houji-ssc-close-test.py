#!/usr/bin/env python3
"""Exercise the patched SSC driver lifecycle with a mock sensor transport.

Pass the pinned, patched iio-sensor-proxy source directory as the argument.
The actual driver is compiled against GLib; no sensor hardware is needed.
"""
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
assert (source / 'src/drv-ssc-accel.c').is_file()
with tempfile.TemporaryDirectory(prefix='houji-ssc-close-') as directory:
    root = Path(directory)
    (root / 'libssc-sensor.h').write_text('#define SSC_SENSOR_NAME "name"\n')
    (root / 'libssc-sensor-accelerometer.h').write_text('''
#include <gio/gio.h>
typedef GObject SSCSensorAccelerometer;
G_DEFINE_AUTOPTR_CLEANUP_FUNC(SSCSensorAccelerometer, g_object_unref)
SSCSensorAccelerometer *ssc_sensor_accelerometer_new_sync(GCancellable *, GError **);
gboolean ssc_sensor_accelerometer_open_sync(SSCSensorAccelerometer *, GCancellable *, GError **);
gboolean ssc_sensor_accelerometer_close_sync(SSCSensorAccelerometer *, GCancellable *, GError **);
''')
    (root / 'test.c').write_text('''
#include "drv-ssc-accel.c"

static unsigned int disable_count, reading_count, expected_disables, released;
static guint measurement_signal;

gboolean apply_mount_matrix(const AccelVec3 vecs[3], AccelVec3 *accel)
{
    return TRUE;
}

gboolean ssc_sensor_accelerometer_open_sync(SSCSensorAccelerometer *sensor,
                                          GCancellable *cancel, GError **error)
{
    return TRUE;
}

gboolean ssc_sensor_accelerometer_close_sync(SSCSensorAccelerometer *sensor,
                                           GCancellable *cancel, GError **error)
{
    unsigned int before = reading_count;
    disable_count++;
    /* QMI completion can dispatch pending reports during synchronous close. */
    g_signal_emit(sensor, measurement_signal, 0, 1.0f, 2.0f, 3.0f);
    g_assert_cmpuint(reading_count, ==, before);
    return TRUE;
}

static void reading(SensorDevice *device, gpointer value, gpointer context)
{
    reading_count++;
}

static void finalized(gpointer data, GObject *object)
{
    g_assert_cmpuint(disable_count, ==, expected_disables);
    released++;
}

static SensorDevice *new_device(void)
{
    SensorDevice *device = g_new0(SensorDevice, 1);
    DrvData *data = g_new0(DrvData, 1);
    data->sensor = g_object_new(G_TYPE_OBJECT, NULL);
    g_object_weak_ref(data->sensor, finalized, NULL);
    device->priv = data;
    device->callback_func = reading;
    return device;
}

int main(void)
{
    measurement_signal = g_signal_new("measurement", G_TYPE_OBJECT,
        G_SIGNAL_RUN_LAST, 0, NULL, NULL, NULL, G_TYPE_NONE, 3,
        G_TYPE_FLOAT, G_TYPE_FLOAT, G_TYPE_FLOAT);
    SensorDevice *device = new_device();
    expected_disables = 1;
    ssc_accelerometer_set_polling(device, TRUE);
    g_signal_emit(((DrvData *)device->priv)->sensor, measurement_signal, 0,
                  1.0f, 2.0f, 3.0f);
    g_assert_cmpuint(reading_count, ==, 1);
    /* Closing with an outstanding client must drain the subscription. */
    ssc_accelerometer_close(device);
    g_assert_cmpuint(released, ==, 1);

    device = new_device();
    ssc_accelerometer_close(device);
    g_assert_cmpuint(disable_count, ==, 1);
    g_assert_cmpuint(released, ==, 2);

    device = new_device();
    expected_disables = 2;
    ssc_accelerometer_set_polling(device, TRUE);
    ssc_accelerometer_set_polling(device, FALSE);
    ssc_accelerometer_close(device);
    g_assert_cmpuint(disable_count, ==, 2);
    g_assert_cmpuint(released, ==, 3);
    return 0;
}
''')
    flags = shlex.split(subprocess.check_output(
        ['pkg-config', '--cflags', '--libs', 'gio-2.0', 'gudev-1.0'], text=True))
    subprocess.run(['cc', '-O2', '-ffunction-sections', '-fdata-sections',
                    '-Wl,--gc-sections', '-I' + str(root), '-I' + str(source / 'src'),
                    str(root / 'test.c'), str(source / 'src/accel-scale.c'),
                    '-o', str(root / 'test'), *flags], check=True)
    subprocess.run([str(root / 'test')], check=True)
print('SSC close: active, inactive, and already-stopped lifecycles passed')
