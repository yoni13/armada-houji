# Houji porting history

What was broken on the Xiaomi 14 port, why, and how it was fixed. Current status
and build and install steps are in [README.md](README.md).

**Reading this document**

- Each entry gives the **issue**, its **cause** (where it was found), the **fix**,
  and the **result** that was tested.
- A result describes the development handset only. Where something is not proven,
  the entry says so.
- Work ran through October 2026. An AI agent did most of the investigation and
  implementation. The device owner supplied hardware observations and supervised
  tests.
- Device identifiers, network profiles, coordinates, recordings and raw logs are
  left out on purpose.

## Contents

1. [Boot and storage](#boot-and-storage)
2. [Display and colour](#display-and-colour)
3. [Touchscreen](#touchscreen)
4. [Sleep and resume](#sleep-and-resume)
5. [Armada 20260926 migration](#armada-20260926-migration)
6. [Wi-Fi](#wi-fi)
7. [Sensors and rotation](#sensors-and-rotation)
8. [Battery and charging](#battery-and-charging)
9. [Audio, Bluetooth and haptics](#audio-bluetooth-and-haptics)
10. [USB, OTG and gamepads](#usb-otg-and-gamepads)
11. [GPS](#gps)
12. [NFC](#nfc)
13. [Session switching](#session-switching)
14. [Making the build reproducible](#making-the-build-reproducible)
15. [Smaller updates and CI builds](#smaller-updates-and-ci-builds)
16. [Installing over stock Android](#installing-over-stock-android)
17. [Open problems](#open-problems)

## Boot and storage

### Xiaomi's bootloader rejected early images

- **Issue:** Armada's handheld install path expects a modified ABL and a `/KERNEL`
  image. Xiaomi's stock ABL loads Android boot-v4 partitions. Early candidates
  returned straight to fastboot or hung at the Xiaomi logo.
- **Fix:** Put the Linux Image in `boot`, the initramfs in `init_boot` and the
  board device tree in `vendor_boot`, using the stock load addresses and version
  metadata. Keep Xiaomi's DTBO selectors but replace the Android payload with a
  no-op overlay. Export device-tree symbols (including the real ARM timer as
  `arch_timer`) for firmware-supplied overlays, and match the stock reserved-memory
  and reserved-GPIO ranges.
- **Result:** Stock ABL boots the Linux system, and it is never flashed. Unlocking
  the bootloader does not make arbitrary boot headers or device trees compatible.

### Early systems depended on USB

- **Issue:** Early tests ran from RAM or a computer-supplied USB root filesystem,
  with timed returns to fastboot. Unplugging USB stopped them.
- **Fix:** Install a new ext4 filesystem in `userdata` holding Armada's EROFS base
  and a persistent writable overlay. The initramfs mounts it directly and grows it
  on first boot. Network-root and reboot timers are gone, and Armada's generic
  ABL and bootc disk-update hooks are disabled for this layout.
- **Also:** Armada's generic installer treated the overlay root as a live system
  and offered an installer for an incompatible partition layout. Its visibility
  service is masked and its launcher removed, so installs and updates use this
  port's tools.

### Switching sessions killed its own worker

- **Issue:** Stopping Plasma also stopped the process that was starting Game Mode.
- **Fix:** Session switching runs in a detached system service, so stopping Plasma
  does not kill the worker that starts Game Mode. Gamescope uses direct DRM output
  with forced composition rotation for the portrait panel. See also
  [Session switching](#session-switching).

## Display and colour

### Static and a pink tint

- **Issue:** Stock ABL's framebuffer was not enough for a native display path.
  Early full-screen updates showed coloured noise, and mixed colours looked pink.
- **Cause:** The stock command table bypassed the display controller's RGB
  rendering defaults for Xiaomi's source-rendered native-4:2:2 stream.
- **Fix:** Add the N3 panel's initialisation sequence and 1200 × 2670 mode, with
  RGB101010 DSI and 10-bit DSC. Restore each panel's validated gamma table from
  SMEM where present. Skip the 4:2:2-specific configuration and use the stock
  SM8650 DSC precision setting.
- **Result:** Primary and mixed-colour charts confirmed the gross tint is gone.
  This is not a colour calibration.

### Waking from sleep produced static or reboots

- **Issue:** Waking could show static or reboot the phone. The light-sleep helper
  blanks the OLED and keeps USB runtime power, but Plasma's power manager shuts the
  panel and DSI down independently, so display state was not guaranteed to survive.
- **Investigation:** Display-only off/on cycles usually recovered, but one wake
  stalled before its first frame and reported DPU timeouts. Full logind sleep/wake
  reproduced reboots even with the Armada sleep helper bypassed and the power
  daemon stopped. Holding the GPU and display power domains on, or disabling deep
  CPU idle, did not stop them. Traces reached DPU plane programming on wake, before
  panel initialisation, with no panic stack. Keeping the normal display operating
  point as well as its runtime power let three full cycles pass.
- **Cause:** Linux 7.2.3 called `dev_pm_opp_set_rate(0)` at display shutdown, which
  dropped the required voltage request. On wake, plane registers were written
  before the normal performance update restored it.
- **Fix:** Backport the upstream DPU and DSI power-vote corrections
  [811c38907eab](https://git.kernel.org/linus/811c38907eab0f66c22c5e5708e6f8eab14d76fa)
  and [06b7ba206561](https://git.kernel.org/linus/06b7ba206561619bb34116f49e0ef26b867ce3aa).
  The operating-point request is kept and runtime PM removes and restores it with
  the device's power state. Clock gating and display power-down still work.
- **Result:** Twelve full sleep/wake cycles passed (screen off for 5 to 60 seconds).
  GPU, GMU, MDSS, DPU and DSI reached runtime suspend, and the MDSS power domain
  switched off. There were no reboots, underruns or timeouts. A physical Power-button
  cycle also gave normal wake and touch. Long-term reliability and deep suspend are
  not established.

### Game Mode did not power the display down

- **Issue:** In Steam Game Mode the GPU suspended, but MDSS, DPU, DSI and the panel
  supplies stayed on, because the helper used a backlight-only policy.
- **Fix:** With the DPU/DSI fixes installed, select the helper's DRM display
  policy, which calls Gamescope's `drm_sleep_internal_screen` on sleep and wake.
- **Result:** Three cycles (30, 10 and 60 seconds) confirmed runtime suspend and
  MDSS power-off, with the connector off and panel supplies at zero. Wake restored
  everything with the same boot ID.

### Duplicate DSP interrupts

- **Issue:** Linux 7.2.3 cleared SMP2P's remembered interrupt bits on ordinary
  notifications, replaying unchanged ADSP ready and handover signals.
- **Fix:** Keep that history unless the remote-restart flag changes. A regression
  check covers ordinary notifications and both restart transitions. The ready and
  handover counts now stay at one after boot.
- **Note:** This did not stop the wake resets. The display power-vote fixes above
  address a separate defect.

## Touchscreen

- **Issue:** A Synaptics transport alone produced no useful Linux touch contacts,
  and repeated calibration patterns could not reproduce Xiaomi's host-side
  processing.
- **Fix:** Trace the stock touch library and driver with IDA, add a buffered SPI
  transport, and run the hash-checked stock processing core through a small ARM64
  compatibility adapter. Contacts become Linux multitouch uinput events. Firmware
  and touch configuration come from the pinned public firmware image, and no
  captured touch frames are build inputs.
- **Result:** Continuous tapping and swiping work in Plasma and Steam.

## Sleep and resume

The hardest area. It combined a kernel fault, a firmware watchdog, compositor
timing and userspace ordering, and several early conclusions were later overturned.
This section gives the final understanding first, then the dead ends.

### Native touch resume

- **Issue:** The first device-suspend test hit a touch interrupt racing the parent
  SPI controller's suspend. Its `-ESHUTDOWN` error disabled the IRQ for good and
  left the touch service restarting in a loop.
- **Fix:** Child PM callbacks drain and disable the IRQ before the SPI parent
  suspends and re-enable it after the parent resumes. Xiaomi's temporal decoder
  rejects frame gaps across sleep, so a native system-sleep hook stops it before
  suspend and starts a fresh session afterward, only if it was running. The hook
  starts the service asynchronously.
- **Result:** An RTC test resumes a healthy stream without an unplanned restart.
  Light sleep keeps streaming as before.

### Sensor DSP watchdog after resume (the main failure)

- **Symptom:** After resuming from native sleep, the sensor DSP's watchdog fired.
  Audio, battery and charging readings, sensors and USB-C control all vanished
  together, because they share that DSP. Resume was slow and a normal reboot could
  stall. Once, the audio stack also faulted while recovering the DSP.
- **Cause (first part; see "a second cause" below):** The sensor DSP can send a request ("reverse RPC") to the
  file-service listener in userspace. The listener is frozen during sleep, and the
  mainline FastRPC driver did not wake the system for these requests. The DSP's
  watchdog then expired while the application processor slept. Stock Xiaomi
  `sscrpcd` turns on FastRPC wake-lock control before starting its listener, which
  is how Android avoids this. This was found by comparing the stock `frpc-adsprpc`
  module and `sscrpcd` in IDA with the mainline driver, and by a trace that
  recorded a listener reply while userspace was frozen.
- **Fix (kernel patch `0020`):**
  - For a successful sensor `NEXT2` reply that arrives during suspend, signal a
    bounded hard wake so the listener can answer.
  - Track replies the listener still owes. Refuse the device prepare stage, and so
    abort suspend, while one is outstanding. This closes the gap before userspace
    freezes, where the suspend flag is not yet set.
  - The next `NEXT2` send clears the debt. A generation counter keeps a newer
    request if its callback arrives before that send returns, and a failed send
    keeps the debt.
  - Idle listener waits do not block sleep, and no permanent wake lock is held.
- **Fix (kernel patch `0021`, audio teardown):** Drain ASoC's deferred resume
  worker before an instantiated card is unbound. During DSP recovery that worker
  was walking component lists the teardown was freeing, which caused the earlier
  fault.
- **Result:**
  - An early-after-boot native sleep woke on a real FastRPC reply after about 228
    seconds, before its RTC alarm, with no watchdog and no kernel fault. The trace
    shows the reply and the wake event. I first read the listener's call 1.25
    seconds later as its answer; it was the interrupted call being re-issued (see
    "a second cause").
  - Afterward, speaker playback reached the hardware, accelerometer samples arrived
    and battery queries worked.
  - A later Power-key sleep, woken by a physical tap, also passed. The owner saw
    Steam, touch and the battery indicator working. Kernel counters showed seven
    successful `s2idle` cycles on that boot with no failures, watchdog, DSP crash or
    kernel fault. The test's result files were not kept, so that check rests on the
    owner's report and the counters.
  - The new kernel and every in-tree and port module were built and installed
    together under a separate release name so rollback stayed possible.
- **Not proven:** Deliberately crashing the DSP to exercise the audio-teardown
  patch.

### What the DSP asks for, and a second cause

A debug build of `hexagonrpcd` logged every request. The sensor DSP's
periodic wake-up request is always the same thing: it writes one registry
item, `sns_gyro_cal_dynamic_config_0.poly_para` (the gyroscope's dynamic
calibration), through a temp-file write and rename. It arrives every few
minutes to an hour while the phone sleeps, and roughly every minute or two
while the phone is awake and still. It needs no file I/O beyond that, so
the earlier syscall traces of the woken daemon came back empty for a reason
that mattered: the request had not reached it yet.

- **Issue:** After a wake, the registry write reached the daemon about 11
  seconds to 100 seconds late, not within the roughly 1.3 s that thawing
  takes. The earlier "listener answered 1.25 s after wake" trace was wrong:
  that was the listener re-issuing its `NEXT2` call, not answering.
- **Cause:** The listener waits for the DSP's request in an interruptible
  `FASTRPC_IOCTL_INVOKE`. The freezer wakes it with a fake signal, the call
  returns `-ERESTARTSYS`, and the restarted call builds a new context. The
  DSP's reply lands on the abandoned original context, which is freed, so
  the request payload is dropped and the DSP only delivers it again after
  its own retry. Stock Xiaomi `frpc-adsprpc.ko` has explicit
  `fastrpc_context_interrupt` and `context_restore_interrupted` handling
  for exactly this; mainline has no equivalent. This is the more likely
  root cause of the old sensor watchdog and of the long resume stalls.
- **Fix (kernel patch `0022`):** Wait with
  `TASK_INTERRUPTIBLE | TASK_FREEZABLE`, so the freezer parks the caller in
  place and the original context is the one that returns the request after
  thaw. Real signals still interrupt it.

### How stock Android handles this

Read from the firmware's own `frpc-adsprpc.ko` and `libadsprpc.so`:

- In `fastrpc_handle_rpc_response`, a client with wake control enabled
  triggers `pm_wakeup_ws_event(ws, ws_timeout, true)` for every reply. The
  PM ioctl caps `ws_timeout` at 50 ms.
- `libadsprpc` takes a userspace wakelock (`/sys/power/wake_lock`) when a
  call returns a request and drops it when the listener calls `NEXT2`
  again with the answer. The blocking `NEXT2` itself deliberately holds no
  lock. `sscrpcd` runs with `group wakelock` and `BLOCK_SUSPEND`.
- Android's SystemSuspend loop writes `mem` to `/sys/power/state` again as
  soon as no wakelock is held. The kernel wakes briefly, the request is
  served, and the system sleeps again; the screen is never involved.

Our kernel has neither `CONFIG_PM_WAKELOCKS` nor `CONFIG_PM_AUTOSLEEP`, and
systemd-sleep stops at the first wake, so an ordinary resume lit the screen
for every DSP request.

### Quiet resume

- **Issue:** Every DSP wake thawed the desktop and turned the display on.
- **Fix:** `houji-sleep` replaces `systemd-sleep` as the real-suspend step
  (`suspend-dispatch` already allows this through `ARMADA_SYSTEMD_SLEEP`,
  and `houji-suspend` sets it). It freezes `user.slice`, runs the same
  system-sleep hooks, and suspends. After a wake with no wake interrupt, a
  FastRPC wake-source increase and no other activity, it suspends again
  with the desktop still frozen. Any other wake ends the loop, runs the
  `post` hooks once, and thaws. The kernel's prepare check from patch
  `0020` refuses a suspend while the listener still owes an answer, so
  retrying on `EBUSY` is how it waits for the request to be served.
  The kernel wake hold dropped from 5 s to stock's 50 ms.
- **Why not a hook:** systemd runs sleep hooks under a 90 second budget, so a
  loop inside a hook would be killed after a couple of dozen wakes.
- **Safeguards:**
  - It falls back to plain `systemd-sleep` if the kernel lacks what it needs or
    `user.slice` cannot be frozen, or when `/etc/armada/houji-sleep-classic`
    exists.
  - Key interrupts (power, volume, any `gpio-keys` label) are compared just
    before each suspend, and again the moment it returns, so a press while the
    system is briefly awake, or while it is entering or leaving sleep, ends
    the loop instead of being lost in the frozen session's input queue.
  - Six dark wakes inside 20 seconds end the loop. If the listener is still
    busy after 15 s following a dark wake, the desktop resumes normally.
  - The hooks come from the same directories as `systemd-sleep` (including
    `/etc` overrides and `/dev/null` masks) under the same 90 second budget. A
    hook that cannot start or exits non-zero is logged and skipped.
  - Stop signals are ignored while the `post` hooks and the thaw run, a failing
    hook cannot skip the thaw, and the unit thaws `user.slice` even if the
    script is killed outright.
  - An unreadable wake-interrupt file counts as a real wake, never a dark one.
- **Review:** an independent read-through found a too-late key baseline, an
  unwatched multi-word `Volume Up` label, an unguarded hook launch and a test
  that left bytecode in the tree that gets staged into images. All are fixed
  and covered by `tests/houji-sleep-test.py`, and staging now skips
  `__pycache__`.
- **Known limit:** if the freezer scans the listener while it is runnable
  rather than sleeping, patch `0022` does not apply and the old restart path
  can still lose that one request. The DSP retries it, and the worst outcome
  is the old delay, not a failure.
- **Result:** On the phone (kernel `adsp3`), a 50-minute sleep ended by an RTC
  alarm absorbed three DSP wakes, after 396 s, 1677 s and 15 s. The desktop
  thawed once, at the end, and the kernel log shows the panel initialised
  only then. The kernel woke at boot-clock 499.96 and 2178.68 and the daemon
  had the request 1.17 s later each time, handled in under a millisecond;
  before patch `0022` it took 11 to 100 s. The kernel counted four
  suspends and no failures. Afterwards speaker playback reached the
  hardware, accelerometer samples and battery readings were normal, the DSP
  was running and no watchdog or oops was logged. A Power press ended a
  separate sleep with `wake irq 21`, the panel came back, and two further
  rapid Power sleep/wake cycles also worked.
- **Not covered on hardware:** a Power press after an absorbed dark wake, a key
  press inside the brief awake window (unit tests only:
  `tests/houji-sleep-test.py`), and the Plasma Mobile session. The tests
  above ran in Steam Game Mode, where Gamescope disables the panel before
  sleep; whether Plasma leaves the panel off across a dark wake is
  unverified. Standby power with the periodic wakes has not been measured.

### Dead ends in that investigation

- **Stopping the sensor consumers before sleep.** A hook stops the orientation
  service and sensor proxy before sleep and restarts only those that were running,
  keeping the DSP file server alive for other clients. It is kept, but it did not
  remove the watchdog.
- **Explicit SSC teardown.** The SSC accelerometer driver released its client
  without disabling an active measurement subscription. Its close path now
  disconnects the callback and sends the disable request first (source patch
  pinned in the build). A 35-minute baseline had still crashed, and a 40-minute
  RTC sleep with this userspace fix alone then passed. That pass was first read as
  the cause being found, but a later ordinary Power-button sleep reproduced the
  watchdog with the fix installed. The earlier pass was a single lucky result, not
  a resolution.
- **Waking on every listener reply.** An experimental kernel patch voted a wake for
  every sensor FastRPC response. It failed device validation at startup and was
  reverted. It recorded many wake events but kept no usable boot trace, so it did
  not establish why startup failed. The final patch only acts during suspend.

### Display handoff and wake latency

- **Issue:** After the watchdog was understood, waking showed a roughly two-second
  black interval, then the finished sleep animation, then the wake animation.
  Native DRM resume restored the saved scanout before Steam could draw its resumed
  state.
- **Fix:** A service ordered before `sleep.target` has Gamescope disable its internal
  output while userspace still runs, then restore it after sleep. This cannot be a
  pre-sleep hook, which runs with `user.slice` already frozen. An already-disabled
  display is preserved, Plasma and light sleep are untouched, and `ExecStopPost`
  restores a display left off by a failed start.
- **Result:** The owner confirmed the old sleep animation no longer appeared. The
  remaining delay was attributed by trace to about 0.39 s of PCIe resume and 0.87 s
  of Wi-Fi resume.
- **Test method:** Later sleep tests inject a short `KEY_POWER` press through the
  real PMIC evdev device, so the normal power-button path is exercised. RTC wake is
  only a backstop. Stopping the handler while keeping logind's key inhibitor
  produced no Steam sleep request, ruling out duplicate dispatch through Gamescope.

### Restart of sensors held the desktop frozen

- **Issue:** The sensor restart after sleep kept `user.slice` frozen for almost two
  seconds after the kernel had resumed, because the sensor proxy's startup includes
  an SSC warm-up.
- **Fix:** Queue that restart without blocking the sleep hook. The orientation
  service's systemd ordering still waits for the sensor proxy.
- **Result:** The frozen interval after kernel resume fell from 1.927 s to 0.102 s.
  DRM page flips resumed about 0.16 s after the display was restored, with no
  suspend error or reboot. The owner still saw a brief animation pause.

### Wi-Fi wake latency (trial, then reverted)

- **Cause:** NetworkManager disconnected Wi-Fi for sleep, so ath12k powered the
  radio down and reloaded firmware before userspace could resume.
- **Trial:** Select WoWLAN magic-packet mode for the onboard adapter, which retains
  firmware through suspend. Wake to first DRM frame dropped from 1.636 s to 0.884 s,
  and kernel resume from 1.250 s to 0.443 s.
- **Problem:** During a batch of full cycles SSH became unavailable. The owner
  confirmed Steam was responsive, and toggling Wi-Fi restored access. This was a
  network recovery failure, not a reproduced animation freeze.
- **Decision:** Disable WoWLAN as the adapter's default. Use an explicit zero value,
  because simply removing the setting can leave the driver's old wake policy in
  place. Three comparison cycles then restored the network each time. Explicit
  per-network preferences are still respected.

### Fresh-frame wake in Gamescope

- **Issue:** Faster resume exposed another stale-frame race.
- **Fix:** Gamescope waits for a completed fresh frame before enabling the panel.
  A timerfd in its event loop provides a bounded fallback for a static client, so
  it does not depend on more client or vblank events. A new sleep request cancels
  both a pending wake and its deadline. Both requests are applied on the compositor
  thread. Only the request crosses threads. The public build applies the patch and
  copies its helper header.
- **Result:** Pausing Steam's UI exercised the fallback, and the panel returned after
  0.506 s. A new sleep request cancelled an earlier pending wake. Two recorded
  physical Power-key wakes and three more injected-key/RTC cycles completed without
  a reboot or suspend failure. The owner confirmed a completed wake animation and
  working touch.
- **Correction:** An earlier version of this trial seemed to work in one cycle, then
  repeated physical-button cycles froze during the animation and the phone became
  unreachable. The single success was not enough evidence. A bounded test that froze
  the desktop document portal did not block filesystem sync, so a suspected
  FUSE/freezer interaction was not reproduced and the freezer policy was left as is.
- **Still open:** The intermittent animation freeze has not been explained. The
  later tests did not identify its original cause.

## Armada 20260926 migration

- **Issue:** The fork was based on the September 19 tree and Houji built Linux 7.2.3,
  with an OCI userspace pin from a September 26 prerelease. The kernel lacked the
  release's SM8650 PCIe sleep operating point, RPMh regulator sleep-state support
  and AudioReach system-suspend changes.
- **Change:**
  - Merge the exact 20260926 release (`c2fd0485b4db`) and pin its final OCI image
    by digest.
  - Move Houji to the release's Linux 7.2.6, and use the Linux manifest pin for the
    touch, NFC and GPS modules as well as root staging.
  - Keep the stock-ABL installer, board firmware and separately pinned touch-aware
    Gamescope. Preserve NFC Manager's settings across local updates.
  - After successful RTC and Power-button wake tests, make native `s2idle` the
    default and let NetworkManager take part in suspend. Light sleep stays selectable.
- **Compatibility:**
  - Linux 7.2.6 already has the GLINK detach and DPU/DSI power-vote fixes, so those
    backports were dropped.
  - Keep the shared battery property-set reply handler, merge the duplicate
    charge-current cases with variant-specific decoding, and initialise Houji's
    current-limit watchdog alongside the newer driver initialisation.
  - Refresh the audio patch without restoring the resume capability upstream removed
    when it added AudioReach graph teardown and recreation.
- **Result:** All 181 shared and Houji kernel patches apply without fuzz. Installer,
  sleep-diagnostic, power-button, performance-policy, charging-policy, panel-gamma
  and Wi-Fi parser checks pass. The kernel and modules build and boot to Steam.
  Light sleep still powers down the display domain and resumes, native `pm_test`
  freezer, devices and platform stages return, and RTC and Power-button wakes work
  with touch confirmed. The full root image builds from pinned public inputs and
  boots from internal userdata, keeping home and owner settings.
- **Not established:** AOSS/CX power-collapse counters did not advance, so upstream's
  battery-saving figures do not apply to Houji. The ath12k resume path still logs an
  hwmon parent-sleep warning.

## Wi-Fi

- **Issue:** Generic WCN7850 firmware failed board-data loading. Stock Kiwi-v1
  failed to start, and Kiwi-v2 reached firmware but asserted during initialisation.
- **Fix:** Use the matching Kiwi-v2 firmware, global N3 board data and regulatory
  files from the firmware baseline. Trace the firmware assertion and stock CNSS
  initialisation. Advertise the expected single-chip QMI MLO resource topology
  before WMI initialisation, even though mac80211 MLO is not enabled. Parse the
  nested DBS/SBS capability TLVs correctly.
- **Issue:** 5 GHz capabilities were cut short by a single-PDEV frequency-range
  interpretation.
- **Fix:** Correct that range handling.
- **Result:** A nearby 80 MHz Wi-Fi 5 access point negotiated two spatial streams.
  Local iperf measured about 509 Mbit/s down and 424 Mbit/s up. The tested 2.4 GHz
  access point advertised one HT stream at 20 MHz, which explains its 72.2 Mbit/s
  link rate. These are examples, not guarantees. NetworkManager owns connections,
  and no test credentials or saved profiles ship with the port.

## Sensors and rotation

- **Issue:** The SSC sensor service needed Xiaomi's registry layout, and initial
  orientation was rotated 180°.
- **Fix:** Integrate libssc, hexagonrpc and iio-sensor-proxy, adapt registry
  handling and apply the corrected accelerometer mounting matrix. Calibration is
  read from each handset's own read-only `persist` partition. Plasma follows
  SensorProxy. A Gamescope orientation service uses its rotation protocol, and a
  compositor patch ties the uinput touchscreen to the internal display.
- **Limits:** Physical touch alignment in every Steam orientation needs broader
  testing. Gyro input for aiming in games is not implemented.

## Battery and charging

### Battery readings

- **Issue:** Generic PMIC GLINK battery property IDs and current polarity did not
  match this firmware, and Steam's battery display lacked charging estimates.
- **Fix:** Implement the Houji property mapping, correct the current sign and expose
  UPower telemetry in the interface Steam expects. Suspend limits are kept when
  desktop power profiles change.
- **Note:** State of charge and time estimates are firmware estimates, not proof
  that charge is rising.

### Fixed 10 W in btop

- **Issue:** btop always showed 10 W.
- **Cause:** The firmware's translated `POWER_NOW` field stayed at 10,000,000 µW while
  voltage and current changed, and btop preferred it.
- **Fix:** Remove that unsupported property from the Houji battery descriptor. Voltage,
  current and charge remain, so consumers can calculate watts. Other Qualcomm
  descriptors are untouched.
- **Result:** After a normal reboot btop showed changing signed power (about −1.2 to
  −2.4 W in the test). This is net battery power, not wall input or total consumption
  while plugged in.

### Charging basics

- **Issue:** Firmware could leave input current at 100 mA, wireless charging could
  start then stop, and an early diagnostic module exposed a GLINK cleanup hang.
- **Fix:** Trace the stock HyperOS charger path. Send the normal verification
  completion message and use a hash-checked adapter for the stock authentication
  program. Apply conservative current limits, bounded leases and thermal checks
  from the board's real thermistors, and port the GLINK detach fix.
- **Result:** Wall charging was seen raising charge with the screen off. Computer USB
  can still supply less than a running system uses. Rated 90 W operation, full thermal
  behaviour and sustained wireless charging remain unverified.

### Fast charging after the 20260926 rebuild

- **Issue:** The tested 3 A ceiling existed only in the installed system. Rebuilding
  from public sources dropped it and exposed the kernel's 1 A staging default. The
  stock 90 W adapter still authenticated, but battery power fell to about 3.7 W.
- **Fix:** Ship the 3 A normal ceiling with the same fallback in the policy. Use the
  stock firmware's read-only fast-charge mode (XM55), advertised APDO power (XM61),
  real adapter authentication and PD-PPS state to pick a separate HyperCharge
  ceiling. The stock normal-mode ceiling of 15.6 A at the battery is an upper bound
  on a thermal vote, not a promised current. Keep the stock SIC and display-dependent
  thermal tables, firmware JEITA limits, gradual ramp and ten-second current lease.
  Advertised adapter wattage is never reported as measured power.
- **Result:** A staged 6 A test reported up to about 19 W at the battery. Stock thermal
  control held the FCC near 4.6 A as the phone warmed.

### Fast charging versus suspend

- **Issue:** Native suspend stops both the host thermal daemon and its lease timer.
  A high charge current could outlive the monitor.
- **Fix:**
  - A kernel PM notifier drops a high FCC to 500 mA before freezing, blocks racing
    increases, and aborts suspend if the drop fails.
  - Resume invalidates board telemetry, and the policy uses `CLOCK_BOOTTIME` to
    discard a ramp across sleep.
  - New high-current selection requires this kernel guard, and service shutdown gives
    up its high-current vote.
  - During an authenticated wired charging session, Power uses Armada's light sleep
    so the host thermal loop keeps running with the screen off. Unplugging, reaching
    full charge or losing the charging service ends light sleep and returns to the
    configured mode. Battery-powered sleep stays native.
- **Result:** Charging light sleep kept the display blank and the policy running, and an
  injected Power key woke it. Stopping the charging service during light sleep cut a
  4.6 A vote to 500 mA and moved into native suspend, followed by a good RTC wake.
  Cable-detach and full-charge selection are covered by policy tests, but those
  physical transitions need longer validation.

### HyperCharge parity investigation (partial)

Restoring the ceiling and seeing adapter authentication does not show stock-equivalent
HyperCharge. The port's own 38°C gate was the cause of an abrupt slowdown.

- **Method:** Re-extract `mi_thermald`, `qti_battery_charger.ko`, the thermal
  configuration and the ADSP firmware from OS3.0.303.0.WNCTWXM. The exact charger
  module differs from the device repository's, so offsets must come from the firmware
  actually used.
- **What the firmware showed (IDA):**
  - XM55 (`fastchg_mode_show` at `0x10e90`) reports firmware state and is not an
    enable switch. XM61 reports advertised APDO power. Stock `power_max_show` uses
    XM62. Neither measures battery power.
  - The normal and global-normal thermal configurations have identical charging
    tables, and the port already matches their virtual-skin weights, wired
    mitigation levels and SIC coefficients. `timer_expires_2` at `0xa7d64` shows SIC
    uses the firmware FCC readback and temperature history within its configured
    limits whenever USB is online.
  - BAT SET property 12 reaches `battmngr_plat_xiaomi_sic_vote_for_fcc` at
    `0x600936a0` and submits a normal vote to the `fcc_0` minimum aggregator, not the
    force-override operation. BAT GET property 12 reads the aggregator result. So the
    feedback is the resulting FCC limit, not an echo of the request or measured current.
  - Stock ADSP configuration sets fast-charge eligibility to 15–47°C and below 95%
    charge. `battmngr_plat_xiaomi_set_fastchg_mode` at `0x6008d010` checks these
    before enabling the mode. Separate charge-pump, battery-health, voltage, connector
    and thermal votes still decide the outcome.
  - The stock `micharge` HAL's `getFastChargeModeStatus` only reads the status node,
    and there is no named enable method. The charger module exposes charge-pump mode
    as XM property 36 with read and write handlers, but when stock code changes it is
    unknown. The `xm_is_force_exit_cp_mode` function reads charge level and adapter
    identity and branches at 25% and 94%, so firmware takes part in charge-pump
    decisions. The stock `cp_mode` node is root-writable while `micharge` runs as
    `system`, which suggests a firmware-managed transition.
  - Do not force charge-pump mode based on the status value alone.
- **Observed warm charging:** With the stock adapter authenticated and firmware
  `fastchg_mode=1`, a read-only trace at 63–65% and about 37.5°C measured 18.9–20.7 W
  at the battery with an FCC vote of 4.6–5.1 A. At 70% the battery reading rose from
  38.0 to 38.1°C and the host vote fell from about 4.6 A to 0.5 A, confirmed by the
  service journal. Firmware still reported fast-charge mode and mitigation stayed at
  zero. USB voltage then dropped from about 17.8 V to 9 V and then 4–5 V. This
  identifies the port's 38°C gate as the cause of the slowdown. It does not prove the
  full stock curve or charge-pump state, and `fastchg_mode=1` staying set through the
  voltage collapse means that bit alone cannot show the pump is working.
- **Observed high charge:** A later trace from 87% to a displayed 100% at about
  37.1–38.0°C kept the FCC readback at 4.279 A while battery current fell from about
  3.4 A to about 1.7 A, with USB near 9 V. Nothing changed abruptly at 95%. The
  phone kept reporting `Charging` for three minutes after displaying 100%, then
  `Full` with zero battery current while the FCC readback stayed 4.279 A. A later
  40-second trace still showed `Full` but with 0.48–0.53 A battery current and a
  0.50–0.55 A FCC readback, even though the host logged a 3 A request, so another
  voter set the lower limit. The zero-current snapshot does not prove a permanent
  stop, and the late low-current behaviour needs a longer look.
- **Consequence:** The sleep selector keeps the host thermal monitor running while
  status is `Charging`, even at a displayed 100%, and releases it once charging ends.
- **Pending (committed, never flashed):** A kernel patch exposes read-only
  charge-pump mode, smart-charge settings and raw gauge charge to identify which stock
  controller is limiting charge. It also allows the firmware's 15–47°C range under
  the guards described in the README. It compiles and passes source tests, but its
  diagnostic reads and the 47°C range have not run on the phone.
- **Status:** Partial. Authentication and higher current are observed. The full stock
  curve, charge-pump transitions, taper and thermal behaviour remain under
  investigation. Charging light sleep is a port workaround to keep host monitoring,
  and stock-equivalent suspend behaviour is unverified.

## Audio, Bluetooth and haptics

### Speakers and microphone

- **Issue:** The AudioReach TDM endpoint, clocks and board routes were missing.
  Early playback formats could crash the ADSP, microphone streams were silent and
  one speaker channel was absent.
- **Fix:** Add the TDM endpoint and clock support and use 48 kHz S32_LE stereo end
  to end, with the tested four-slot timing. Slot mask 5 selects the two I²S phases,
  and each CS35L41 amplifier is routed through its protected DSP path. Load the
  matching top and bottom coefficients and each phone's factory resistance
  calibration before enabling output. Use the correct capture DAI, ADC startup
  order, low-power clock selection and SoundWire lane 1 when remapping ADC1 to master
  port 3. ALSA UCM exposes speaker and microphone to PipeWire.
- **Result:** Both speaker channels and microphone capture passed acoustic tests.

### Bluetooth and vibration

- **Issue:** UART and power wiring and PMIC haptic support were missing.
- **Fix:** Add the stock board wiring and periodic force-feedback support.
- **Result:** Controller pairing and input, and short and long vibration pulses, were
  confirmed. Bluetooth audio, calls and broad controller interoperability were not
  tested.

## USB, OTG and gamepads

### Normal USB and SuperSpeed

- **Issue:** Development images exposed USB serial consoles. Early DWC3 startup
  timed out, and the temporary USB 2 workaround blocked SuperSpeed.
- **Fix:**
  - Remove diagnostic gadgets and serial logins. Charging is the default, and
    Armada's MTP switch enables one file-transfer interface on demand.
  - Wire the Type-C graph and QMP PHY with its stock supply and cable-orientation
    GPIO, and let ADSP UCSI own roles and VBUS.
  - Remove the USB 2-only clock override.
  - Enable uMTP's SuperSpeed descriptors and query FunctionFS for the negotiated
    packet size, so USB 2 and USB 3 termination both work.
- **Result:** PC transfers passed at 5 Gbit/s in both connector orientations, and the
  USB 2 fallback passed too.

### Gamepad Home button

- **Issue:** A SHANWAN Android gamepad's Home button was on a separate Consumer
  Control interface, and a first generic axis mapping triggered Steam screenshots.
- **Fix:** Combine its interfaces in InputPlumber, map Home to Guide, Z/RZ to the right
  stick and GAS/BRAKE to triggers.
- **Result:** Home opened the Steam menu normally. Powered host operation was verified
  with that gamepad. A Pixel 7a webcam negotiated 5 Gbit/s and streamed raw and 1080p
  MJPEG, though its UVC stream occasionally flagged bad frames for an unknown reason.
  USB4 and DisplayPort are not claimed.

## GPS

- **Issue:** The stock modem rejected generic QMI LOC registration and first reported
  sessions without satellite fixes.
- **Fix:** Trace the stock client metadata, hold one QRTR client across the session,
  start the firmware file service before MPSS, and map its MCFG requests to stock
  firmware. Support UTC and orbit assistance and the expanded satellite-report format.
  Position and uncertainty can travel over private Unix sockets without logging
  coordinates. Use private, read-only-shadow NV snapshots instead of sharing or
  changing another handset's calibration.
- **Result:** Satellite fixes were obtained with better sky visibility. Receiver-reported
  uncertainty is not measured ground-truth accuracy.
- **Open:** Restarting MPSS in the same boot rebooted the phone in one test. GPS stays an
  opt-in development feature and is not an enabled GeoClue provider.

## NFC

### Reader bring-up

- **Issue:** The SN220 accepted NCI commands but found no cards, and a vendor
  notification was not recognised by Linux.
- **Cause:** Tracing Xiaomi's HAL with IDA showed the notification reports an
  unexpected clock. Xiaomi's kernel enables GPIO35's always-on wake route for CLKREQ,
  as well as setting the pin as an input.
- **Fix:** Request that optional interrupt through the normal GPIO/IRQ driver, which
  enables the route in mainline. Add the I2C0 wiring, VEN and IRQ pins and a vote on
  the shared L3C I/O supply.
- **Result:** Card discovery works with the unmodified NXP NCI core. The experimental
  vendor initialisation commands are not part of the port.

### Connecting to a card failed

- **Issue:** Discovery worked but connecting failed. Linux reported an incomplete target
  list and misaligned the activation fields.
- **Cause:** The controller adds an extra byte to its NFC-A RF parameter block. Linux
  advanced only by the fields it decoded and read that byte as the discovery
  notification type.
- **Fix:** After parsing the known fields in discovery and activation notifications,
  advance to the end of the validated parameter block.
- **Result:** The tested card activates through ISO-DEP and answers a standard NDEF
  application select with `6A82` (application not found). This proves command and
  response transport. It does not show NDEF content reading or protected MIFARE Classic
  access. The standard neard service is packaged with the radio and continuous polling
  off by default. No tag identifiers, contents or packet captures are in the build.
  After installing the updated images and rebooting, the controller probes by itself
  without a temporary overlay. Reading NDEF from physical cards, other tag families
  and writes still need separate validation.

### Emulating a tag

- **Proof of concept:** Another phone read a standard Type 4 text tag from a temporary
  host responder on Houji. The installed neard has no emulation method, and the
  kernel's listen path handles NFC-DEP, not ISO-DEP card emulation. The diagnostic
  briefly took over the NCI transport to serve a fixed read-only message, leaving the
  data-ready IRQ trigger unchanged and using the normal driver's configuration reset to
  restore reader mode. A second test set a synthetic four-byte NFCID1, and the other
  phone confirmed both it and the text. The original controller settings were kept in
  memory and restored. Other UID lengths and emulating a physical card's application
  and authentication behaviour are not established.

### NFC Manager

- **Issue:** Plasma had no controls for this NFC backend. The temporary responder needed
  manual driver switching, and removing the reader driver dropped its vote on a supply
  shared with Wi-Fi.
- **Fix:**
  - Add NFC Manager to KDE's application menu, with reader on/off, rescanning and
    read-only text-tag emulation.
  - A Polkit-protected D-Bus service serialises controller access and restores the
    reader when emulation stops.
  - A supply-hold module, loaded only when needed, keeps NFC powered while the normal
    driver is detached, and is unloaded afterward. Driver and GPIO chip names are
    found at runtime, and no received card data is saved.
  - The service checks current power state first (neard errors if told to disable an
    already disabled adapter). Emulation keeps the original protocol settings in
    memory, clears temporary routing with the normal NCI reset, and verifies the
    restore. A cleanup hook reattaches the normal driver if the backend exits
    unexpectedly.
- **Result:** The supply module loads and unloads on the installed kernel. Reader on/off,
  rescanning, both serial modes and reader restoration work through the D-Bus service,
  and the unprivileged local desktop can control it. Protocol checks cover NDEF and CC
  reads, UTF-8 text, input limits and rejected writes. The owner confirmed reading the
  text tag with another phone.
- **Background operation:** The first version stopped emulation when the app closed. At
  the owner's request, the service now runs independently of the UI, saves tag settings
  and the desired mode privately under `/var/lib/armada-nfc`, and restores that mode at
  boot. Closing the window leaves emulation on, and Stop emulation saves an off choice.
  Tests confirmed that emulation continues after a client disconnect, settings reload
  after a service restart, and an explicit Stop stays off.

## Session switching

- **Issue:** Choosing Game Mode in Plasma returned to Plasma.
- **Cause:** Houji wrote its session choice to `zz-holo-autologin.conf`, but Armada's
  session settings write `zz-steamos-autologin.conf`. SDDM reads the latter afterward,
  so an existing Plasma preference overrode the request.
- **Fix:** Use Armada's canonical file in both the image builder and the switch worker,
  and remove obsolete Houji and temporary overrides. Keep the worker outside the session
  it stops and clear the previous graphical session's environment.
- **Result:** An isolated regression test starts with conflicting settings and checks
  both directions and a second Game Mode switch. A live round trip through Armada's
  desktop switch and the normal Game Mode launcher returned to Steam.

## Making the build reproducible

- **Issue:** The first installers repacked earlier diagnostic images and assumed one
  workspace layout, one filesystem UUID and one storage size.
- **Fix:** Replace those with explicit source and firmware inputs, a matching kernel and
  module build, and image-generation parameters. Runtime support is staged into the
  image instead of relying on changes already installed on the owner's phone.
  Historical diagnostics and private validation records are not distributed.

Bugs found while verifying a clean build:

- **AVB flags.** The cleanup reflash hit a host fastboot failure while rewriting AVB
  flags. Packaging now sets the two disable flags in the pinned stock vbmeta image, and
  the installer checks the AVB header, bounds and flags before any write, then sends
  the result with an ordinary flash. The flag layout follows
  [Android's fastboot implementation](https://android.googlesource.com/platform/system/core/+/683f225ad4c259d0fe63d583e051840b12039d00/fastboot/fastboot.cpp).
- **Missing `/bin/sh`.** The first standalone ramdisk installed BusyBox applets from its
  init script, but the kernel needed the shell before that script could run. The phone
  rebooted repeatedly into fastboot. Packaging now includes the shell symlink.
  Installer preflight checks the CPIO archive, executable init, shell link and static
  arm64 BusyBox before touching the phone, and a regression test covers the case.
- **Missing `realpath`.** The next boot reached init but BusyBox lacked `realpath`,
  needed to find the preserved home. The applet is enabled, and packaging now runs the
  built BusyBox (under QEMU on non-arm64 hosts) to check required applets, shell syntax
  and builtins, large-file sizes and path resolution before generating images.

Verification:

- The corrected clean build booted from internal storage through the cleaned
  installer's preserving-update path, with the rebuilt kernel and modules, the new EROFS
  root and the existing home on separate overlays. The owner confirmed normal display and
  touch, then launched games. Plasma and Steam both started, Wi-Fi negotiated two
  streams at 80 MHz, PipeWire exposed speaker and microphone, touch, sensors, Bluetooth
  and charging services were active, the boot slot was marked successful, and no USB
  gadget was bound by default. GPS, Bluetooth pairing, acoustic audio and USB host
  peripherals were not retested in that pass.
- The fresh sparse userdata filesystem passed local checks. The physical reflash kept the
  owner's data, and installer regression tests cover the destructive write sequence
  separately. An initial Flatpak key-import error cleared after clock sync and a setup
  retry. The base image's controller-type service still reports failure with no
  controller attached.
- A clean-build audit copied only the files meant for publication into a separate
  snapshot and hid the original workspace. With empty build output and compiler caching
  off, the full build downloaded its sources, compiled the kernel and userspace, and
  produced all six flash images plus the EROFS root. Only the pinned public OCI image
  download cache was reused, and its manifest was fetched independently without
  credentials and its digest verified. Installer regression tests and final image checks
  also passed. No extracted firmware, source checkout or earlier build artefact from the
  porting workspace was needed.

## Smaller updates and CI builds

### One 7 GB image for every change

- **Issue:** Every change meant a new 7.1 GB `rootfs.erofs`, and the bundle also
  carried `userdata.img`, which is the same image again inside an ext4
  filesystem (15 GB in all). That is too much to build, store and download per
  commit.
- **What is actually coupled** (read from the initramfs, `assemble.py` and
  `stage-update.py`):
  - The kernel and device tree live in `boot_b` and `vendor_boot_b`, apart from
    the root image.
  - The initramfs only compares a build id baked into `init_boot` with a file on
    userdata. It does not look at the kernel, so a new kernel can be flashed
    without touching `init_boot`.
  - The modules for the base kernel are inside the read-only root image.
  - The port itself adds only about 290 MB (uncompressed) to Armada's pinned
    image, so most of every root image is identical from build to build.
- **Kernel-only updates:** A new bundle type carries `boot.img`, `vendor_boot.img`
  and the module tree (about 90 MB). `package-kernel-update.py` builds it with the
  same packer as a full build. `install-kernel-update.py` runs on the phone, checks
  every entry of the archive, installs the modules under their own release name and
  writes a receipt. `flash-internal.py --kernel-only` needs that receipt and writes
  only the two partitions. A build-time check refuses a kernel whose boot-critical
  drivers (ext4, erofs, overlayfs, the UFS storage stack) are not built in, since
  the installed initramfs loads no modules. Each build must carry its own kernel
  release, because the root image's module directory sits in the read-only layer.
- **Userdata built where it is flashed:** `make-userdata.py` builds `userdata.img`
  from `rootfs.erofs` and `images.json` on the user's machine, and `assemble.py`
  now uses the same code. A published bundle therefore holds only the root image.
  The flasher accepts a locally built `userdata.img` only with a sidecar proving it
  was made from this bundle's root image, so a stale or truncated file cannot be
  flashed.
- **Considered and left for later:** a layered root, with Armada's base published
  once per pin and the port as a roughly 150 MB second overlay layer. It would shrink
  full updates too, but it changes the initramfs and update tool and needs a
  hardware reflash to prove, so it was not worth the boot-path risk yet.

### The first release run

The first run of the release workflow passed its checks and then failed 42 minutes
in, after the 39-minute kernel build, on the first userspace component. The cause
was the machine, not the port: it had been developed on Arch (Meson 1.12, GCC 16,
Wayland 1.26, erofs-utils 1.9) and the runner is Ubuntu 24.04. Finding the rest of
the mismatches by re-running on GitHub would have cost most of an hour each, so
the userspace build was rehearsed in an Ubuntu 24.04 container with Ubuntu's own
cross compiler. That reproduced the failure exactly (same Meson 1.3.2, same GCC
13.3.0, same message) and then found, one at a time:

- `libssc` requires Meson 1.4 and Ubuntu has 1.3.2.
- `libssc` also needs `protoc` (`protobuf-compiler`) and Python headers
  (`python3-dev`), which Arch ships by default.
- Ubuntu's GCC enables stack protection by default and its guard symbol,
  `__stack_chk_guard`, is defined only in the dynamic loader. The sysroot's
  `libc.so` is a bare symlink to `libc.so.6` rather than the usual linker script
  that pulls the loader into a link, so every protected object failed to link. The
  compiler used for the flashed image has it off, so the cross-compiler names now
  point at wrappers that add `-fno-stack-protector`. One mechanism covers every
  call site; the port calls the compiler from Meson, from Makefiles and directly.
- `wlroots`, built into Gamescope, wants a build-host `wayland-scanner` of 1.24 and
  Ubuntu has 1.22, so the scanner alone is built from the 1.24.0 release the image
  uses. The tarball's checksum matches the one Arch records for it.
- `mkfs.erofs` 1.7.1 rejects `--workers`. The rest of the call works (`lz4hc` level 9,
  the fixed timestamp, `fsck.erofs`), at about 53 MB/s on one thread, so the option
  is now only passed when the tool lists it.

With those in place the sensors stack, Gamescope, the GPS tools and BusyBox build
under Ubuntu's toolchain, and `assemble.py` produced a bundle whose `boot.img` is the
same size as the flashed one. The root image step (it needs the Armada image mounted
under rootless Podman) and the release upload could not be rehearsed, and both worked
on the next run: the full image built in 58 minutes (the build step took 55, with a
cold 39-minute kernel) and the pre-release was published. The workflow also now saves
its caches even when the build fails, so a late failure no longer throws the kernel
build away.

The published files were downloaded and checked the way a user would use them. The
parts, the joined root image (exactly the manifest's size) and `SHA256SUMS` all
verified; `fsck.erofs` passed and the image holds the port's files, including the
CI-built Gamescope; `make-userdata.py` built `userdata.img` from the real 7.5 GB image
in 110 seconds with a clean `e2fsck`; and the flasher's offline checks passed for both
a fresh install and an update, and refused the userdata image after one flipped byte.
Against the build that was flashed and booted:

- `vendor_boot.img` is byte-for-byte identical, so Ubuntu's `dtc` and `fdtoverlay`
  produce the same device tree. `dtbo.img` and `vbmeta.img` are identical too.
- The kernel config differs in 22 lines, all of them values detected from the
  toolchain (compiler, binutils, `pahole`, OpenSSL), none selected. The visible effect
  is that `CONFIG_RELR` is off, because Ubuntu's linker cannot pack relocations, so
  `boot.img` is 82.2 MB instead of 67.9 MB, 82% of `boot_b`. Every boot-critical
  option is still built in.
- Not done: flashing the CI-built image on the phone. Its kernel was built by GCC 13
  rather than GCC 16, so it is a different binary from anything tested on hardware.

### How it was checked

- **Packaging against a known image:** a bundle packaged from the already-flashed
  `adsp3` build reproduced its `boot.img` byte for byte, and its `vendor_boot.img`
  matched the full bundle's.
- **On the phone:** the same bundle was installed, flashed and booted end to end:
  modules installed, receipt written, host preflight passed, only `boot_b` and
  `vendor_boot_b` written, and the phone returned on the same kernel with sensors,
  touch, audio and the DSP running. That first run reinstalled the kernel that was
  already running; a different kernel was flashed afterwards (next item).
- **A different kernel, on hardware:** a fresh build under a new release name
  (`-kdryrun`, same config and patch series) went through the whole path with the
  fixed installer and booted. The phone matched its state before the flash, with
  fewer error-level kernel messages, and a 120-second sleep in Game Mode completed
  natively and woke on the RTC alarm. The first sleep attempt did nothing, and the
  cause was the session: the phone had been booting into the Plasma desktop, where
  PowerDevil holds a blocking inhibitor on the power key, so logind logged the press
  and KDE did not suspend. The test was rerun after switching to Game Mode with the
  port's own switch unit, and the original autologin file was restored byte for byte.
- **A refusal that was right:** the first hardware attempt stopped with "already
  holds different modules". I suspected the phone's own `depmod` run and checked
  before changing anything: the generated index files were identical, and the only
  differences were the three out-of-tree modules, which an earlier hand-made package
  had stripped and the standard build copies unstripped. The installer compares whole
  trees on purpose, so it was left as it is and the rehearsal bundle was rebuilt from
  matching inputs.
- **Tests that bite:** each safety rule was broken in turn to confirm a test fails.
  That found two real gaps, a same-size corruption that only the checksum could catch
  and a receipt check that was only covered on the older code path, and both now have
  tests.
- **A blocker the hardware test could not see:** an independent review found that
  the first install of any *new* kernel release always failed. The installer
  verified the staged tree with `modinfo -k <release>`, which only looks under
  `/lib/modules`, where a release that is not installed yet does not exist. The
  rehearsal above had reinstalled the *running* kernel, which takes the "already
  installed" path where that lookup works, and the unit tests had mocked `modinfo`
  away. Running the old installer on the phone with a fresh release reproduced it
  exactly (`modinfo: ERROR: Module houji-tcm-probe not found`) and also showed the
  failure path cleaning up after itself. The staged tree is now checked through
  `modinfo -b`, and the same install on the phone with the fixed installer put in
  1598 modules, left nothing behind and was a no-op the second time.
- **Other review findings fixed:** `--replace` could delete the running kernel's
  modules and was not atomic, so it now refuses the running release and any release
  the root image owns, and swaps by renaming the old tree aside; the boot-critical
  list missed the UFS PHY, the ramdisk decompressors, the EROFS decompressor and the
  console; the release check was a prefix match; there was no free-space check; the
  receipt was written through a link someone could have planted; and the packager
  could wipe any directory given as `--output`. A half-applied flash now says so.
- **Mocks that hid bugs:** the install tests now run the real `modinfo` and `depmod`
  on genuine ELF module objects compiled in the test, and a test feeds the
  packager's archive to the phone's reader.
- **A stale copy:** a bundle carries a copy of the installer from the moment it was
  packaged. The first phone run above used the copy from a build made before the
  fix, so a bundle must be rebuilt, not reused, after the installer changes.
- **A command-line bug only a real build could find:** `--localversion -kNAME`
  failed in `argparse`, which reads the value as another option, in both the
  documentation and the first workflow draft. The option now takes `kNAME`.

## Installing over stock Android

### Blank slot-B firmware and a stalling bootloader

- **Issue:** The first install onto a phone that had just been reflashed with
  Xiaomi's fastboot package stopped in the initramfs with `Armada startup failed:
  ADSP firmware`. Before that, the installer's `boot_b` write had timed out right
  after the 7.5 GB `userdata` write, and the bootloader had stopped answering (even
  `fastboot reboot fastboot` was ignored) until the phone was restarted by hand.
- **What the boot showed:** Everything before the firmware mount worked. `userdata`
  mounted, the build id matched, the 7.5 GB root image mounted and the overlay came
  up, so the stalled write had not damaged anything. `modem_b` was found (the same
  partition that mounted in earlier boots) but would not mount as FAT.
- **Diagnosis, one step at a time:**
  - A diagnostic `init_boot` printed what the partition looked like. `blkid` found
    nothing on `modem_b` but identified `modem_a` as the FAT image carrying the
    package's volume id. The kernel's FAT driver does not compare a filesystem's
    declared size with the device, so the image declaring 211.8 MB while its file is
    131.6 MB was not the cause.
  - A second diagnostic hashed both partitions. `modem_a` matched the package file
    exactly, and `modem_b`'s whole 131,624,960-byte image region hashed the same as
    that many zero bytes. It was blank, not mis-written.
  - A USB serial console, started from `fail()` in a debug-only initramfs, made it
    possible to run commands on the phone instead of photographing its screen. The
    shipped device tree sets the USB controller to `otg` with a default role of
    `peripheral`, and the kernel has the configfs ACM gadget built in, so a gadget
    started from the initramfs enumerates on the PC as an ordinary serial port. It
    was tied to one debug image and is not shipped.
  - Through that console every slot-B firmware partition was compared with its
    slot-A twin. Nine differed: `featenabler`, `modem`, `modemfirmware`,
    `bluetooth`, `dsp`, `qupfw`, `xbl_ramdump`, `imagefv` and `recovery`. Eight were
    blank at the start, and `modemfirmware` differed without being blank there. The
    other 19 pairs compared, the boot-critical firmware (`abl`, `xbl`, `tz`, `hyp`,
    `aop`, `uefi` and similar), were byte-identical. All nine slot-A partitions
    matched the package files by length and SHA-256, and `persist` was intact.
  - The package's script had flashed all 33 A/B partitions, and its log reported
    both slots written for every one, including these nine.
- **Fix for that phone:** the nine slot-B partitions were copied from slot A, each
  source checked against the package hash before the copy and each result after it.
  `modem_b`, `dsp_b` and `persist` then mounted as the init expects and Armada
  booted.
- **What caused it:** not established. A second run flashed the package again
  without booting Android and installed Armada straight afterwards. Armada booted
  and found `modem_b` intact, so the package does write slot B. Booting Android in
  between is the only difference between the two runs, which makes it the main
  suspect. It was not tested directly, because that would erase the install. A
  forced restart followed a bootloader stall in both runs, so it does not explain
  the difference.
- **Two more behaviours found on the way:**
  - When the bootloader reports `crc: 1`, the package's script loads Xiaomi's
    `crclist`. For the rest of that fastboot session the bootloader refuses any
    image not on the list (`Error flashing partition : 0000001B`, the UEFI CRC
    error). A bootloader restart clears it, and `fastboot reboot-bootloader` does
    that without booting Android. It had not shown before only because the script's
    own reboot cleared it.
  - The bootloader stalled after the 7.5 GB `userdata` write in both runs: first as
    a timed-out `boot_b` write, then as a `reboot` that was acknowledged and
    ignored. The data was intact both times, and holding Power alone restarted the
    phone.
- **Changes:**
  - The installer writes the boot images and activates slot B, then writes
    `userdata` last. Nothing that matters follows the stalling write, and a restart
    after a stall boots Armada instead of Android. It explains the CRC refusal, says
    to hold Power if the phone is still in fastboot after the reboot, and says what
    state the phone is in if the `userdata` write fails.
  - The init's failure messages name the unreadable partition and point to the
    README.
  - The README gained "Starting from stock firmware".
- **Lessons:** a flash log that says both slots were written does not prove both
  slots hold the data. A mount that fails without a kernel message needs the
  partition's own bytes looked at. A console on the failing system is worth more
  than photographs of its screen.

## Open problems

- Intermittent animation freeze around Steam wake: never reproduced or explained.
- Sleep has been exercised for hours, not days. The standby power cost of the
  sensor DSP's periodic wakeups is unmeasured, and DSP crash recovery is
  untested.
- Wireless charging starts, then stops.
- Stock HyperCharge behaviour, the 15–47°C fast-charge path and charge-pump transitions
  are unverified.
- GPS: restarting the modem can reboot the phone.
- NFC: NDEF reads from physical tags, other tag families and writes are unverified.
- Gyro aiming, cellular, cameras and fingerprint are not implemented.
- Other panel revisions, storage sizes and regional firmware are untested.
