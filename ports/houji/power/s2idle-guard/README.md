# Houji native-sleep APSS collapse guard

Patch `0033` caps only Houji's **suspend-to-idle** cluster selection at
`0x41000044` (cluster collapse), avoiding `0x4100c344` (APSS/LLCC collapse).
CPUs still use rail power collapse; devices suspend and normal wake IRQs work.
Awake runtime-idle selection is unchanged. The governor keeps its latency vetoes.

The release kernel's first retained failure ended during partial PSCI return
from APSS collapse. With the cap, 12 kernel sleep/resume attempts completed in
the same boot, including an RTC smoke test, a Power-key wake after a DSP dark
wake, and six 90-second RTC transactions. All 161 recorded PSCI entries had
matching returns, including bursts of rapid re-entry; no detail records were
suppressed, no suspend failures or new kernel faults occurred. This is tested
containment of the failing transition, not proof of the firmware's reset cause
or of indefinite standby reliability.

The six-transaction run lasted 600 seconds including wake/awake intervals and
reported a 33 mAh charge-counter decrease. The counter updated in large batches,
so this is not a sleep-only power measurement or a comparison against unguarded
sleep. Avoiding APSS/LLCC collapse can cost standby power.

## Existing k20261010 installation

The module here implements the same cap on the already-installed kernel,
without flashing. It checks the board, exact kernel release, CPU-domain flags,
and both expected state encodings before installing a private governor copy.
CPU idle is quiesced and the domain lock held for installation/removal.

```sh
python3 ports/houji/power/s2idle-guard/build.py /path/to/completed/kernel /path/to/output
# Copy the built .ko and the service to the handset, then:
sudo install -m644 houji-s2idle-guard.ko /usr/lib/modules/$(uname -r)/extra/
sudo depmod -a
sudo install -m644 houji-s2idle-guard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now houji-s2idle-guard.service
```

The module must be compiled against the exact completed release kernel, including
its configuration and BTF. Do not rewrite its vermagic for another kernel.
Fresh kernels use patch `0033` directly and do not need the module or service.

Check the installed guard:

```sh
systemctl status houji-s2idle-guard
cat /sys/module/houji_s2idle_guard/parameters/limited
cat /sys/power/mem_sleep
```

`limited` counts capped selections, not whole sleep transactions. Stopping the
oneshot service alone deliberately does not unload an active guard. To restore
the original policy while awake:

```sh
sudo systemctl disable --now houji-s2idle-guard.service
sudo modprobe -r houji_s2idle_guard
```
