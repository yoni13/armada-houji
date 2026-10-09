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
9. [Thermal limits](#thermal-limits)
10. [Audio, Bluetooth and haptics](#audio-bluetooth-and-haptics)
11. [USB, OTG and gamepads](#usb-otg-and-gamepads)
12. [GPS](#gps)
13. [NFC](#nfc)
14. [Session switching](#session-switching)
15. [Houji Settings](#houji-settings)
16. [Making the build reproducible](#making-the-build-reproducible)
17. [Smaller updates and CI builds](#smaller-updates-and-ci-builds)
18. [Installing over stock Android](#installing-over-stock-android)
19. [Open problems](#open-problems)

## Boot and storage

### Cellular bring-up (October 2026, ongoing)

- **eSIM:** stock switches slot 2 through the modem's `esim_enable` EFS item.
  The eUICC also needs the SN220 NFC VEN supply high. With that supply present,
  lpac read the chip and downloaded/enabled a profile. A 255-byte APDU maximum
  was required; splitting the first secure-channel segment at 120 bytes failed.
  A second carrier required retaining the HTTP connection and in-memory cookies
  between provisioning requests; doing so resolved server verification failures
  and allowed that data-enabled profile to download and activate.
- **Registration:** selecting the USIM as the primary GW provisioning session
  allowed ModemManager to register on LTE/5G and create a multiplexed bearer.
  The first profile was for provisioning tests. With the second profile and the
  SM8650 IPA correction, a small HTTPS request succeeded over LTE, routed through
  NetworkManager. Testing used only a few kilobytes of the prepaid allowance.
- **License fatal:** the modem deliberately crashed on Smart Transmit checks
  without the stock QTEELS relay. A Linux relay to QTEE UID 119, FS/GPFS listeners
  and read-only RPMB access returned successful secure license responses. The
  license was already factory-installed; no cloud license download was needed.
- **Remaining failure:** a later modem crash reported an IPA/GSI assertion.
  Stock SM8650 tables differ from Linux's SM8550 fallback in receive endpoint
  IDs, LAN RX channel and IMEM fallback address. Patch `0030` has now received
  cellular data and remained up past the earlier failure window in a live-module
  test. Automatic recovery is disabled by the new
  startup helper because hot modem restarts can reset the handset.
- **Integration:** pinned tool builds, private-storage services and a KDE SIM
  Settings module were built and flashed. The user verified the new profile
  manager and existing Cellular Network page. Slot-1 physical SIM registered on
  LTE/5G and completed HTTPS with roaming disabled. Switching back to eSIM in
  Settings and listing profiles worked. Physical slot 2 was explicitly skipped.
  Dialer and Spacebar were installed; actual calls and SMS remain untested.
- **Suspend follow-up:** ModemManager 1.24.2 needed fixes for a netlink transaction
  use-after-free, an incorrect bearer-count assertion, and missing QRTR rescan
  after resume. With those fixes, native s2idle resumed automatically after an
  armed RTC alarm and both SIM types passed HTTPS after wake. The native sleep
  loop now recognizes an armed RTC deadline even if the wake IRQ is obscured.
  It never arms an alarm itself; the timed test hook is not part of any image.
- **Quick-settings follow-up:** after resume, ModemManager and NetworkManager
  were connected while Plasma reported no SIM. ModemManagerQt emitted removal
  before updating its cache, so synchronous list rebuilds retained the removed
  modem. Its removal/SIM lifecycle ordering is now patched. Plasma NM also waits
  for profile saves before activation/disconnection and coalesces rapid data
  toggles. Repeated/rapid on-off tests passed; after an RTC suspend the live UI
  model selected only the replacement modem with its SIM present, and the user
  confirmed the quick-settings switch worked. The lifecycle regression test
  fails against the original KDE source and passes against the patched source.
- **Call inspection:** the user's outgoing calls reached QMI VOICE, entered
  dialing, then terminated before connection. Read-only queries on IMSA/IMS
  binding 0 (the primary subscription) reported IMS not registered and IMS
  registration disabled; binding 1 accepted the bind but returned
  `InvalidOperation` for status. CallAudioD independently reported no suitable
  audio card/voice ports. The current UCM profile is HiFi-only. Calling needs
  further IMS and voice-audio integration; no test call was placed by the agent.
- **Download routing:** with IPv4-only Wi-Fi and cellular IPv6 active together,
  Steam selected IPv6 CDN endpoints over the modem and downloaded at roughly
  0.08–0.12 Mbps despite a healthy 5 GHz Wi-Fi link. After the physical SIM was
  removed, Wi-Fi-only downloads reached about 31 Mbps. The new dispatcher makes
  this preference persistent by temporarily suppressing cellular defaults in
  both families while routed Wi-Fi is active, restoring saved preferences on
  Wi-Fi loss. Unit and typed D-Bus replay tests cover the policy; live dual-link
  fallback still needs checking with a SIM present.

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

### Compute DSP (NPU) enabled (2026-10-09)

- **Request:** enable the NPU, which is the HTP v75 inside the compute DSP
  (CDSP). Only the ADSP and modem ran; there was no `/dev/fastrpc-cdsp`.
- **Inputs:** the handset's stock `modem_b` already holds `cdsp.mdt` and
  `cdsp_dtb.mdt`, the mainline driver's SM8650 defaults, so no `firmware-name`
  is needed, as for the ADSP. `dsp_b` holds the userspace side (`cdsp/`, with
  `fastrpc_shell_3` and skeleton libraries). The stock reserved-memory layout
  matches `sm8650.dtsi` for all three CDSP regions: `cdsp_mem` at 0x9ca00000
  (20 MiB), `q6_cdsp_dtb_mem` at 0x9de00000 and `global_sync_mem` at 0x82600000.
- **Change:** `&remoteproc_cdsp { status = "okay"; };`. The compiled DTB
  differed from the running one only in that node's status. It was installed by
  writing `vendor_boot_b` from Linux with a verified backup and read-back. The
  `kcap3` kernel and modules were byte-identical. A DTB-only `make` must keep
  `LOCALVERSION`, or it rewrites the tree's release name and the bundle packer
  refuses the mismatched Image.
- **Result:** the CDSP booted 176 ms after loading (`remote processor cdsp is
  now up`), and FastRPC created `/dev/fastrpc-cdsp` and `-secure` with five
  compute context banks. `hexagonrpcd` attached to the CDSP root domain and also
  created a signed user domain from the stock `fastrpc_shell_3`. Both ran until
  stopped, which needs successful remote calls (`remotectl open`, listener
  register and init, then the waiting invoke). The same worked after stopping
  and restarting the CDSP and after two sleeps. A udev rule gives the logged-in
  user `fastrpc-cdsp`; `-secure` and the ADSP node stay root-only.
- **Sleep:** the CDSP power-collapses on its own (25 entries during a 3-minute
  sleep) and added no wakeups. `aosd`, `cxsd` and DDR low-power counts stayed 0
  with the CDSP running and with it stopped. So the SoC never reaches its deepest
  sleep on this port anyway, and the CDSP is not the cause. That is a separate
  issue.
- **Not included:** an NPU runtime or models. Qualcomm's QAIRT/QNN runtime and
  any model weights belong to their owners and must be supplied separately.
  Unsigned process domains were not tested.

### The clock started in 1970 and the Decky store crashed (2026-10-09)

- **Report:** the Decky store kept crashing.
- **Cause:** opening it imports a frontend chunk from Decky Loader's local web
  server. Steam's JS log showed `Failed to fetch dynamically imported module:
  http://localhost:1337/frontend/chunk-B1zfxE9I.js`, and Decky's error boundary
  then showed its crash screen. The server answered 404. Decky Loader is a
  PyInstaller one-file program that unpacks its frontend into `/tmp/_MEI*` once
  at start. Its archive held 18 chunks; only 11 files were left on disk. On every
  boot the `rtc-pm8xxx` driver registered at about 3.7 s, and the kernel copied
  the PMIC RTC into the system clock: `1970-01-05`. Linux cannot set that RTC.
  Decky started at 8 s, still in 1970, so everything it unpacked was dated 1970.
  chrony corrected the clock at 12 s, once Wi-Fi was up. Fifteen minutes after
  boot, `systemd-tmpfiles-clean` applied `q /tmp ... 10d` and removed every one
  of those files that had not been read since the clock jump. That included the
  store's chunks, which load only when the store opens.
- **Fix:**
  - Armada's `armada-decky.conf` adds `x /tmp/_MEI*`, so `/tmp` ageing never
    touches a PyInstaller program's unpacked files. The houji image stages it
    until the pinned image ships it. This also covers Armada devices with a
    working RTC, after 10 days of uptime.
  - The phone's `chronyd` now starts with `-s` and after `dev-rtc0.device`; a
    udev rule tags `rtc0` for systemd so that ordering is possible. chrony reads
    the RTC, logs `RTC time before last driftfile modification (ignored)` and
    steps the clock to the drift file's time. chrony updates that file about
    hourly, so early services start within about an hour of the real time
    instead of in 1970.
- **Status:** restarting Decky Loader re-unpacked all 35 files, and both missing
  chunks loaded. Both rules were installed live; a `chronyd` restart took the new
  path, ignored the RTC and left the clock correct.
- **Verified after a reboot:** the kernel still set 1970 at 3.7 s. `chronyd`
  started at 4.8 s, after `rtc0`, with no added delay, and logged `System time
  restored from driftfile`. chrony also writes that file when it stops, so the
  restored time was only the reboot's length behind. Decky started at 9.3 s
  with the correct date, and NTP then stepped the clock by 16.75 s instead of
  56 years. All 35 Decky files were dated today. A manual
  `systemd-tmpfiles --clean` removed none of them, and the store chunk loaded.
  No units failed.

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

### The screen refreshed at 120 Hz while Steam was idle

- **Issue:** On a static Steam screen the display still drew about 0.7 A. Steam
  itself stops drawing when idle, but Gamescope committed a frame on nearly
  every vblank (about 116 per second), and the panel refreshed itself at
  120 Hz regardless.
- **Cause:**
  - MangoHud 0.8.4's `mangoapp` never reset its new-frame flag, so after the
    first frame it redrew the overlay continuously while it was shown, and
    each redraw made Gamescope composite and send a frame. With `mangoapp` paused, an idle Steam sent no frames at all.
  - The panel driver used the stock fixed 120 Hz mode. The N3's driver chip
    also has stock "idle" modes that step down to 10 Hz or 1 Hz on their own
    when no new frame arrives.
- **Fix:**
  - MangoHud patch `0008` is upstream's fix (2c1dc5283c04, "throttle overlay
    to one render per game frame"). `mangoapp` now draws once per app frame,
    so the overlay sends nothing while Steam is idle. The port's `mangoapp`
    build picks it up.
  - Kernel patch `0027` powers the panel on in the stock `idle_120_to_1hz`
    mode: 120 Hz while frames arrive, down to 1 Hz when idle. TE stays fixed
    at 120 Hz, so the DSI host, DRM mode and Gamescope are unchanged.
- **Result:** Tested on the phone, Steam home screen, overlay at level 4,
  same brightness:
  - Display commits dropped from about 1745 to 2 per 15 seconds, and battery
    draw from about 680 mA to 320 mA.
  - Panel idle mode then took it from about 327 mA (forced 120 Hz) to about
    245 mA, measured on the built image against a test module that forces
    each mode.
  - No flicker at low brightness and smooth motion in a 60 fps game; 30 fps
    games were not tried.

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

### Touch stopped until reboot after one bus error

- **Issue:** During a game launch an SPI transfer from the touch controller
  timed out, and touch never came back. The touch service restarted about
  2,000 times, each time reporting "raw stream stalled".
- **Cause:** The completion arrives through the GPI DMA driver's tasklet,
  and the SPI core allows only 200 ms for a 1.5 KB frame at 15 MHz, so a busy
  system can miss it. The driver then disabled its IRQ for good. Reopening
  the stream cleared the error but never re-enabled the IRQ or reset the
  controller. Rebinding the SPI controller to recover crashed in the GPI
  driver, so that is not a recovery path.
- **Fix:** On any packet error the driver resets and reconfigures the
  controller from a freezable work item (identity, report mode, touch
  reports, with backoff) and then re-enables the IRQ. Debugfs
  `houji-touch/recover` triggers the same path; `recoveries` counts it.
- **Result:** A forced reset on the phone recovered in about 0.5 s; the touch
  service restarted once and touch worked.

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

### Intermittent reboot while waking (2026-10-06)

- **Report:** the first short Power tap did not wake the phone; further short
  taps were followed by a reboot. No long-button recovery was reported.
- **Recorded sequence:** the previous boot resumed successfully at 13:00:53 UTC,
  reconnected Wi-Fi and cellular data, then entered native sleep again at
  13:01:46. At 13:01:48 a sensor-DSP wake was classified as dark with effectively
  zero time asleep. The helper immediately wrote `mem` again. The journal ends
  at that second `PM: suspend entry (s2idle)`; there is no matching exit.
- **Reset evidence:** no current kernel panic/oops, remoteproc fatal, or orderly
  system shutdown was retained. Both pstore locations were empty. The newest
  PMIC PON ring entries describe PS_HOLD, warm reset, count 1, then ON. This
  records the reset mechanism, not the software/firmware initiator. The PMIC
  UVLO latch still reads `0x40`, but the retained history does not establish a
  new UVLO event for this incident. The rawdump partition contains an older
  Android 6.1 crash and cannot diagnose this Armada reboot.
- **Comparison tests:** temporary device-PM timing logs captured a successful
  42-second RTC sleep, a three-minute sleep containing a DSP wake at 143 seconds
  and successful re-suspend, and a short Power-key wake. Four kernel sleep
  cycles completed with zero reported suspend failures. ADSP and MPSS stayed
  running. The Wi-Fi hwmon warning seen near the incident also appears in
  successful cycles, so it does not identify the failing component.
- **Second occurrence (2026-10-07):** with ETS2 left running, the phone
  entered native sleep, slept 131 s, served a sensor-DSP dark wake, and logged
  `dark wake 1 after 131 s ... sleeping again`. The journal ends there, with no
  further suspend entry, no panic, empty pstore and no orderly shutdown. Unlike
  the first case, the dark wake followed real sleep, so a zero-duration wake is
  not required. Going back to sleep after a DSP dark wake is still the common
  point. `FAULT_REASON1` again read `0x40`, which is the latched value noted
  above and cannot date the event. Logs are kept root-only in
  `/var/lib/houji-modem/crash-20261007/` on the handset.
- **Third occurrence (2026-10-09), the first with a crash log:** on battery, with
  the GameSir X2s attached, the phone served two sensor-DSP dark wakes (after 65 s,
  then 7 s), going back to sleep within about 3 ms each time. The journal ends at
  `PM: suspend exit` after the second, before the next suspend entry was logged.
  The PMIC recorded a PS_HOLD warm reset. pstore kept the console log but no
  panic record, so Linux never reached `panic()`; something outside its panic
  path reset the chip. The console log ends at the resume before. The kernel
  suspends its consoles during sleep transitions (`printk: Suspending
  console(s)`), and the console runs at `loglevel=6`, so a hang inside a
  transition records nothing there. One lead: every resume powers the WCN7850
  back on over PCIe (`mhi0: Wait for device to enter SBL or Mission mode`), and
  the dark-wake re-suspend follows within milliseconds, likely before its
  firmware finishes booting. Earlier boots completed over a hundred such quick
  re-suspends, so this is a race at most, and unproven.
- **Diagnostics enabled for the next occurrence (live on the handset only, not
  in the image):** `/etc/tmpfiles.d/houji-sleep-diagnostics.conf` sets
  `printk.console_suspend=N`, `pm_print_times=1` and `pm_debug_messages=1` and
  unbinds fbcon (`vtcon1`). `/etc/sysctl.d/90-houji-sleep-diagnostics.conf`
  raises the console level to 7. The ramoops console then records each device's
  suspend and resume callback through every sleep transition, about 1,000 lines
  (90 KB) per cycle; the 2 MB console holds about 20 cycles.
  - fbcon had to go. The kernel switches to a text VT for suspend, so with console
    suspend off fbcon drew every message while the display was suspending: a
    visible terminal at wake, and transitions grew from about 1.6 s to 2.1–2.5 s.
    Unbound, transitions are back to about 1.6 s.
  - The first traced dark wake spent about 1.3 s of its 1.6 s on Wi-Fi:
    `ath12k` suspend_late 95 ms, `qcom-pcie` resume_noirq 380 ms, `ath12k`
    resume_early 585 ms and resume 255 ms. The WCN7850 resume completes before
    the next suspend begins, which weakens the half-booted-firmware lead above.
  - To remove: delete both files and reboot.
- **Conclusion:** intermittent native sleep/wake reset remains unresolved.
  Re-suspending after a sensor-DSP dark wake is a lead, not a proven cause.
  No speculative kernel or sleep-policy fix was applied.
  Raw evidence is retained root-only on the handset; diagnostic logging and
  the temporary alarm/hook were restored/removed after the tests.

### Crash logs did not survive a reset (2026-10-08)

- **Problem:** pstore was empty after every reset, including the wake resets
  above, so none of them left a kernel log.
- **Cause:** the ramoops range copied from the stock tree, 4 MiB at
  `0xa7000000`, is `UEFI_FD` in the memory map in `xbl_config`. The `uefi`
  partition is an ELF whose only load segment is `0xa7000000`, `0x2cd000`
  bytes, and the high-entropy data found in the range matched that partition
  byte for byte. UEFI is loaded there on every boot, warm resets included. A
  pattern written to DRAM there before a warm reset was gone afterwards, while
  the untouched blocks beyond the UEFI image were unchanged.
- **Fix:** the device tree keeps `0xa7000000` reserved and moves ramoops to the
  gap the memory map leaves between `PIL_Reserved` (ends `0xa2b80000`) and
  `Display_Demura` (`0xa3600000`): 4 MiB at `0xa2b80000`, write-combined, with
  2 MiB console, 512 KiB pmsg and 256 KiB oops/panic records. After a warm reset
  `systemd-pstore` archived the previous boot's console log and pmsg, both with
  their markers. A cold reset (a normal reboot, `fastboot reboot`) power-cycles
  DRAM and keeps nothing; the PMIC PON log records it as `HARD_RESET`. The wake
  resets were PS_HOLD warm resets, so the next one should leave its console log
  in `/var/lib/systemd/pstore`.
- **Panics never rebooted:** `reboot=panic_warm` was added so a panic reboot
  keeps the log, but a test panic (`sysrq-c`) froze the phone instead. Its
  console log, recovered after a Power + Volume Down warm reset, showed the
  ath12k panic notifier sleeping: `ath12k_pci_panic_handler` resets the
  WCN7850 through register helpers that first call `mhi_device_get_sync()`,
  which waits for an MHI state change that cannot happen once `panic()` has
  stopped the other CPUs. `panic()` therefore never reached `kmsg_dump()` or the
  reboot. Mainline has the same code. Patch 0031 clears
  `ATH12K_PCI_FLAG_INIT_DONE` before that reset, as power down already does, so
  the accesses skip the wake.
- **Verified (2026-10-09):** flashed as a kernel-only update,
  `7.2.6-armada-houji-kcap3`, with all three changes. All 85 modules loaded
  with no BTF errors. A test panic printed `Rebooting in 10 seconds..` 63 ms after
  the panic notifiers started, with no sleep warning, and the phone came back by
  itself. The PMIC logged a PS_HOLD warm reset. `systemd-pstore` archived both
  the panic record (`dmesg-ramoops-0`) and the console log, each with the test
  marker.
- **Do not rebuild only the Image:** a first attempt rebuilt just the `Image`
  under the old release name. That changed the vmlinux BTF, and this config
  rejects modules whose BTF does not match (`CONFIG_MODULE_ALLOW_BTF_MISMATCH`
  is unset), so no module loaded, `qcom_battmgr` included. Kernel changes need a
  kernel-only update under a new release name.
- **Reaching fastboot:** `systemctl reboot --reboot-argument=bootloader` alone
  reached fastboot once in three tries; it otherwise booted Linux or took
  several minutes. Holding Volume Down while it restarts reached fastboot in
  8 s.
- **Records were overwritten (2026-10-09):** systemd-pstore archives a record
  under its pstore name, and a console record is always `console-ramoops-0`, so
  each archived crash log replaced the previous one. A freeze while a game was
  starting (board skin 46 °C, ended by a Power + Volume Down reset the owner did
  not notice) lost its log to the next crash this way.
  `houji-pstore-archive.service` now runs after systemd-pstore and prefixes new
  records with the time the previous boot's journal ends and that boot's ID, for
  example `2026-10-09T09-59-03Z-0123456789ab-console-ramoops-0`. Records stay
  files in `/var/lib/systemd/pstore`, where Armada's sleep report counts them, and
  the newest 30 are kept.
- **Recovering a frozen phone without losing the log:** hold Power + Volume
  Down until the screen goes black, then release both so it boots Linux. That
  reset is warm. From fastboot, `fastboot reboot` is a cold reset and loses the
  log. Logs are kept root-only in `/var/lib/houji-modem/crash-20261009/` on the
  handset.

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

### armada-powerd killed by its watchdog during sleep

- **Issue:** systemd killed `armada-powerd` 16 times in a day (15 s
  watchdog), each time just after a suspend began, with a core dump each time.
- **Cause:** Each dark-wake round trip spends about 1.5 s suspending and
  resuming devices with userspace frozen while `CLOCK_MONOTONIC` runs, and
  `houji-sleep` re-suspends 2–13 ms after the wake. Every kill followed
  13–73 s of such back-to-back trips; one followed a single 21.5 s trip with
  a Wi-Fi resume timeout. `armada-powerd` was idle, not hung; it only trips
  because its limit is the shortest.
- **Fix:** `houji-sleep` pauses systemd's service watchdogs for the suspend
  loop and re-enables them 5 s after the wake, once services have run.
  A 0.2 s pause per dark wake was tried first and did not prevent the kill.
- **Result:** A forced burst of 15 RTC suspends killed `armada-powerd` with
  the stock behaviour and with the 0.2 s pause, and did not with watchdogs
  paused. On the build, a suspend restored the watchdogs 5 s after waking.

### Power did not sleep the phone in Plasma

- **Issue:** In Plasma a short Power press locked and blanked the screen but
  left the phone awake, drawing about 128 mA.
- **Cause:** PowerDevil's mobile default power-button action toggles the
  screen, and Plasma Mobile binds the Power key to PowerDevil's "Turn Off
  Screen" shortcut rather than its power-button action.
- **Fix:** `/etc/xdg/powerdevilrc` sets the power-button action to Sleep for
  all profiles, and a once-per-user autostart hook moves the Power key to
  that action through KGlobalAccel. User changes are kept.
- **Result:** On the phone, a Power press in Plasma suspended through
  `houji-sleep` and the next press woke it.

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

### Ambient light reported 0 lux

- **Issue:** iio-sensor-proxy had no light sensor. The port's udev rule set the
  sensor type list to `ssc-accel` only, which replaced the upstream rule that
  adds `ssc-light`. With the light driver allowed, the DSP's `ambient_light`
  sensor (`TCS3720ALSPRX`, vendor xiaomi) sent a single 0.0 lux report. It
  stayed at 0 under a flashlight and with the sensor covered.
- **Investigation:**
  - The DSP lists exactly one `ambient_light` sensor. A debug libssc that
    requested every match showed no hidden alternative. Its registry entries
    were byte-identical to the stock registry Android had left on `persist`, so
    the configuration was not the cause.
  - The calibration stream `ambient_light_cal_strm` showed the hardware working.
    Channel counts followed the flashlight and the automatic gain stepped
    correctly, while lux stayed 0 across 2,663 events. The panel backlight
    barely changed the counts.
  - The DSP firmware strings mention a backlight value taken from an OEM
    configuration request, and a "screen off" state that holds back reports.
  - In Xiaomi's `vendor.xiaomi.sensor.citsensorservice.aidl` (odm; examined in
    IDA, class names from RTTI), `Displayinfo2SlpiNotifier` reads display
    events from `/dev/mi_display/disp_feature`. `Lux_Ams_Tcs3720_Fb::sensorSendnotify`
    and `processOemConfig::transferOemConfig` send them to the
    `ambient_light_raw` sensor. The request uses message ID 2048 and a Xiaomi
    `sns_physical_sensor_oem_config` body: field 1 = 5 with field 9 = the
    backlight. `alsAlgo::process` computes lux on the main processor from the
    raw channels. It sends the result the same way, with field 1 = 15,
    field 11 = lux and field 12 = CCT.
- **Tests on the phone:**
  - Sending the backlight changed the value the DSP echoes in its events.
  - Sending lux 123, 456.5, 7 and 250 made `ambient_light` report exactly those
    values within about 2 ms.
  - With backlight 0 sent, the raw channels stopped: one event in 4 s, against
    197 at backlight 61.
  - The raw channels stream only while a client keeps `ambient_light` enabled.
    Fields 0–3 are C, R, G and B, and fields 4–7 are the unit's factory channel
    scales.
- **Fix:**
  - A libssc patch exports `ssc_sensor_send()`.
  - `houji-als` follows the backlight and DPMS state and sends them to the DSP.
    It computes lux like `alsAlgo::process`: channels × scales, an IR ratio
    choosing the low- or high-IR coefficients from `lightSensorConfig.json`,
    minus 0.3, and 200,000 on saturation. It averages 200 ms windows, takes a
    median of three, ignores changes under 10 % and sends the result back.
  - The udev rule now allows `ssc-light`, and the sleep hook stops the service.
- **Result:**
  - iio-sensor-proxy reports `HasAmbientLight` and a lux level, about 1040 in a
    daylit room.
  - A 40-second test followed a flashlight (up to about 5,700 lux) and a
    covering hand (about 110–180 lux).
  - CPU use is 0 without a light client and about 0.7 % of one core with one.
  - The sleep hook's stop and restart cycle was checked by hand.
- **Not done:** Xiaomi's leak correction (`White_test` and the `panel_Info_cali`
  tables) uses a capture of the pixels above the sensor, so it is not ported.
  Readings were not compared with a lux meter.

### Steam's adaptive brightness needs an IIO light sensor

- **Issue:** Steam ignores iio-sensor-proxy.
- **How Steam finds a sensor (IDA, `steamclient.so`):**
  - At startup it scans `/sys/bus/iio/devices/iio:device*` and reads each
    device's `name`. It knows `ltrf216a` and `opt3001` (Steam Deck sensors,
    with calibration gains) and `als` (gain 1.0).
  - For `als` it reads `in_illuminance_raw` as lux.
  - It offers adaptive brightness only in SteamOS management mode with
    `STEAM_ENABLE_DYNAMIC_BACKLIGHT` set to a non-zero value. The
    `[display]` lines in its log report what it found.
- **Fix:**
  - Kernel patch `0023` adds `houji-virtual-als`, an IIO light device named
    `als`. Root writes the value, anyone can read it, and `read_count` counts
    reads.
  - `houji-als` writes each lux value there. While the count keeps changing,
    it holds its own `ambient_light` client, because the raw channels only
    stream while one is enabled. It releases the client 30 seconds after the
    last read.
  - A udev rule keeps iio-sensor-proxy off the device, and a session drop-in
    sets the variable.
- **Result:**
  - On the phone, Steam logged `ALS: 1`, `ALS 0 gain 1.000000 model 3` and
    `adaptive brightness available: 1`.
  - It read the device about five times a second, and the value followed the
    room (17.6 lux in the evening).
- **Open:** with adaptive brightness switched on, a flashlight did not
  brighten the screen. Steam's mapping from lux to backlight, and how it
  writes the backlight on this panel, are not yet traced.

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

### The battery percentage stayed far above the charge left

- **Issue:** The percentage read 83% with 70% left, fell 1 point in 8 minutes
  of heavy use, and dropped 23 points at the next boot.
- **Cause:** The battery firmware reports a linearized capacity
  (`en-linear-soc`, as on stock). When a charge completes it ramps the figure
  to 100%, then moves it at most 1 point per state-machine run. The phone
  uses the external bq27z561 gauge (platform 5); unplugged, the gauge monitor
  stops and the state machine runs only on the 8-minute discharge heartbeat.
  The charge counter (`charge_now`) is the gauge's own value. The firmware's
  charge limit is off, as read on the phone; an earlier static reading that
  blamed it was wrong.
- **Fix:** Kernel patch `0028`: while discharging, `capacity` is the lower of
  the firmware's figure and `charge_now / charge_full` (rounded up), and it
  only falls until charging resumes. Charging and full kept the firmware's
  figure, so a completed charge still reads 100% (charging is capped as well
  since the plug-in fix below).
- **Result:** On battery the phone read 55% with the gauge at 54.3%, where the
  firmware said 57%.

### The percentage jumped up when the charger was plugged in

- **Issue:** Plugging in the charger changed 23% to 54% within a second. The
  charge counter was unchanged at about 21%. The figure then fell about 1 point
  every 5–10 seconds (50, 44, 36, 29) while the status said Charging, until it
  met the gauge.
- **Cause:** The same stale linearized figure as above. Patch `0028` only
  clamped it while discharging, so the clamp ended the moment the status became
  Charging and the firmware's old figure showed through. With the charger
  attached the state machine runs often, which is why it then walked down. In
  that session the battery was also still draining while it read Charging: about
  3 W came in at 9 V and 0.35 A, the phone drew about 5.5 W, and the battery was
  at 44.8 °C, above the 38 °C limit for high charging current.
- **Fix:** Patch `0028` now also caps the figure while charging, at the gauge
  plus 3 points. The cap is not a floor, so the figure still follows the
  firmware down and the gauge up. When the status is Full the firmware's figure
  is kept, because it ramps to 100% a little ahead of the counter and a full
  battery must read 100%. A host test (`charging/test-capacity-clamp.c`, run by
  `native-tests.sh`) compiles `houji_capacity()` out of the patch and covers
  plug-in, unplug, full, the charge limit and unreadable gauge values. It
  reproduces the old behaviour (54 shown with 23 left) against the previous patch.
- **Status:** Flashed as a kernel-only update (`7.2.6-armada-houji-kcap2`). The
  phone booted it, the out-of-tree modules loaded, and while charging the figure
  read 15% with the gauge at 13.8%, inside the cap. The plug-in jump itself was
  not reproduced on the phone: the firmware re-estimates at boot, so its figure
  matched the gauge, and a gap of this size only builds up over a long discharge
  under load. It needs a check the next time the percentage has run well ahead of
  the gauge. Until then the fix rests on the host test of the shipped function.

## Thermal limits

### Nothing limited the CPU or GPU by heat

- **Issue:** The kernel's only CPU trips are critical ones at 110 °C. The GPU has
  a passive trip at 95 °C. Nothing reacted to skin or board temperature. In a
  game the board CPU thermistor reached 61 °C and the skin estimate 46.5 °C, with
  the GPU at its 834 MHz top.
- **Stock behaviour:**
  - `mi_thermald` drives its limits from `VIRTUAL-SENSOR0`, which
    `board_thermal.py` already reproduces.
  - The decoded `thermal-normal.conf` caps each CPU cluster in steps from
    25 °C to 50 °C skin. It holds the GPU at level 2 above 15 °C, pauses cpu3,
    cpu4 and cpu7 at 50 °C, and adds CPU caps at 3 % and 1 % battery.
  - `thermal-mgame.conf` replaces this in games. The CPU runs uncapped until
    46 °C skin and then drops to about 0.6 GHz, and the GPU is held at level 3.
- **Semantics (IDA):**
  - `ss` sections (`timer_expires`, 0xa6298) select the highest trigger
    reached. Their clear values are unused. The cap then moves one step of the
    device's frequency table per poll in both directions. It only jumps while a
    profile is being reloaded.
  - `monitor` sections (`timer_expires_1`, 0xa7138) keep a hysteresis flag per
    threshold. The level ends the leading run of active thresholds; reversed
    tables such as battery level use the trailing run.
  - Several sections writing the same device combine: the lowest CPU frequency,
    the highest GPU level and every paused core win.
- **GPU levels:** `msm_kgsl.ko` (`adreno_device_probe`) builds its SKU code from
  the feature code alone unless that code is 209–224 or 241–256. On this unit
  the feature code is 2, which selects the table whose SKU list is `<0>`: 903,
  834, 770, 720 MHz and down. Level 2 is 770 MHz and level 3 is 720 MHz.
- **Fix:** `houji-thermal` applies the stock tables through the cpufreq and
  devfreq cooling devices:
  - These are separate frequency limits from the profile's `scaling_max_freq`
    and GPU `max_freq`, so `armada-powerd` and Steam keep working unchanged.
  - The CPU cooling devices are bound to no thermal zone.
  - The GPU's is shared with the kernel's 95 °C trip. The service never lowers
    a state the kernel raised, and re-applies its state after the kernel resets
    it on resume.
  - The prime cluster's energy model lists a 3302.4 MHz boost state that
    cpufreq does not, so its cooling states are offset by one.
  - At the owner's request, Game Mode keeps the normal CPU steps and leaves the
    GPU at full speed until the stock game trip (46 °C, 770 MHz; 48 °C,
    720 MHz). Plasma uses the stock normal profile.
- **Result:** Live in a game:
  - The caps followed the skin estimate (about 41 °C: prime 1248 MHz) and rose
    one step per second as it cooled.
  - Stopping the service released every cooling state.
  - Taking cpu4 offline and back online worked.
  - Policy tests cover both evaluators, level mapping and profile combination.
- **Not ported:** the 51 °C brightness cap (its unit is not established), the
  modem, Wi-Fi, NPU, torch and voice limits, and framework overrides.

### A reset under load was a brownout

- **Symptom:** Launching a game at 46 % battery and about 46 °C skin reset the
  phone. The journal stopped without a shutdown. The owner saw a red CPU symbol
  first; this was probably the Steam performance overlay.
- **Evidence:** The PMK8550 `FAULT_REASON1` register (PON PBS 0x8C8) read
  `0x40`, the UVLO (under-voltage lockout) bit. `FAULT_REASON2` (0x8C9), which
  holds the over-temperature bit `OTST3`, was 0. Battery telemetry updates only
  about once a second (3.63 V at 1.6 A during play), too slowly to show the dip.
- **Cause (likely):**
  - The stock board overlay configures BCL on `pm8550b` (battery) and `pm8550`
    (system supply), with three alarm levels each. Its thermal zones respond by
    cutting the GPU to cooling states 2, 4 and 5 or more, and the NPU and modem
    too.
  - The upstream kernel has no BCL driver, so these alarms do nothing. The PMIC
    BCL blocks are enabled, and their live battery-current reading matches the
    load.
- **Status:** Open. Fixing it needs a kernel BCL driver and device-tree zones.
  The thermal limits above lower the average load but cannot react within
  milliseconds.

### Steam's performance overlay showed zeros and no battery

- **Issue:** The full overlay showed 0 for GPU and CPU power, GPU voltage, GPU
  junction and memory temperature, memory clock, fan, VRAM and RAM
  temperature, and it had no battery line.
- **Cause:**
  - Steam only passes MangoHud a preset number. MangoHud 0.8.4's built-in
    presets include readings that come from AMD, Intel or NVIDIA drivers.
    MangoHud hides them itself on the Steam Deck and other known handhelds,
    but not on an Adreno phone.
  - Armada's MangoHud reads only `/sys/class/power_supply/battery`, which is
    the name Armada's own kernel gives Qualcomm batteries (patch `0503`,
    paired with MangoHud patch `0003`). This port's battery was
    `qcom-battmgr-bat`. MangoHud logged "No battery found".
- **Fix:**
  - `MANGOHUD_PRESETSFILE` in the Steam session points to
    `/usr/share/houji/mangohud-presets.conf`. It holds MangoHud's built-in
    presets 2–4 without those readings.
  - Kernel patch `0024` names the battery `battery`, like the rest of Armada.
    The port's charging, thermal, battery-wait and Steam power services use
    the new path. `houji-sleep` treats `battery` wakeups as bookkeeping, as
    it did for `battmgr`.
  - UPower and Steam's battery reading go by type, so they are unaffected.
  - A first attempt used `BAT0`, the conventional ACPI laptop name that
    upstream MangoHud matches. Armada's MangoHud ignored it.
  - Remaining time then read 00:00. The battery firmware reports
    `time_to_empty_avg` and `time_to_full_avg` as 0; on stock, the Android
    framework makes the estimate. Kernel patch `0026` returns "no data" for
    a 0 on houji. MangoHud then estimates from `charge_now` and
    `current_now`, like UPower does.
- **Result:** Tested on the phone. The overlay no longer shows the zeros, and
  its battery row shows power and remaining time while discharging. While
  charging it shows neither, which is MangoHud's normal behaviour. Separate CPU and GPU power stay
  unavailable because the SoC exposes no power meters to Linux.

### The overlay's battery percentage differed from Steam's

- **Issue:** The overlay showed 70% while Steam's top bar showed 83%.
- **Cause:** Steam, through UPower, shows the firmware's `capacity`. MangoHud
  uses `capacity` only for batteries without charge readings; otherwise it
  divides `charge_now` by `charge_full`, the raw cell charge. On houji the two
  differ by up to 13 points depending on charge level, so no offset fixes it.
- **Fix:** MangoHud patch `0007` (in `packages/mangohud`) prefers `capacity`.
  Its power and remaining time still come from the charge and current
  readings. The pinned Armada image predates the patch, so the port builds
  `mangoapp`, the overlay Steam runs, from Armada's MangoHud version and
  patches (`mangohud/build.py`) and installs it over the image's copy.
- **Result:** Tested on the phone with the rebuilt `mangoapp`; the overlay
  shows the same percentage as Steam.

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

### Bluetooth gamepad Home button

- **Issue:** Over Bluetooth, the owner's gamepad identified as `1949:0402`
  ("Gamepad") and no InputPlumber profile matched it. Home did nothing, and
  neither did Home + A.
- **Cause:**
  - Home is the consumer usage AC Home (`0x0C0223`) in a Consumer Control
    collection of its own. `hid-generic` creates one input device per
    collection, so Home became `KEY_HOMEPAGE` on "Gamepad Keyboard", and
    Steam reads only "Gamepad".
  - Steam then uses SDL's mapping "Amazon Fire Game Controller", which expects
    Guide at button 17. SDL numbers evdev buttons by key code; the 16 HID
    buttons give 0–15. `BTN_MODE` was no option: HID button 13 already
    reports it, as SDL button 12 (`misc1`).
- **Fix:** Kernel patch `0025` (`hid-lab126-gamepad`):
  - It rewrites the Consumer Control collection as a Game Pad application,
    keeping the Consumer usage page. It does this only when the descriptor
    matches byte for byte. The collection then joins the gamepad's device.
  - It maps AC Back to `BTN_TRIGGER_HAPPY1` (SDL 16) and AC Home to
    `BTN_TRIGGER_HAPPY2` (SDL 17).
- **Result:** Tested on the phone, first as a loaded module and then in the built image:
  - Home arrived on the gamepad device and opened the Steam menu.
  - The physical A sends HID button 2, which SDL's mapping calls B, so A and B
    are swapped on this unit. Home + the physical B opens quick access. The
    owner chose to keep this layout. Swapping in the kernel would break a
    genuine Fire controller, which shares the IDs.

### GameSir X2s record button as Quick Access

- **Issue:** The owner wanted the GameSir X2s Type-C (USB `3537:0105`) record
  button to open Quick Access.
- **Cause:** The button sends no gamepad button. Its Consumer Control interface
  reports Volume Down and Power together for about 5 ms (Android's screenshot
  shortcut), so it lowered the volume and logind saw a power key press.
  `powerbuttond` only follows the phone's own power key, so it never slept.
- **Fix:** InputPlumber profile `11-gamesir-x2s.yaml` combines the gamepad and
  Consumer Control interfaces into a virtual Xbox 360 pad. Its stick and trigger
  map matches the SHANWAN one; the face buttons already use Xbox codes. The record
  map turns Volume Down + Power into Quick Access, which the Xbox 360 target sends
  as Guide, then A 160 ms later.
  - A plain chord fired on the 5 ms press, so A was held for 0–9 ms. Steam then
    sometimes saw Guide alone and opened the Steam menu.
  - A delayed chord fires on release and holds Quick Access for 100 ms. In this
    InputPlumber version a delayed chord only fires for presses it tracked, and
    only chord mappings track presses, so a second chord that can never complete
    (it also needs `KEY_F24`, which the device cannot send) tracks both keys.
- **Result:** Five presses in a row opened or closed Quick Access. Volume and
  power are no longer affected, and Steam reads the virtual pad instead of the
  raw controller.

### GameSir X2s Home and record buttons swapped (2026-10-09)

- **Request:** Home should open Quick Access and the record button should be the
  Steam button, on the X2s only.
- **Finding:** the X2s firmware sends Home and record only when they are
  released, as a press and release about 5 ms apart, however long the button was
  held. Raw HID captures during 2-second holds showed this, and the owner saw both
  menus open only on letting go. Neither delay can be removed in software.
- **First version:** on the Xbox 360 target, Home became Quick Access through the
  record button's delayed chord, and record became Guide. Both menus felt slower.
  After each button's release report, the virtual pad sent Guide within 1 ms for
  record and within 10 ms for Home. But that target has no Quick Access button:
  it sends Guide, then A 160 ms later. The swap had moved those 160 ms onto Home.
  Holding Guide for 100 ms on record only delayed the menu, because Steam opens it
  when Guide is released.
- **Fix:** the X2s profile now targets `deck-uhid`, so Steam sees a Steam Deck
  controller with real Steam and Quick Access buttons. That target holds a press
  shorter than its 8 ms frame over to the next frame, so plain mappings suffice.
  The gamepad interface's map, renamed `gamesir_x2s_gamepad` because it now holds
  more than axes, maps `BTN_MODE` straight to Quick Access. The record map is a
  plain chord to Guide.
- **Result:** the owner found Quick Access quicker, the Steam menu on record, and
  sticks, triggers, face buttons and D-pad working.
- **Caveat:** Armada's controller-type setting applies to every InputPlumber
  device. Its default here, the first of `ARMADA_IP_TARGETS`, is `deck-uhid`.
  Choosing an Xbox type in Armada Control would put the X2s back on the Xbox 360
  target, where a 5 ms Quick Access can open the Steam menu instead.

### The controller-as-mouse pointer was invisible

- **Issue:** In CS2's menus the stick moved an invisible pointer: menu items
  highlighted, but nothing was drawn.
- **Cause:** Steam sends controller-as-mouse motion to the game's Xwayland as
  XTEST. Xwayland passes XTEST to the compositor only through libei, and the
  port's Gamescope was built with `input_emulation` disabled. It logged
  "Gamescope built without libei, XTEST will not be available!". The X pointer
  moved, but Gamescope never saw motion, so it kept the cursor hidden
  (`GAMESCOPE_CURSOR_VISIBLE_FEEDBACK` stayed 0).
- **Fix:**
  - The Gamescope build enables `input_emulation`. It is built against Fedora 44's
    `libeis-devel` 1.5.0 and runs on the image's `libeis` 1.6.0 (same
    `libeis.so.1` ABI). Gamescope now logs "Successfully initialized libei" and
    exports `LIBEI_SOCKET`; Xwayland already links `libei`.
  - Gamescope draws a game's cursor at its native size; CS2's is 24×24, about
    1.3 mm on this 460 ppi panel. The gamescope-session package patch `0008` adds
    `ARMADA_GAMESCOPE_CURSOR_SCALE_HEIGHT`, and the Xiaomi 14 profile sets 600:
    the 1200 px landscape output gets a 72 px cursor. The houji image applies
    the patch until the pinned Armada image ships it.
- **Result:** The pointer showed while moving (feedback 1 in 117 samples) and the
  owner judged its size good. The scaling alone, without libei, still left it
  invisible.

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

### NFC failures were not logged

- **Issue:** Card emulation failed four times in a row and the manager only
  said "NFC operation failed".
- **Fix:** The service logs the exception type, where it was raised, the
  phase it was in, the controller's fixed message and any errno, never
  payloads or card data. An unsupported activation now names its RF
  interface, protocol, technology and mode, payload size and credits.

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

## Houji Settings

### A Decky plugin for the phone's own settings (2026-10-09)

- **Request:** settings Steam does not have, reachable in Game Mode: rotation
  lock, eSIM download and SIM choice, mobile data and roaming, a charge limit and
  NFC.
- **Design:** a Decky plugin, Houji Settings, with a thin backend that sends each
  request to `/run/houji-settings.sock`. `houji-settings.socket` (`Accept=yes`)
  starts the native root helper, `houji-settings`, for each connection, with the
  socket as its stdin and stdout. The helper reads one JSON request and uses the
  existing cellular and NFC services, NetworkManager and ModemManager.
  - **Why a socket:** the first version ran the helper as a subprocess of the
    plugin. Decky runs under FEX, which resolves absolute paths in its x86 rootfs
    first, so `#!/usr/bin/python3` started the rootfs's x86_64 Python, which has no
    `gi.repository`. Every D-Bus call failed silently while command-line tools
    still worked. The panel loaded, but switching to eSIM did nothing, and the
    cellular service and NFC appeared unreachable. Shell tests ran the helper
    natively and could not show it. Calling the plugin from Steam's own JS
    context through the CEF debugger found it; `FEXBash` confirmed the x86
    Python. Armada Control avoids the same trap with its native service.
- **Decky logs every call:** its frontend writes each plugin call's arguments
  (`Calling PY method ... with args`) and result (`Resolved PY call with value`)
  into Steam's `webhelper_js.txt`. So:
  - replies carry no identifiers. eSIM profiles are named by an HMAC of the ICCID
    under a key in `/run/houji` that changes at every boot. ModemManager and
    NetworkManager data is reduced to fixed fields (state, operator name, signal,
    technology, roaming), so no IMEI, ICCID, phone number, operator code or APN
    login is returned. A test feeds the helper all of these and checks the reply;
    on the phone, the scan of the real status reply against its actual
    identifiers found none.
  - an activation code is never a call argument. The frontend gets a single-use
    token valid for two minutes, then posts the code to a listener the plugin
    backend opens on `127.0.0.1`. The backend hands it to the helper on stdin, and
    the helper passes it to the cellular service over D-Bus, which gives it to
    `lpac` on stdin. In Steam's own JS context (origin
    `https://steamloopback.host`), a fake code reached the helper and a replayed
    token was refused. Steam's JS log, the plugin log and the journal did not
    contain it afterwards.
- **Rotation lock:** the orientation service reads
  `/etc/armada/houji-rotation-lock`. While it names an orientation, the service
  applies it and releases the accelerometer. The service publishes the
  orientation it applied so the plugin can lock to it. Its unit has
  `ProtectSystem=strict`, so it writes into its own `RuntimeDirectory`
  (`/run/houji-orientation`) rather than `/run/user`.
- **Charge limit:** the charging policy holds the battery's charge current at 0
  once the configured percentage is reached. Charging stops and the supply keeps
  powering the phone. Unlike a positive vote, 0 needs no lease renewal. Charging
  resumes 5 points below the limit; each new attachment starts afresh. A
  non-PD supply gets its 500 mA default back only after a hold, so a USB-PD
  temperature pause is never undone.
  - **Bug found while testing:** the first version published its state under
    `/run/houji`, which is read-only for this unit (`ProtectSystem=strict`). The
    write failed after every charging decision. The loop's error path then
    skipped adapter authentication and reset the current ramp each second, so
    charging stayed at 500 mA. The state now goes to the unit's
    `RuntimeDirectory`, and a failed report cannot affect charging.
  - **Measured:** at 94% on a USB-PD charger, an 80% limit took the battery from
    1.32 A to 0 mA within four seconds. The policy logged `host charging held at
    the charge limit` with authentication still passing. Removing the limit
    resumed the normal ramp.
- **Cellular:** the cellular service gained `profile-nickname` (the eUICC stores
  the name; no carrier notification), and its profile list now includes
  nicknames. That service selects eSIM as the data SIM before any profile
  operation, so the plugin only offers profile changes once eSIM is the chosen
  data SIM, and the helper refuses them otherwise. Mobile data follows Plasma
  Mobile's order: block device autoconnect, set autoconnect on the most recent
  profile only, activate it, and allow autoconnect again.
- **Build:** the port builds the plugin in Armada's `node:22-slim` image with
  `npm ci` from a lockfile matching Armada Store's dependencies. The container
  build was byte-identical to a local one. The image stages it in
  `/usr/share/decky-plugins`, and `armada-decky-sync` seeds it at boot.
- **Verified by the owner:** the panel loaded with all sections, the rotation
  lock held while the phone was turned, the landscape label matched the USB side,
  and the charge limit showed its hold.
- **Not yet tested on hardware:** eSIM download, switching and renaming from the
  plugin, and the mobile data and roaming switches. Slot 1 reported `sim-missing`
  at the time, and the SIM selection was left unchanged. The charge limit has
  not been tested over a long sleep or with wireless charging.

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
- **A sound archive reported as corrupt, by chance:** on the phone the installer
  said `Module archive is corrupt` for a bundle whose checksums matched and which
  `zstd -t` accepted. Repeating the same check-only run gave one failure in three.
  `tarfile` in streaming mode stops at the end-of-archive marker, so `zstd` could
  still be writing the padding behind it when the pipe closed, and it then exited
  with a broken pipe. The reader now drains what follows the marker (capped at the
  size limit) before it reads `zstd`'s exit status. A test with 8 MB of padding
  reproduced the error every time before the change and passes after it. The bundle
  was repackaged, and the images and modules came out byte-identical.

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

### Counter-Strike 2 drew most of the world black

- **Symptom:** In native (Linux) CS2, from the first frame, large meshes rendered
  pure black except where direct sunlight fell on them. Sky, weapons, HUD,
  decals and some indirectly lit walls were correct. Seen on Dust II and Mirage.
- **Cause:** CS2 never used the phone's Turnip. It ran Armada's x86 build of
  Turnip (`/usr/share/guestos/fex-mesa`) under FEX emulation, and that driver
  misrenders the game. Steam's FEX should forward Vulkan to the native driver
  through its Vulkan thunk, which Armada enables, but the thunk only engages when
  the guest opens a library path listed in Steam's FEX `ThunksDB.json`.
  pressure-vessel exposes the guest loader as
  `/usr/lib/pressure-vessel/overrides/lib/x86_64-linux-gnu/libvulkan.so.1`, a
  link into the provider's Arch-layout `/run/gfx/main/usr/lib`, and Steam's FEX
  build does not expand `@PREFIX_LIB@` to that directory. `/proc/<pid>/maps`
  showed `libvulkan-guest.so` was never loaded, so the earlier "thunk off" test
  had changed nothing.
- **Fix:** `armada-game-launch` writes a per-game FEX `ThunksDB.json`
  (`$STEAM_COMPAT_DATA_PATH/fex-emu/`, FEX's per-app config directory) whose
  Vulkan entry keeps the depot's paths and adds the pressure-vessel link. It
  only does so for launches through Steam's Linux FEX compat tool; arm64ec
  Proton launches are untouched. Steam's FEX depot is not modified. Tested by
  `tests/fex-vulkan-thunk-test.sh`. The houji image stages the launcher from this
  checkout until the pinned Armada image includes it.
- **Result:** With only the launcher change and the original depot, CS2 loaded
  `libvulkan-guest.so`, `libvulkan-host.so` and the host
  `/run/host/usr/lib64/libvulkan_freedreno.so`. Dust II rendered correctly,
  including the shaded CT-spawn interior and shaded streets that were black.
  The first run with the native driver compiled shaders for about ten minutes
  before the menu appeared; later launches reach it much sooner. Memory
  stays tight (available memory briefly reached 0 MB while loading a map).
- **Tests that did not help, all made on the emulated x86 Turnip:** Valve's
  `tu_override_uncached_as_cache_coherent`, `TU_DEBUG` render-path switches, ir3
  optimisation switches (`IR3_SHADER_DEBUG`, full recompiles), a Turnip build
  without Armada's patch `0001`, FEX's strict `compatible` profile and CS2's
  graphics settings. Why the x86 Turnip build misrenders CS2 is not known.
- **Memory:** Any change that invalidates Steam's precompiled shaders makes CS2
  recompile in-process. One such run froze the phone and another was killed by
  the OOM killer. A temporary swap file on `/run/houji/data` let full recompiles
  finish.

## Open problems

- Intermittent animation freeze around Steam wake: never reproduced or explained.
- Sleep has been exercised for hours, not days. The standby power cost of the
  sensor DSP's periodic wakeups is unmeasured, and DSP crash recovery is
  untested.
- Wireless charging starts, then stops.
- Stock HyperCharge behaviour, the 15–47°C fast-charge path and charge-pump transitions
  are unverified.
- Heavy load can reset the phone through a brownout (PMIC UVLO). Stock BCL
  voltage alarms, which cut the GPU quickly, have no kernel driver yet.
- GPS: restarting the modem can reboot the phone.
- The x86 Turnip build used under FEX when the Vulkan thunk is off misrenders
  Counter-Strike 2. The cause is unknown; native CS2 now uses the thunk.
- NFC: NDEF reads from physical tags, other tag families and writes are unverified.
- Gyro aiming, cellular, cameras and fingerprint are not implemented.
- Other panel revisions, storage sizes and regional firmware are untested.
