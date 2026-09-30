// SPDX-License-Identifier: GPL-2.0-only
/* Keep the shared NFC I/O rail enabled during userspace card emulation. */
#include <linux/device.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/regulator/consumer.h>

static struct device *nfc_device;
static struct regulator *supply;

static int match_nfc(struct device *dev, const void *unused)
{
	return of_device_is_compatible(dev->of_node, "nxp,nxp-nci-i2c");
}

static int __init houji_nfc_power_init(void)
{
	int ret;

	if (!of_machine_is_compatible("xiaomi,houji"))
		return -ENODEV;
	nfc_device = bus_find_device(&i2c_bus_type, NULL, NULL, match_nfc);
	if (!nfc_device)
		return -ENODEV;
	supply = regulator_get(nfc_device, "vddio");
	if (IS_ERR(supply)) {
		ret = PTR_ERR(supply);
		goto put_device;
	}
	ret = regulator_enable(supply);
	if (!ret)
		return 0;
	regulator_put(supply);
put_device:
	put_device(nfc_device);
	return ret;
}

static void __exit houji_nfc_power_exit(void)
{
	regulator_disable(supply);
	regulator_put(supply);
	put_device(nfc_device);
}

module_init(houji_nfc_power_init);
module_exit(houji_nfc_power_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Houji NFC shared supply hold for userspace emulation");
