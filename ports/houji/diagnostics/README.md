# GPU lock-owner / fence-wait trace

For native-sleep resets and persistent CPU-idle/restart breadcrumbs, see
[sleep diagnostics](SLEEP.md).

`gpu-lock-trace.py` instruments the validated Houji `kufs1` and `k20261010` drivers using
tracefs kprobes. It reads structure offsets from that kernel's BTF and refuses
other releases until their function semantics have been checked. It requires
no new kernel or module and does not change GPU eviction or sleep policy.
For `k20261010`, the GEM submission/shrinker, reservation/fence and ww-mutex
sources used by these probes were compared with the previous validated build;
the collector still reads actual structure offsets from the running BTF.

Enable continuous debugging on the phone (persists across reboot):

```sh
sudo systemctl enable --now houji-gpu-lock-trace.service
sudo journalctl -u houji-gpu-lock-trace.service -n 5
```

Use Steam normally. The service has no time or cumulative-write expiry. When
events show `gpu-worker` inside `msm_submit_retire` with an unreturned lock attempt
for two seconds, it saves that capture and stacks/state, removes its probes, and
automatically re-arms after five seconds. Manual captures and collector failures
also restart; boot enablement survives reboot. The service remains opt-in for
fresh images. It does not induce a stall or initiate suspend.

For a visible UI freeze that does not block retirement, preserve the trace now;
the service automatically starts a fresh capture afterward:

```sh
sudo systemctl kill --kill-whom=main --signal=SIGUSR1 houji-gpu-lock-trace.service
```

To stop debugging, save the current capture and disable boot startup:

```sh
sudo systemctl disable --now houji-gpu-lock-trace.service
```

`systemctl stop` alone also stops automatic re-arming, but leaves boot startup
enabled. A direct one-shot invocation, `sudo /usr/libexec/armada/houji-gpu-lock-trace
--seconds 21600`, retains the old six-hour monotonic-time / 8 GiB cumulative-write
limits; stop the service first. Native suspend pauses monotonic time.

Continuous runs get dated directories under
`/var/tmp/houji-gpu-lock-traces/continuous/`. The newest eight runs, including the
current run and interrupted/reset runs, are retained (up to about 512 MiB of
trace chunks plus metadata). Older continuous captures rotate out; copy a run
outside this directory to keep it. Existing one-shot captures in the parent
directory are preserved. Continuous collection still writes to storage and uses
CPU: the original live sample was about 7% of one core and 53 MiB RSS.

Each run contains:

- `trace-*.txt`: continuously drained events, in numbered chunks. At most eight
  8 MiB chunks are retained, plus up to 16 MiB in the per-CPU kernel buffers.
- `start.json`: boot ID, wall/monotonic timestamps, BTF layout and probe
  definitions; the wall clock uses the phone's timezone (Asia/Taipei, UTC+8).
- `dependency-state.json`: in-flight allocations, reclaim waits, retirement
  lock attempts and a bounded set of candidate chains. Checkpoints are written
  every two seconds so file rotation does not erase the identity of an older,
  still-blocked operation.
- `tasks.json`, `meminfo.txt`, `vmstat.txt`: final blocked-thread stacks and
  memory state. No user command lines or GPU-buffer contents are collected.
- `loss-counters.json`, `kprobe-profile.txt`: per-CPU overruns and probe misses.
- `end.json`: stop reason, retained chunks, cleanup errors and bytes written.

A reset can leave only the numbered chunks and the last dependency checkpoint;
the final stacks and loss counters then remain unavailable. A checkpoint with
no pending wait does not establish what happened after that checkpoint. Check
service status and the run's files rather than inferring an expiry from wall time.

The files are root-only because they include kernel addresses and thread IDs.
Raw captures belong in private diagnostic storage, not the repository. An
exclusive process lock prevents overlapping collectors; systemd post-exit cleanup
also removes this collector's probes after an abnormal exit before re-arming.

## What distinguishes a dependency from ordinary contention

1. `alloc` identifies an allocation while the submitting task owns a buffer
   reservation lock. `evict`/`purge` identify reclaim in that same task.
2. `retire` plus `lock` identify the retirement worker's contended reservation
   lock. The mutex owner encodes three flag bits; the collector records the raw
   value and chooses the corresponding BTF-based task PID. `lock_end` and the
   kernel's contention events establish when the acquisition actually finishes.
3. `resv_wait` links the reclaim task to the reservation being waited on.
   `wait` records the actual fence address/context/sequence, and paired return
   probes record success or timeout.
4. For a scheduler-finished fence whose parent is an MSM hardware fence,
   `wait_detail` records both identities, signal flags, the completed counter,
   and the hardware-written sequence. A 32-bit wrap-aware comparison determines
   whether hardware had completed despite the software fence remaining unset.
   Other fence types remain visible in the generic wait records; their layout
   is not interpreted as an MSM fence.
5. A candidate requires matching owner pointer and task ID, simultaneous
   allocation/reclaim context, and a retirement lock attempt without a matching
   return. It records whether the worker's lock equals the allocation lock,
   the reclaim lock, or another lock held by that task.

Kprobe field reads are best-effort, not an atomic snapshot; owner handoff and
concurrent signaling can race them. Per-CPU trace order, ring losses, return
probe misses, pointer reuse and incomplete evidence must be considered before
calling a candidate a proven cycle. Repeated matching waits with a still-blocked
worker are stronger evidence than a single entry. Do not force-signal fences or
reset hardware from this collector.
