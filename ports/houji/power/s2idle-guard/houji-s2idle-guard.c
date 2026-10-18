// SPDX-License-Identifier: GPL-2.0-only
/* Live counterpart of patch 0033 for the already-installed release kernel.
 * It caps only s2idle's shared-domain selection, not awake idle or CPU collapse.
 */
#include <linux/cpu.h>
#include <linux/cpuidle.h>
#include <linux/device.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm_domain.h>
#include <linux/suspend.h>
#include <linux/utsname.h>

static struct device *rsc;
static struct generic_pm_domain *cluster;
static struct dev_power_governor *original;
static struct dev_power_governor guarded;
static unsigned long limited;
module_param(limited, ulong, 0444);
MODULE_PARM_DESC(limited, "s2idle cluster selections limited to cluster collapse");

static bool cluster_suspend_ok(struct dev_pm_domain *domain)
{
	struct generic_pm_domain *pd = pd_to_genpd(domain);

	if (!original->system_power_down_ok(domain))
		return false;
	if (pm_suspend_target_state == PM_SUSPEND_TO_IDLE && pd->state_idx > 0) {
		pd->state_idx = 0;
		limited++;
	}
	return true;
}

static int __init guard_init(void)
{
	unsigned long flags;
	int ret = -EINVAL;

	if (!of_machine_is_compatible("xiaomi,houji") ||
	    strcmp(utsname()->release, "7.2.6-armada-houji-k20261010"))
		return -ENODEV;
	rsc = bus_find_device_by_name(&platform_bus_type, NULL, "17a00000.rsc");
	if (!rsc)
		return -ENODEV;
	if (!rsc->pm_domain)
		goto fail;
	cluster = pd_to_genpd(rsc->pm_domain);
	if (strcmp(cluster->name, "power-domain-cluster") ||
	    !(cluster->flags & GENPD_FLAG_CPU_DOMAIN) ||
	    !(cluster->flags & GENPD_FLAG_IRQ_SAFE) ||
	    cluster->state_count != 2 || !cluster->states[0].data ||
	    !cluster->states[1].data ||
	    *(u32 *)cluster->states[0].data != 0x41000044 ||
	    *(u32 *)cluster->states[1].data != 0x4100c344)
		goto fail;
	cpus_read_lock();
	cpuidle_pause_and_lock();
	/* GENPD_FLAG_CPU_DOMAIN uses raw_slock in this exact kernel. */
	raw_spin_lock_irqsave(&cluster->raw_slock, flags);
	original = cluster->gov;
	if (original && original->system_power_down_ok) {
		guarded = *original;
		guarded.system_power_down_ok = cluster_suspend_ok;
		cluster->gov = &guarded;
		ret = 0;
	}
	raw_spin_unlock_irqrestore(&cluster->raw_slock, flags);
	cpuidle_resume_and_unlock();
	cpus_read_unlock();
	if (ret)
		goto fail;
	pr_info("houji-s2idle-guard: s2idle cluster=0x41000044; awake idle unchanged\n");
	return 0;
fail:
	put_device(rsc);
	rsc = NULL;
	return ret;
}

static void __exit guard_exit(void)
{
	unsigned long flags;

	cpus_read_lock();
	cpuidle_pause_and_lock();
	raw_spin_lock_irqsave(&cluster->raw_slock, flags);
	cluster->gov = original;
	raw_spin_unlock_irqrestore(&cluster->raw_slock, flags);
	cpuidle_resume_and_unlock();
	cpus_read_unlock();
	put_device(rsc);
	pr_info("houji-s2idle-guard: original governor restored; limited=%lu\n", limited);
}
module_init(guard_init);
module_exit(guard_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Houji s2idle APSS collapse guard, preserving native sleep");
