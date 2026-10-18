// SPDX-License-Identifier: GPL-2.0-only
/* Optional kufs1 observer: persistent printk breadcrumbs, no power-policy changes. */
#include <linux/atomic.h>
#include <linux/kprobes.h>
#include <linux/module.h>
#include <linux/pm.h>
#include <linux/suspend.h>
#include <linux/tracepoint.h>
#include <linux/utsname.h>

#define PREFIX "houji-sleep-observer: "
#define LIMIT 256
static atomic_t cycle = ATOMIC_INIT(0);
static atomic_t records = ATOMIC_INIT(0);
static atomic_t suppressed = ATOMIC_INIT(0);

static void idle_record(const char *phase, unsigned int cpu, unsigned int state, bool s2idle)
{
	if (!s2idle)
		return;
	if (atomic_inc_return(&records) > LIMIT) {
		atomic_inc(&suppressed);
		return;
	}
	pr_info(PREFIX "cycle=%d cpu=%u psci-%s state=0x%x\n",
		atomic_read(&cycle), cpu, phase, state);
}

static void idle_enter(void *data, unsigned int cpu, unsigned int state, bool s2idle)
{
	idle_record("enter", cpu, state, s2idle);
}

static void idle_exit(void *data, unsigned int cpu, unsigned int state, bool s2idle)
{
	idle_record("exit", cpu, state, s2idle);
}

static void suspend_phase(void *data, const char *action, int val, bool start)
{
	if (strcmp(action, "machine_suspend"))
		return;
	if (atomic_inc_return(&records) <= LIMIT)
		pr_info(PREFIX "cycle=%d machine-%s val=%d\n",
			atomic_read(&cycle), start ? "enter" : "exit", val);
	else
		atomic_inc(&suppressed);
}

static int sleep_notify(struct notifier_block *nb, unsigned long event, void *data)
{
	if (event == PM_SUSPEND_PREPARE) {
		atomic_inc(&cycle);
		atomic_set(&records, 0);
		atomic_set(&suppressed, 0);
		pr_info(PREFIX "cycle=%d prepare\n", atomic_read(&cycle));
	} else if (event == PM_POST_SUSPEND) {
		pr_info(PREFIX "cycle=%d post records=%d suppressed=%d\n",
			atomic_read(&cycle), atomic_read(&records), atomic_read(&suppressed));
	}
	return NOTIFY_OK;
}

static struct notifier_block sleep_nb = { .notifier_call = sleep_notify };
static struct {
	const char *name;
	void *probe;
	struct tracepoint *tp;
	bool registered;
} events[] = {
	{ "psci_domain_idle_enter", idle_enter },
	{ "psci_domain_idle_exit", idle_exit },
	{ "suspend_resume", suspend_phase },
};

static void discover(struct tracepoint *tp, void *priv)
{
	int i;
	for (i = 0; i < ARRAY_SIZE(events); ++i)
		if (!strcmp(tp->name, events[i].name))
			events[i].tp = tp;
}

static int restart_probe(struct kprobe *p, struct pt_regs *regs)
{
	pr_emerg(PREFIX "Linux restart path=%s comm=%s pid=%d\n",
		p->symbol_name, current->comm, task_pid_nr(current));
	return 0;
}
NOKPROBE_SYMBOL(restart_probe);

static struct kprobe resets[] = {
	{ .symbol_name = "kernel_restart", .pre_handler = restart_probe },
	{ .symbol_name = "emergency_restart", .pre_handler = restart_probe },
	{ .symbol_name = "machine_restart", .pre_handler = restart_probe },
	{ .symbol_name = "psci_sys_reset", .pre_handler = restart_probe },
};
static int reset_count;
static bool notifier_registered;

static void cleanup(void)
{
	int i;
	while (reset_count)
		unregister_kprobe(&resets[--reset_count]);
	if (notifier_registered)
		unregister_pm_notifier(&sleep_nb);
	for (i = ARRAY_SIZE(events) - 1; i >= 0; --i)
		if (events[i].registered)
			tracepoint_probe_unregister(events[i].tp, events[i].probe, NULL);
	tracepoint_synchronize_unregister();
}

static int __init observer_init(void)
{
	int i, ret;
	if (strcmp(utsname()->release, "7.2.6-armada-houji-kufs1") &&
	    strcmp(utsname()->release, "7.2.6-armada-houji-k20261010"))
		return -EINVAL;
	for_each_kernel_tracepoint(discover, NULL);
	for (i = 0; i < ARRAY_SIZE(events); ++i) {
		if (!events[i].tp) {
			ret = -ENOENT;
			goto fail;
		}
		ret = tracepoint_probe_register(events[i].tp, events[i].probe, NULL);
		if (ret)
			goto fail;
		events[i].registered = true;
	}
	ret = register_pm_notifier(&sleep_nb);
	if (ret)
		goto fail;
	notifier_registered = true;
	for (i = 0; i < ARRAY_SIZE(resets); ++i) {
		ret = register_kprobe(&resets[i]);
		if (ret)
			goto fail;
		reset_count++;
	}
	pr_info(PREFIX "armed; s2idle-only PSCI breadcrumbs, %d records/cycle, Linux restart probes\n", LIMIT);
	return 0;
fail:
	cleanup();
	return ret;
}

static void __exit observer_exit(void)
{
	cleanup();
	pr_info(PREFIX "disarmed\n");
}
module_init(observer_init);
module_exit(observer_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Houji kufs1 sleep/reset breadcrumb observer");
