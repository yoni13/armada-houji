// SPDX-License-Identifier: GPL-2.0-only
/*
 * Motion readings supplied by user space, for InputPlumber.
 *
 * The Xiaomi 14 (houji) reaches its accelerometer and gyroscope only through
 * the sensor DSP. houji-gyro reads them with libssc and writes each sample to
 * this IIO device, whose name matches InputPlumber's BMI IMU driver. That
 * driver polls the raw channels and adds the motion to the controller's
 * virtual Steam Deck controller. houji-gyro loads this module only while gyro
 * aiming is on and a configured controller is connected, so InputPlumber does
 * not poll an idle device.
 *
 * houji-gyro writes values already in the units InputPlumber needs. With the
 * scales below, the Steam Deck target receives the Deck's own units.
 */

#include <linux/iio/iio.h>
#include <linux/iio/sysfs.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/platform_device.h>

#define HOUJI_IMU_NAME	"bmi-houji-imu"

enum { ACCEL_X, ACCEL_Y, ACCEL_Z, GYRO_X, GYRO_Y, GYRO_Z, AXES };

struct houji_imu {
	struct mutex lock;	/* protects value */
	int value[AXES];
};

#define HOUJI_IMU_CHANNEL(_type, _axis, _address) {			\
	.type = _type,							\
	.modified = 1,							\
	.channel2 = IIO_MOD_##_axis,					\
	.address = _address,						\
	.scan_index = -1,						\
	.info_mask_separate = BIT(IIO_CHAN_INFO_RAW),			\
	.info_mask_shared_by_type = BIT(IIO_CHAN_INFO_SCALE) |		\
				    BIT(IIO_CHAN_INFO_SAMP_FREQ),	\
	.info_mask_shared_by_type_available = BIT(IIO_CHAN_INFO_SCALE) |	\
					      BIT(IIO_CHAN_INFO_SAMP_FREQ),	\
}

static const int houji_imu_accel_scale[] = { 1 };
static const int houji_imu_gyro_scale[] = { 0, 1 };
static const int houji_imu_rate[] = { 200 };

static const struct iio_chan_spec houji_imu_channels[] = {
	HOUJI_IMU_CHANNEL(IIO_ACCEL, X, ACCEL_X),
	HOUJI_IMU_CHANNEL(IIO_ACCEL, Y, ACCEL_Y),
	HOUJI_IMU_CHANNEL(IIO_ACCEL, Z, ACCEL_Z),
	HOUJI_IMU_CHANNEL(IIO_ANGL_VEL, X, GYRO_X),
	HOUJI_IMU_CHANNEL(IIO_ANGL_VEL, Y, GYRO_Y),
	HOUJI_IMU_CHANNEL(IIO_ANGL_VEL, Z, GYRO_Z),
};

static int houji_imu_read_raw(struct iio_dev *indio_dev,
			      struct iio_chan_spec const *chan,
			      int *val, int *val2, long mask)
{
	struct houji_imu *imu = iio_priv(indio_dev);

	switch (mask) {
	case IIO_CHAN_INFO_RAW:
		mutex_lock(&imu->lock);
		*val = imu->value[chan->address];
		mutex_unlock(&imu->lock);
		return IIO_VAL_INT;
	case IIO_CHAN_INFO_SCALE:
		/* Accelerometer: Deck units. Gyroscope: micro-units. */
		if (chan->type == IIO_ACCEL) {
			*val = houji_imu_accel_scale[0];
			return IIO_VAL_INT;
		}
		*val = houji_imu_gyro_scale[0];
		*val2 = houji_imu_gyro_scale[1];
		return IIO_VAL_INT_PLUS_MICRO;
	case IIO_CHAN_INFO_SAMP_FREQ:
		*val = houji_imu_rate[0];
		return IIO_VAL_INT;
	default:
		return -EINVAL;
	}
}

static int houji_imu_read_avail(struct iio_dev *indio_dev,
				struct iio_chan_spec const *chan,
				const int **vals, int *type, int *length,
				long mask)
{
	switch (mask) {
	case IIO_CHAN_INFO_SCALE:
		if (chan->type == IIO_ACCEL) {
			*vals = houji_imu_accel_scale;
			*type = IIO_VAL_INT;
			*length = ARRAY_SIZE(houji_imu_accel_scale);
		} else {
			*vals = houji_imu_gyro_scale;
			*type = IIO_VAL_INT_PLUS_MICRO;
			*length = ARRAY_SIZE(houji_imu_gyro_scale);
		}
		return IIO_AVAIL_LIST;
	case IIO_CHAN_INFO_SAMP_FREQ:
		*vals = houji_imu_rate;
		*type = IIO_VAL_INT;
		*length = ARRAY_SIZE(houji_imu_rate);
		return IIO_AVAIL_LIST;
	default:
		return -EINVAL;
	}
}

static int houji_imu_write_raw(struct iio_dev *indio_dev,
			       struct iio_chan_spec const *chan,
			       int val, int val2, long mask)
{
	/* houji-gyro sets the DSP rate; accept InputPlumber's request. */
	return mask == IIO_CHAN_INFO_SAMP_FREQ ? 0 : -EINVAL;
}

/* One sample, all six axes: "ax ay az gx gy gz". */
static ssize_t sample_store(struct device *dev, struct device_attribute *attr,
			    const char *buf, size_t len)
{
	struct houji_imu *imu = iio_priv(dev_to_iio_dev(dev));
	int v[AXES];

	if (sscanf(buf, "%d %d %d %d %d %d", &v[0], &v[1], &v[2],
		   &v[3], &v[4], &v[5]) != AXES)
		return -EINVAL;

	mutex_lock(&imu->lock);
	memcpy(imu->value, v, sizeof(v));
	mutex_unlock(&imu->lock);

	return len;
}

static IIO_DEVICE_ATTR_WO(sample, 0);

static struct attribute *houji_imu_attributes[] = {
	&iio_dev_attr_sample.dev_attr.attr,
	NULL
};

static const struct attribute_group houji_imu_attribute_group = {
	.attrs = houji_imu_attributes,
};

/*
 * InputPlumber ignores IIO devices without a /dev node, and the IIO core
 * creates one only for a device with buffers or events. An empty event group
 * is enough.
 */
static struct attribute *houji_imu_event_attributes[] = {
	NULL
};

static const struct attribute_group houji_imu_event_group = {
	.attrs = houji_imu_event_attributes,
};

static const struct iio_info houji_imu_info = {
	.read_raw = houji_imu_read_raw,
	.read_avail = houji_imu_read_avail,
	.write_raw = houji_imu_write_raw,
	.attrs = &houji_imu_attribute_group,
	.event_attrs = &houji_imu_event_group,
};

static struct platform_device *houji_imu_pdev;
static struct iio_dev *houji_imu_dev;

static int __init houji_imu_init(void)
{
	struct houji_imu *imu;
	int ret;

	houji_imu_pdev = platform_device_register_simple("houji-imu", -1, NULL, 0);
	if (IS_ERR(houji_imu_pdev))
		return PTR_ERR(houji_imu_pdev);

	houji_imu_dev = iio_device_alloc(&houji_imu_pdev->dev, sizeof(*imu));
	if (!houji_imu_dev) {
		ret = -ENOMEM;
		goto unregister_pdev;
	}

	imu = iio_priv(houji_imu_dev);
	mutex_init(&imu->lock);

	houji_imu_dev->name = HOUJI_IMU_NAME;
	houji_imu_dev->info = &houji_imu_info;
	houji_imu_dev->modes = INDIO_DIRECT_MODE;
	houji_imu_dev->channels = houji_imu_channels;
	houji_imu_dev->num_channels = ARRAY_SIZE(houji_imu_channels);

	ret = iio_device_register(houji_imu_dev);
	if (ret)
		goto free_dev;

	return 0;

free_dev:
	mutex_destroy(&imu->lock);
	iio_device_free(houji_imu_dev);
unregister_pdev:
	platform_device_unregister(houji_imu_pdev);
	return ret;
}

static void __exit houji_imu_exit(void)
{
	struct houji_imu *imu = iio_priv(houji_imu_dev);

	iio_device_unregister(houji_imu_dev);
	mutex_destroy(&imu->lock);
	iio_device_free(houji_imu_dev);
	platform_device_unregister(houji_imu_pdev);
}

module_init(houji_imu_init);
module_exit(houji_imu_exit);

MODULE_DESCRIPTION("Motion readings supplied by user space (Xiaomi 14)");
MODULE_LICENSE("GPL");
