# Native-sleep reset diagnostics

This is separate from the continuously running GPU lock collector. Three
October 17 crash consoles reached `PM: suspend-to-idle` without the matching
return, so successful device callbacks are no longer the last known phase.
Neither that endpoint nor a PMIC PS_HOLD warm reset identifies the initiator.

## Installed on the debug handset

- `houji-sleep-debug.service` loads the optional `houji-sleep-observer.ko` and
  records a boot/service-start anchor. It is enabled at boot on the debug phone.
- `sleep-observer.c` registers existing PSCI idle and suspend tracepoints. It
  prints only **s2idle** CPU power-state entries/returns, machine-suspend
  boundaries and prepare/post markers to the kernel console. Normal awake idle
  does not print. Up to 256 detail records are printed per kernel sleep attempt;
  the post marker reports suppression, so incomplete pairs after that limit
  cannot be interpreted as a fault. Linux `kernel_restart`, `emergency_restart`,
  `machine_restart` and `psci_sys_reset` entry probes print distinct breadcrumbs.
- `sleep-state.py`, installed as `houji_sleep_diagnostics.py`, gives the native
  helper an opt-in, fsynced ledger at `/var/lib/armada/sleep-debug/`. It records
  transaction boundaries and every kernel sleep attempt/return/error, with boot
  ID, UTC+8 wall time, monotonic/boottime, RTC seconds, charger/battery telemetry,
  Power-key counts and suspend statistics. No buffer contents or command lines
  are collected. Four 8 MiB JSONL files rotate by sequence, not wall time.
  Selected clock/boot anchors also go to the ramoops console.
- `/etc/armada/houji-sleep-debug` enables the ledger. Without that marker it does
  no I/O. Diagnostics failing to write do not abort sleep or its cleanup.
- Existing temporary logging settings keep `printk=8`, `console_suspend=N`,
  `pm_debug_messages=1` and fbcon unbound. `pm_print_times=0` removes the large
  per-device callback flood while retaining aggregate phase/idle markers.
- A handset-only journald drop-in raises its bounded allowance from 128 MiB to
  512 MiB, with 32 MiB files and a 30-second sync interval. The explicit sleep
  ledger sync is independent of journald and clock corrections.

The observer uses printk in the idle path and the ledger adds synchronous I/O
before suspend. This instrumentation can affect timing. It does not change CPU
idle states, the suspend mode, device power policy, or restart behavior. A reset
may still lose buffered console messages; absence of a restart marker is not
absolute proof that Linux did not request it. A forced hard reset may erase
RAM-backed pstore altogether.

## Build and check

This module is diagnostic-only, not automatically built into fresh images. Build
against the **matching kernel configuration and ABI**, and revalidate the three
tracepoint prototypes before using another kernel. The current observer refuses
anything other than `7.2.6-armada-houji-kufs1` or the validated release update
`7.2.6-armada-houji-k20261010`. The latter observer is built against that exact
kernel, with matching module BTF; the earlier vermagic adjustment below applies
only to the initial `kufs1` diagnostic deployment.

Copy `Makefile` and `sleep-observer.c` into a private build directory, then use:

```sh
make -C /path/to/matching/kernel M=/absolute/private/module-dir \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- modules
```

The development tree's release label had become `kdryrun`. For the deployed
diagnostic build, running/local BTF layouts of `module`, `tracepoint`, `kprobe`,
`notifier_block` and `task_struct` were checked for equality before adjusting
only module vermagic to `kufs1`. Module BTF was omitted rather than embedding
split BTF referencing the different build. Do not assume changing vermagic makes
an arbitrary build compatible.

Registration, unload and re-registration passed while awake. The native helper's
45 regression tests and three ledger tests passed. No suspend, restart or panic
was induced to exercise the callbacks: the next natural sleep provides that
validation. The service and source are kept here because this is an opt-in debug
deployment, not a new default sleep policy.

## Read and stop

```sh
sudo systemctl status houji-sleep-debug.service
sudo dmesg | grep 'houji-sleep-observer\|houji-sleep-debug'
sudo tail /var/lib/armada/sleep-debug/events.jsonl
```

To disable the additional observer and ledger:

```sh
sudo systemctl disable --now houji-sleep-debug.service
sudo rm /etc/armada/houji-sleep-debug
```

GPU tracing has its own service and remains independently enabled. The original
sleep helper/logging configuration is backed up on the phone under
`/root/houji-sleep-debug-before-20261017T233355/`. Private evidence is kept under
`build/houji-day-restarts-20261017-2312/` on the host.
