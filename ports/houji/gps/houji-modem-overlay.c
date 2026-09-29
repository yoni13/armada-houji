// SPDX-License-Identifier: GPL-2.0-only
/* First MPSS probe is a supervised test even though auto_boot is false.
 * Probe assigns the reserved DSM/QLINK regions through the secure monitor.
 * Deliberately no module_exit: removal after a failed first modem boot is not
 * a proven recovery path. A normal reboot clears this temporary overlay.
 */
#include <linux/module.h>
#include <linux/of.h>
#include "modem-overlay.h"

static int overlay_id;

static int __init houji_modem_init(void)
{
	struct device_node *node;
	bool enabled;

	if (!of_machine_is_compatible("xiaomi,houji"))
		return -ENODEV;
	node = of_find_node_by_path("/soc@0/remoteproc@4080000");
	if (!node)
		return -ENODEV;
	enabled = of_device_is_available(node);
	of_node_put(node);
	if (enabled)
		return -EBUSY;
	return of_overlay_fdt_apply(modem_dtbo, sizeof(modem_dtbo),
				    &overlay_id, NULL);
}
module_init(houji_modem_init);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Houji MPSS candidate; supervised first probe, no autostart");
