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

Latest release migration: [Armada 20261010](#armada-20261010-migration-2026-10-18).

## Armada 20261010 migration (2026-10-18)

- **Request:** Update to the latest upstream release, park the FSR experiment
  without deleting its code, and research Steam automatic brightness.
- **Release identity:** GitHub's latest stable release is `20261010`, source
  `c3d178c9e5cc7107aaa128b8c1f384a9c6bd8dfa`, image version `20261010.c3d178c`.
  The arm64 OCI manifest is pinned at
  `sha256:f1be3089bb7c917adf41068704ce8dd9a679e3c47cab466b3d2832be1b2f0cde`;
  all downloaded blobs were checksum-verified. Source changes were imported
  relative to the previous release with Houji changes preserved. No merge
  commit or publication was performed during preparation.
- **Port integration:** The 192 shared kernel patches plus 30 Houji patches
  apply cleanly to pinned Linux 7.2.6. New kernel release `k20261010` includes
  upstream GMU interrupt/timeout, GPU priority, UFS, UART and Wi-Fi scan fixes;
  these are not claimed to resolve Houji's unexplained native-sleep resets.
  Original VM reservations remain selected for the ongoing comparison.
  Gamescope 3.16.31-ogc1 is built with all release patches plus Houji's touch
  association/fresh-frame wake. MangoHud retains capacity reporting and overlay
  throttling alongside the release's fdinfo/upscaler changes. A Houji device
  entry enables the new steamos-manager integration; the duplicate user-session
  vpower writer is masked because Houji already publishes that telemetry.
- **FSR parked:** A master `HOUJI_FSR4_EXPERIMENTAL=1` gate now controls the
  game hook, NGX bridge, frame server, QNN session, standalone runner, framegen
  path and Decky controls. Old per-feature flags cannot activate those paths
  alone. On the handset, the server was stopped, Goat's original launch options
  restored, forced TAA/50% render-scale overrides removed, and injected files
  moved to a private reversible backup. Sources/models/build scripts remain.
  The pre-experiment game preferences were already restored and were retained.
- **Preserving installation:** The stager retains owner settings, diagnostics,
  writeback budget, SSH/Decky setup and home. Modem private storage uses existing
  backing directories through bind mounts rather than copying live NV/secure
  files. On this installation, all five slot-B boot images were flashed through
  fastboot, with init_boot last. No slot-selection or GPT-writing command was
  used. The proposed direct-write installer was stopped before any boot writes.
- **Preparation checks:** All 36 Houji test suites and performance-settings
  checks passed, including the stager's two backing-store tests and
  27 installer tests. Gamescope, MangoHud, kernel and external
  modules built; target-loader dependency checks passed. Packaging verifies
  stock-ABL metadata, device-tree symbols/reservations and image hashes.
  The new kernel's BTF validates all 15 GPU probe definitions; instrumented
  GEM/fence/mutex and PSCI tracepoint source semantics match the previous build.
  Runtime validation is recorded after installation, not inferred from builds.
- **Reboot-command failure and correction (Taiwan time, UTC+8):** At 03:04:43,
  `systemctl reboot bootloader` failed with `Too many arguments`. At 03:05 the
  handset entered charger light sleep; its lost SSH connection was incorrectly
  taken as evidence of a reboot. The boot ID was unchanged. At about 12:22,
  `systemctl --reboot-argument=bootloader reboot` succeeded and fastboot appeared
  immediately on the already-connected cable. Check command results and the
  transient unit's journal, rather than treating a scheduled unit as success.
- **Installed validation (12:24–12:25 UTC+8):** Booted Armada
  `20261010.c3d178c` and `7.2.6-armada-houji-k20261010`. All five flashed payloads
  matched their manifest hashes when read back from Linux. GPT verification
  reported no problems (the same stock-layout warnings as before). Steam's
  library UI rendered, Gamescope associated the built-in touch with DSI-1, audio
  exposed the internal speakers/microphone, and device services were active.
  Shared home and private modem bind mounts were present. GPU tracing armed and
  the matching sleep observer loaded. The existing controller-type oneshot
  failed with no composite controller attached; InputPlumber itself was active.
  FSR remained disabled. Native-sleep reset stability is still unresolved.

### First post-update reset reaches partial PSCI return (2026-10-18)

- **Report:** A resume reboot after the release update. The handset booted the
  same `k20261010` kernel again; raw evidence was preserved privately before
  further tests. All times below are Taiwan time (UTC+8).
- **Before native sleep:** The independent recorder confirms charger light
  sleep at 12:26:51: both DRM CRTCs inactive and app.slice frozen. A Power wake
  at 12:29:07 completed normally. On battery, the first native attempt returned
  on Power IRQ 21 at 12:30:33. The next sleep transaction began at 12:30:40 and
  completed five sensor-DSP dark-wake cycles before its final re-entry.
- **New endpoint:** Cycle 7 entered at 12:31:31. All eight CPUs printed PSCI
  entry markers, including one selected state `0x4100c344`. After a recorded
  30.658-second sleep, CPU0 returned and repeatedly re-entered/returned with
  `0x4100c344`; CPU1 also returned and re-entered. The console then ends without
  `machine-exit`, device-resume callbacks, `post`, or the ledger's kernel-return.
  The last retained activity is approximately 12:32:02 after accounting for
  suspended time. Only 26 bounded detail records were printed in this attempt,
  below the 256-record limit; the missing completion is not that limit.
- **Interpretation:** Unlike the older consoles ending at the general
  `PM: suspend-to-idle` marker, this capture establishes that CPUs reached the
  PSCI call boundary and at least CPU0/CPU1 returned before full system resume.
  The markers surround `psci_cpu_suspend_enter()` and do not record its return
  value. They do not prove why a selected state immediately returns, which
  component initiated the reset, or that an unprinted CPU never returned.
  There is no saved panic or Linux restart-probe marker. The narrowed target is
  the CPU/domain idle exit/re-entry and wake coordination, before device resume.
- **GPU:** The interrupted checkpoint has empty allocation/reclaim/retirement/
  lock/fence maps and no candidates after 257,118 processed trace lines. The
  retained console has no translation-fault report. Missing final loss counters
  prevent treating that as proof that every GPU path was healthy.
- **Status:** The upstream update has not fixed the native-sleep reset. The
  observer and continuous collector remain enabled; no new suspend trial or
  power-policy change was made to investigate this report.

## Boot and storage

### Suspend-only APSS collapse guard deployed (2026-10-18)

- **Change:** The live governor guard caps Houji's shared cluster state at
  `0x41000044` during s2idle, avoiding the `0x4100c344` APSS/LLCC transition
  captured in the reset. CPU rail collapse, device suspension and battery
  native sleep remain enabled; awake idle selection is unchanged. Original
  latency constraints and rejection results remain authoritative. Patch `0033`
  implements the equivalent board-specific rule for future kernel builds.
- **Validation, Taiwan UTC+8:** A 20-second RTC smoke test passed. A subsequent
  attempt served a DSP dark wake and resumed on Power IRQ 21; the test stopped
  rather than suspend again while the owner was using the phone. With approval,
  six 90-second RTC transactions then passed from 12:49:06 to 12:59:06. Together
  these exercised 12 kernel suspend/resume cycles and 68 guarded selections
  without a reboot, device-resume failure or new kernel fault. All 161 observed
  PSCI entries had matching exits. Two attempts included 38 and 43 CPU entries
  respectively, so rapid re-entry itself was exercised. No detail records were
  suppressed, every system resume completed, and the user slice thawed.
- **Deployment:** The matching module was installed and boot-enabled on
  `k20261010`, then unloaded/reloaded through its installed service successfully
  while awake. Its callback/init/unload machine code matches the trial build.
  No boot partition or slot state changed. The source patch applies with zero
  fuzz, compiles against the kernel, and its extracted governor test covers
  latency vetoes, alternate state layouts and non-s2idle selection.
- **Limits:** This is tested containment of one failing power transition, not
  identification of the firmware reset initiator. The PMIC ring from the crash
  decodes as PS_HOLD/WARM_RESET. Six transactions plus their awake intervals
  consumed a reported 33 mAh over 600 seconds; the batched gauge updates do not
  establish sleep-only power or the difference from the previous policy.
  Long standby is still unverified. Diagnostics remain active. See
  [guard build and rollback](power/s2idle-guard/README.md).

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
- **Fourth occurrence (2026-10-09 12:16), captured with the diagnostics:** on
  battery, the phone served a dark wake after 1 s asleep and began to sleep again
  2.5 ms later. Every device then suspended successfully. The console log's last
  line is `simple-pm-bus soc@0: PM: simple_pm_bus_suspend returned 0`, the final
  device callback before the SoC enters s2idle. In a successful cycle the next
  line is `simple_pm_bus_resume`. There was no panic record, and the PMIC logged
  a PS_HOLD warm reset. The last recorded device callback completed successfully;
  no resume was recorded. This does not establish whether the failure was in
  later Linux suspend/idle code, firmware, the hypervisor or the power controller.
- **Common point at that time:** all five resets in native sleep came as the system went back
  to sleep milliseconds (2.5–3 ms) after a sensor-DSP dark wake. Some came after
  a dark wake that followed only 1–2 s of sleep. The root cause is still unknown.
- **Mitigation (2026-10-09):** `houji-sleep` waits `DARK_SETTLE` (0.3 s, chosen by
  the owner over 1 s) after each dark wake before suspending again. Userspace
  stays frozen and the display off. On the phone, three dark wakes in a 10-minute
  sleep each re-entered sleep 0.31–0.33 s after resuming, and the phone woke
  normally. Whether the resets stop needs longer use. The cost is 0.3 s awake per
  dark wake, on top of about 1.6 s per round trip. A USB-PD charger keeps the
  phone in light sleep, where this loop does not run.
- **Conclusion:** intermittent native sleep/wake reset remains unresolved.
   Re-suspending after a sensor-DSP dark wake is a lead, not a proven cause.
   No speculative kernel or sleep-policy fix was applied.

### Reset on the first native-sleep entry (2026-10-10)

- **Report:** the owner pressed Power to sleep and the phone rebooted. This
  followed removal of the experimental offscreen-download feature. The installed
  Power handler, native display hook and sleep helper matched the committed
  pre-feature versions byte for byte; the previous boot had no offscreen-service
  journal entries, and its units and configuration were absent after reboot.
- **Retained evidence:** a 344,119-byte ramoops console and the previous journal
  were preserved in a root-only incident directory on the handset. Power was
  pressed at boot time 1260.034 s; `PM: suspend entry (s2idle)` was recorded at
  1263.086 s. The console ends at 1263.396 s after
  `simple_pm_bus_suspend returned 0`. There is one suspend entry and no resume
  or sensor-DSP dark-wake event in that boot. No panic/oops record, kernel panic,
  watchdog lockup, remoteproc crash or orderly restart was retained.
- **Meaning:** the failure can occur on initial sleep entry, not only during
  rapid re-suspend after a dark wake. The 0.3-second dark-wake settle is therefore
  not a complete fix. A successful last callback and absent panic trace do not
  identify the reset initiator or rule out Linux. Native sleep remains enabled;
  no further crash/suspend trial or speculative runtime change was made during
  this inspection.

### Three resets while charging through the X2s (2026-10-10)

- **Owner's context:** all three resets in the 04:00–12:00 UTC+8 interval
  happened with the charger connected through the GameSir X2s. The owner was
  asleep and could not establish the battery level at the failures. Clock-sync
  records put the subsequent boot starts at approximately 05:55:10, 11:34:11 and
  11:55:41. The preceding journals end at 05:21:00, 11:12:54 and 11:55:14; those
  gaps do not establish the exact reset times.
- **Shared retained sequence:** all three ramoops consoles show native s2idle
  entry, `native suspend: reduced host FCC to 500 mA`, successful USB-device,
  xHCI and DWC3 suspend callbacks, then end after the final recorded
  `simple_pm_bus_suspend` callback. USB-PD policy was active in each preceding
  boot. No USB disconnect/enumeration failure was retained immediately before
  the final suspend. Successful callbacks do not rule out a later USB/PD or
  power-role interaction.
- **Policy lead:** the first boot entered charging light sleep at 03:59:03 and
  later reached native suspend. The charging guard is based on active charging,
  not cable attachment alone: `Full`, or `Not charging` at 100%, permits native
  sleep while still plugged in. Missing telemetry, an unavailable charging
  service or a timed-out policy query also removes the guard. Thus overnight
  completion could expose the native-sleep failure, but status/capacity and the
  exact guard rejection reason were not captured at these transitions.
- **Assessment:** charging through the X2s is a common condition and warrants
  comparison with direct charging and battery-only operation. Neither the X2s,
  the 500 mA reduction nor full-charge transition is a proven cause. Keeping
  light sleep for the whole external-power attachment would avoid this native
  transition, but was not applied during the read-only investigation. Native
  sleep on battery and the charging policy remain as configured.
  Raw evidence is retained root-only on the handset; diagnostic logging and
  the temporary alarm/hook were restored/removed after the tests.

### Internal storage lost on resume: UFS PHY did not initialize (2026-10-10)

- **Report:** after a long Hogwarts Legacy session on battery, the owner slept
  the phone; on wake the screen stayed black. The X2s stayed powered, but Wi-Fi
  never returned. A Power + Volume Down warm reset preserved the console log.
- **Retained sequence:** native s2idle entered and exited normally. During
  resume, `ufshcd_system_resume` fully reinitialized the UFS link, as expected
  with `spm_lvl=5`, because SM8550/SM8650 have no UFS PHY retention. Every attempt
  failed: `qcom-qmp-ufs-phy: phy initialization timed-out`, `Failed to calibrate
  PHY: -110`, then `Host init failed -5` and `ufshcd_wl_resume failed`. All 15
  attempts failed within about one second, followed by SCSI/EROFS read errors on
  the root image. The display and network could not recover because userspace
  could no longer read storage. No panic, GPU hang or lockup was retained.
- **History:** none of the five earlier retained crash consoles or recent kernel
  journals contained this PHY failure. Those consoles hold eight complete native
  resumes, all with a working PHY, and three native sleeps earlier in the same
  boot also resumed normally. The kernel already includes upstream's SM8650
  resume handling (power off an already-powered PHY before reinitialization).
- **Comparison with stock:** each UFS retry powers the PHY off and restarts
  calibration, so one attempt never had more than mainline's 10 ms to reach
  `PCS_READY`. The 15 retries did not add up to one longer wait. Xiaomi's vendor
  PHY driver (`phy-qcom-ufs-qmp-v4-pineapple`) allows 1 s per attempt. Stock's
  `dtbo` also votes three rails for the PHY: `vdda-phy` (L1D), `vdda-pll` (L3I)
  and `vdda-qref` (L1I, 64.5 mA). L1I feeds QREF, which supplies the PHY
  reference clock. Mainline has no QREF vote, so here L1I stayed on only while
  PCIe, USB or DSI held it. At this resume PCIe had already linked up, so L1I was
  on. The missing QREF vote is therefore a latent gap rather than a proven cause.
- **Change:** patch 0032 gives each SM8650 calibration attempt the vendor's 1 s,
  requests `vdda-qref`, and the board DT points it at L1I. A slower lock than the
  old 10 ms budget is logged with its duration. A timeout now logs the PCS and
  common-block ready/status registers, so a recurrence can be told apart from a
  PLL that never locks.
- **Result:** installed as kernel-only update `7.2.6-armada-houji-kufs1`, by
  writing `boot_b` and `vendor_boot_b` from Linux with read-back verification.
  Rollback images, hash-identical to the previous slot contents, are kept on the
  host. After boot, `regulator_summary` lists `1d80000.phy-vdda-qref` as an L1I
  consumer. The usual 85 modules loaded, and Wi-Fi, audio and the CDSP worked.
  On battery, 10 RTC-timed native sleeps through the normal `systemctl suspend`
  path produced 16 kernel suspend/resume cycles (including dark wakes) with no
  failures. No PHY init exceeded 10 ms, and there were no timeouts or I/O
  errors. The original failure was rare (none in eight retained resumes), so
  this test shows the change is safe. It does not prove the cause is gone. A
  recurrence would now log its lock time or status registers.
- **Recovery:** after the warm reset, ext4 replayed its journal on the userdata
  filesystem, deleted two orphan inodes and reported a clean state. Raw evidence
  is retained root-only on the handset.

### Keep light sleep for the entire charger attachment (2026-10-10)

- **Request:** keep light sleep while plugged in, even at full charge, and
  retain native sleep on battery after the three charger/X2s-associated resets.
- **Change:** `--needs-light-sleep` now checks the wired and wireless supply
  `online` properties. Battery status/capacity, PD type, authentication and
  charging-daemon state no longer release the light-sleep guard. Missing or
  invalid telemetry from a required/present supply, query errors and timeouts
  keep light sleep; a missing optional wireless supply does not prevent normal
  battery sleep. The query emits an explicit `offline` result so an interpreter
  failure cannot be mistaken for an unplugged charger.
- **Detach handling:** three consecutive offline checks, about six seconds,
  are required before ending charging light sleep. An online or unknown result
  resets that count. The wrapper rechecks after thaw/input cleanup in case the
  charger reconnected. A normal Power wake still ends the sleep lifecycle.
- **Verification:** 25 charging-policy tests, 12 charging-sleep tests, 40 native
  sleep tests and the light-sleep shell checks passed. They cover full/held
  batteries, non-PD supplies, absent authentication, wireless power, telemetry
  and query failures, short offline reports, reconnect during cleanup, Power
  wake, and return to native sleep after confirmed detach. The two helpers were
  installed with backups while awake. Live read-only checks selected configured
  `s2idle` unplugged, then light sleep with USB online and the Type-C sink role
  while charging through the X2s at 92%. No live full-charge, sleep or overnight
  stability test was performed for this change; the reset root cause remains
  unresolved.

### Warm reset on a Power-key wake with the original memory layout (2026-10-15)

- **Report:** A short Power press brought up the boot logo, without a long
  Power hold. The phone was running again around 20:37 (all times UTC+8).
- **Retained sequence:** Charging light sleep ended normally at 20:21:12.
  Another Power press was recorded at 20:21:29 and native sleep started at
  20:21:37. Sensor services, including gyro aiming, stopped successfully.
  The journal records six sensor-DSP dark wakes through 20:23:46; the retained
  console contains later suspend/resume cycles and audit activity at 20:25:14.
  Its final cycle ends after `simple_pm_bus_suspend returned 0`, with every
  recorded device callback completed and no following resume callback.
- **Reset mechanism:** Read-only inspection of the PMIC PON ring, decoded
  against the stock `pmic-pon-log.ko`, shows PS_HOLD, WARM_RESET, count 1, then
  ON. The preceding Power-only recovery has a distinct KPD_PWR stage-2 /
  HARD_RESET sequence. This distinguishes the latest event from a forced
  long-button reset; it does not identify the software or firmware initiator.
- **What this rules out:** The restored original memory layout was active.
  Steam removed the Rockstar game session from its running list at 18:35:17;
  the 20:20 sample had about 7.3 GiB available RAM. No panic, OOM kill, GPU
  fault or UFS calibration/I/O error appears in the retained final sequence.
  Thus neither the reclaimed VM carve-outs nor an actively running GTA session
  is required for this failure signature. The earlier 11:53 reboot remains of
  unknown origin, and the 12:35 hang lost its RAM console on forced recovery.
- **Next diagnostic:** Existing suspend-to-idle entry/exit messages are
  KERN_DEBUG, hidden from the ramoops console at `kernel.printk=7`. Raise only
  the current boot's console threshold to 8 (PM debug already enabled) so the
  next naturally occurring failure can distinguish the end of device suspend
  from entry into / return from CPU idle. Previous values are saved with the
  private evidence. No new suspend trial, kernel flash or sleep-policy change
  was made; this is instrumentation, not a fix. Exact reset timing and the
  failing low-power stage remain unresolved.

### Charger-detach handoff and another warm reset (2026-10-17)

- **Report:** A short Power press after unplugging the charger brought up the
  boot logo. GTA was left running. The original memory layout was active; all
  times below are Taiwan time (UTC+8).
- **Retained sequence:** Steam recorded a successful resume at 23:52:43 on
  October 16, another suspend at 23:53:04, and a duplicate suspend request at
  00:23:37 on October 17 while the first was still in progress. Console audit
  anchors place panel initialization at about 00:23:37.5 and native-sleep
  entry at 00:23:37.8. The final device callback returned successfully at about
  00:23:38.2, with no recorded resume. This fits light-sleep cleanup followed
  by native sleep after charger detach. The previous journal ended at 23:35;
  its timestamp and the archive filename do not date the final console event.
- **Reset evidence:** The PMIC ring again records PS_HOLD, WARM_RESET, count 1,
  then ON, with no saved panic. The console threshold had reverted to 7 after
  reboot, filtering the CPU-idle debug markers. The handset's existing diagnostic
  sysctl override now sets threshold 8 across reboots; the previous setting was
  backed up. These records still do not identify the reset initiator or prove
  the exact point of failure after device suspend.
- **Handoff race:** After confirmed detach, the charging wrapper requested
  light-sleep cleanup and treated its successful exit as permission to enter
  native sleep. A concurrent Power press could be lost in that interval. The
  wrapper now carries a PMIC Power-IRQ baseline through cleanup, charger
  rechecks and exec. The native helper checks it before freezing and again
  after pre hooks, immediately before kernel sleep. Detected Power activity
  ends the sleep transaction and runs any required thaw/cleanup. A quiet
  detach still permits battery native sleep. This guards the userspace
  handoff; it does not establish that the race caused this warm reset or fix
  the underlying native-sleep failure.
- **Verification:** 16 charging-sleep tests, 45 native-sleep tests, 25 charging
  policy tests and the light-sleep shell checks passed. Coverage includes a
  key during cleanup, pre hooks or charger rechecks, a reconnect, missing key
  telemetry, and a quiet detach. Helpers were installed with backups while
  awake. Both live IRQ readers agreed and recognized a deliberately mismatched
  baseline; no live sleep or reboot was invoked for verification.
- **GPU evidence:** The preserved run has no final stop record or loss counters
  because it was interrupted by the reset. Its last checkpoint has no pending
  allocation, reclaim, lock, retirement or fence waits. One older candidate
  already had its software fence signaled, and its raw events had rotated out;
  it does not prove a deadlock. These partial records cannot exclude a later
  GPU problem. The collector's six-hour limit uses monotonic time, which pauses
  in native suspend, explaining why the 14:30 run had not expired by wall-clock
  time. Raw console, PMIC and trace evidence remains in private build storage.

### Repeated daytime resets reach the CPU-idle phase (2026-10-17)

- **Report:** Multiple restarts while asleep or waking. The owner also used
  both forced Power recovery and a deliberate menu reboot, so not every boot
  transition is an unexplained crash.
- **Timeline:** Five subsequent boot starts are estimated at about 07:09,
  08:14, 09:09, 11:28 and 14:42 (Taiwan UTC+8), using the PMIC RTC-relative
  boot records and the current synchronized wall/boottime anchor. These are
  boot-start estimates, not exact failure times. An orderly shutdown precedes
  the fourth; the third has no surviving crash console. The first two and last
  have retained native-sleep consoles. Large clock rollback on boot made later
  records appear dated October 16; chrony finally corrected the current boot
  forward by 75,574 seconds at 21:54:54 on October 17. Directory names alone
  cannot establish chronology.
- **Narrower endpoint:** All three new crash consoles finish at
  `PM: suspend-to-idle`, after aggregate noirq device suspend completes, with
  no matching `resume from suspend-to-idle`. The persistent level-8 setting
  exposed the marker previously filtered out. In matching kernel source this
  is the beginning of `s2idle_loop()`, before its wake checks and CPU-idle
  entry; it does not prove that the final PSCI firmware call was reached.
  The first two final idle entries date to approximately 07:08:21 and 08:12:54;
  the last is about 14:41:08 after RTC-relative correction. No saved panic or
  UFS PHY-calibration error explains these endings. The latest PMIC sequence
  is PS_HOLD, WARM_RESET, count 1, then ON; earlier hard resets also remain in
  the ring and cannot all be assigned to incidents by its untimestamped data.
- **GPU evidence:** All interrupted continuous-trace checkpoints have no
  pending allocation/reclaim/retirement/lock/fence maps and no candidates. The
  last failed boot's retained window has 101,597 retirement entries and returns
  each, and 1,212 contended lock entries and returns each. Crash-time loss
  counters are unavailable, so this does not exclude all GPU failure modes.
  Its console separately records 128 UCHE translation-fault messages plus
  suppressed callbacks, beginning after a successful wake and ending about
  22 seconds before the final native-sleep entry. Earlier two crashes have no
  such fault in their retained windows. Steam and trace task labels show Euro
  Truck Simulator 2 in the affected boot; they do not identify which address
  space owned the faulting TTBR. The translation faults are actionable
  GPU evidence, but not proof of the previously suspected lock cycle or a
  shared reset cause.
- **Evidence loss:** The 128 MiB persistent journal had retained only part of
  the current boot; device-callback debug logging produced roughly a thousand
  lines per suspend cycle, and this boot had completed 356 cycles. Captures
  were preserved before changing diagnostics. The handset now retains
  aggregate PM/idle debug markers without the per-device flood, with a bounded
  512 MiB journal and an independent fsynced sleep ledger carrying boot ID,
  RTC/monotonic clocks, charger/battery telemetry and attempt outcomes.
- **Next-stage observer:** A diagnostic-only module now records existing
  s2idle PSCI entry/return tracepoints and Linux restart-function entry into
  the crash console. It leaves normal awake idle silent and caps detail at
  256 records per sleep attempt. Matching running/local ABI layouts were
  verified; load, unload and re-load passed while awake. It is boot-enabled
  alongside continuous GPU tracing. The 45 native-sleep tests and three
  ledger tests passed; no sleep/reboot was forced to validate the hooks.
  See [sleep diagnostics](diagnostics/SLEEP.md) for limitations and rollback.
  The reset cause remains unresolved; native battery sleep stays enabled.

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

### Desktop rotation stopped after sleep, then was off (2026-10-22)

- **Report:** in the KDE desktop, rotation ("gyro") was unresponsive and
  sometimes wrong after a day of use.
- **Cause 1, every resume:** `61-houji-sensors` stops `iio-sensor-proxy` before
  sleep and starts it after. The proxy owns its bus name before it opens the
  sensors, and opening the SSC accelerometer waits on the DSP while D-Bus calls
  are still dispatched. KWin claims the accelerometer the moment the proxy
  reappears, so the claim landed while the sensor was still opening. It was
  recorded but the sensor was never started (the hotplug path handles this; the
  startup path did not). With a debug build, KWin claimed again after the hook
  ran, and 0 samples followed, against 182 in 6 s before. Rotation stayed frozen
  at the pre-sleep angle, which is wrong once the phone has turned. Game Mode was
  unaffected: its orientation helper starts after the proxy.
- **Fix:** `sensors/iio-sensor-proxy-houji.patch` (replaces
  `iio-ssc-stop-on-close.patch` and keeps that change) makes a claim wait for
  the opened device and starts sensors that already have clients once startup
  opens them. It applies to the pinned 3.9 revision; the unpatched source
  rebuilt to the phone's exact binary, so the installed binary differs only by
  this change. On the phone, after the real sleep hook restarted the proxy, KWin's
  claim produced 183 samples in 6 s; Game Mode still applied its orientation.
- **Cause 2, at the time of testing:** KWin's saved output config
  (`~/.config/kwinoutputconfig.json`) had `autoRotation: Never`, so the desktop did
  not use the sensor at all. KWin's sensor logs show auto-rotate active in
  yesterday's desktop sessions, so it changed later; what wrote it is not
  established (Display settings also crashed once, at 13:44). It was set back to
  `Always` with a backup. Houji's first-session helper only sets it once, so a later
  change by the user stays.
- **Not verified:** physical rotation by hand; the phone lay flat during the
  tests, so orientation read `undefined` throughout.

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

### Steam automatic brightness: adaptive bounds, not a fixed lux curve (2026-10-18)

- **Request:** Research how Steam implements automatic brightness.
- **Implementation:** Inspected the ARM64 binary actually mapped by the running
  client (`steamrtarm64/steamclient.so`) and its live settings callback. Steam
  reads the virtual IIO `als` device, takes the higher of up to two sensors,
  filters the latest two seconds through a median, and derives adaptive minimum
  and maximum brightness. The user's selected brightness is adjusted only when
  outside those bounds. At 100 lux and above the normal allowed range is about
  15–100% raw backlight; a medium setting need not brighten under a flashlight.
  The UI slider is nonlinear and is not the raw backlight fraction.
- **Output:** The loaded client discovers `/sys/class/backlight`, reads the
  maximum, combines adaptive changes with manual preference/idle dimming/resume
  fades, then writes an integer to `brightness`. It first tries a user write,
  with SteamOS privileged helper fallbacks. This is separate from SensorProxy.
- **Read-only check:** Steam reported adaptive available/enabled, about 683 lux,
  min/max 0.15/1.0 and raw backlight 646; the panel agreed at 646/2047. No forced
  sensor injection or slider movement was used. These readings explain why the
  earlier flashlight observation alone was insufficient to call the path broken.
  Details, thresholds, binary identity and addresses are in
  [Steam brightness](sensors/als/STEAM-BRIGHTNESS.md).

### Gyro aiming through the controller's virtual Steam Deck (2026-10-15)

- **Issue:** Steam gyro aiming needs motion on the same controller as the
  sticks. The phone's gyroscope is only reachable through the sensor DSP, and
  Steam sees the GameSir controllers as InputPlumber's virtual Steam Deck.
- **Cause:**
  - libssc already has a gyroscope class, but opens every sensor at the first
    rate the DSP advertises: about 25 Hz.
  - InputPlumber 0.79 skips virtual evdev and hidraw devices (hidraw only
    passes when it matches a connected Bluetooth device) and has no evdev
    motion source. Its D-Bus `SendEvent` panics a worker thread
    (`blocking_send` inside the runtime). It does poll IIO IMUs, choosing its
    BMI driver by device name (`bmi*-imu`), and needs a `/dev/iio:deviceN`.
  - An IMU source listed in a controller config creates that controller's
    composite device, and a virtual Steam Deck, even with no controller.
- **Fix:**
  - `houji-imu`, an out-of-tree module, registers an IIO device named
    `bmi-houji-imu` with accelerometer and gyroscope channels. A write-only
    `sample` attribute takes all six axes at once; an empty event group gives
    it a `/dev` node.
  - `houji-gyro` streams the SSC gyroscope at 200 Hz and the accelerometer at
    50 Hz (re-enabled with an `SscEnableConfigRequest` through the port's
    `ssc_sensor_send`), rotates them from Android's device frame into the
    current screen orientation, and writes values that reach the Deck target
    in the Deck's own units (16384 per g, 16.384 per °/s; from SDL's Steam Deck
    driver). It loads the module only while gyro aiming is on, a controller
    whose config lists the IMU is connected with a gamepad target, and the
    phone is not in light sleep; otherwise it closes the sensors and unloads
    the module. The sleep hook stops it before native sleep.
  - The X2s and X5 Lite configs list the IMU. Houji Settings → Controls has the
    toggle (off by default).
- **Result:**
  - The module created `/dev/iio:device2`; with no controller connected the
    toggle waited and no virtual controller appeared.
  - With the X5 Lite connected, its composite device took both sources. The
    virtual Deck's reports carried gravity along the screen's up axis in the
    `left` orientation, and the owner aimed in a game with Steam's Gyro
    Behavior.
  - Cost while streaming: about 3.6 % of one core for `houji-gyro`, and
    InputPlumber rises from 0.8 % to 6.6 % (it polls the device every 2.5 ms).
    Off: nothing runs.

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

### Use stock Android's one battery-percentage source (2026-10-18)

- **Request:** Use one percentage consistently before and after plugging in,
  after checking what stock Android uses.
- **Stock evidence:** The pinned firmware's `android.hardware.health-service.qti`
  configures `/sys/class/power_supply/battery/capacity` as its battery-level
  source. `BatteryMonitor::getProperty` reads it directly for capacity. Stock
  `qti_battery_charger.ko` maps capacity to GLINK wire ID 4 and divides that
  firmware value by 100, independent of charging status. Raw gauge SOC and the
  charge counter are separate properties, not the normal displayed percentage.
  See [stock battery reporting](charging/STOCK-BATTERY.md).
- **Change:** Removed patch `0028` and the obsolete clamp-specific test. The
  port now reports the firmware capacity in every state, without a remembered
  discharge minimum, raw-counter substitution, charging-only +3 margin or a
  full-status source switch. This supersedes the two clamp fixes above.
  Firmware smoothing can still lag or re-estimate; this change removes the
  port's formula switch, not all firmware-originated percentage changes.
- **Verification:** Replaying the pre-change patches and rebuilding reproduced
  the installed battery module's `.text` byte-for-byte. Rebuilt with only the
  capacity patch omitted; all 196 remaining kernel patches apply without fuzz,
  the module builds, 25 charging-policy tests pass, and the new capacity getter
  returns firmware percent directly. The battery module was replaced and loaded
  while awake at 00:19 UTC+8 with a verified backup, keeping the same kernel and
  boot. Linux and UPower both reported Full / 100%. The on-disk module is updated
  for subsequent boots; sleep/GPU diagnostics remain enabled. No physical cable
  transition or long-discharge accuracy test was induced during deployment.

### The main percentage disagreed with the gauge after heavy gaming (2026-10-18)

- **Issue:** After a long GTA V session unplugged, the phone read 0% (3.04 V,
  charge counter 0). With the charger attached and the game closed it read 60%,
  settled at 56%, and then rose, while the raw gauge (Xiaomi property 195) and
  the charge counter were near 29% and rising normally.
- **What was established:** A kprobe trace of GLINK replies showed firmware
  returning `5600` for property 4 with result 0, so Linux and Steam were
  reporting what firmware said. Stock's driver divides property 4 by 100 as we
  do, and has no fallback that explains a non-zero reply. The stock ADSP image
  contains an SOC-smoothing state machine (charger attach, charging-state
  transitions, raw versus system SOC, SOC saved at charger removal); which branch
  ran here is unknown. Continued charging brought the main figure within about 2
  points of the gauge (86% against 84%) without any reset or driver change.
- **Request:** "Implement how Android represents the value." Android already
  shows property 4, so what stock does differently is traffic: it re-reads
  capacity and Xiaomi property 116 every 5–20 s, and it forwards the panel's
  blank state to firmware as Xiaomi property 117.
- **Change:** Kernel patch `0034` exposes `thermal_temp` (116) and
  `fb_blank_state` (117). `houji-charge-policy` keeps 117 equal to the backlight
  state, reads it back at every poll, and polls capacity and 116 at stock's
  interval. Polarity comes from the BTF in stock `qti_amoled_ecm.ko`
  (`BLANK=1`, `UNBLANK=2`): 1 means blank. USB moisture detection, which stock
  couples to the same flag, is not ported. See
  [stock battery reporting](charging/STOCK-BATTERY.md).
- **Verification:** All 224 kernel patches apply without fuzz; a rebuilt module's
  disassembly differs from the installed one only in `.rodata` offsets and the two
  new attributes. Helper tests: 1 new, 8 policy-native, 10 native, 25 charging
  policy. Installed live at 00:19–00:21 UTC+8 on the running kernel, with the
  charger connected at 100% and rollback copies kept. A GLINK trace showed the
  expected reads, a forced wrong value was corrected at the next poll, and
  blanking the panel flipped property 117 within a second.
- **Status:** **Not shown to fix the disagreement.** The battery was full and
  charging when installed. The next discharge under load, followed by a plug-in
  on a low battery, is the test that matters. If the main figure still diverges,
  the cause is inside the ADSP state machine and needs firmware-side evidence.

## Memory

### Native resident helpers and parked Decky plugin (2026-10-18)

- **Request:** Reduce resident RAM, including the inactive FSR4 NPU Decky
  process, and replace simple Houji helpers with native code.
- **Plugin:** The upscaler toggle was off but Decky still loaded its FEX/Python
  backend at 82.1 MiB RSS / 53.4 MiB PSS. Moved its directory into
  `~/homebrew/disabled-plugins/fsr4-npu` and restarted Decky. The process is gone;
  the three other plugins remain, and the FSR sources/settings/models are kept.
- **Native conversions:** Steam's UPower/vpower bridge, Gamescope orientation
  bridge and X2s session routing are now C++17. The battery protocol keeps atomic
  publication, readiness, stale-estimate clearing and the `-1` shutdown sentinel.
  Orientation keeps sensor claims/releases, lock semantics, debouncing and
  compositor/socket identity. Controller routing preserves custom config,
  Plasma precedence and profile publication before InputPlumber restart.
  Build/staging and unit-test dependencies now include the native helpers.
- **Live deployment:** Installed with a rollback copy at 13:18 UTC+8. All three
  units run the ARM64 binaries; UPower and vpower agree, the owner's rotation
  lock remains selected, and Game Mode routing applies. The sleep guard and
  diagnostics remain active. All 38 Houji test scripts pass, including nine
  private-D-Bus/native integration tests.
- **Memory:** Same-boot PSS for the three helpers fell from 48.6 MiB to 6.1–6.5 MiB
  (42–43 MiB saved). The measured Decky process group fell by about 60 MiB after its
  restart, including removal of FSR4; the combined observed reduction was
  102.5 MiB PSS in the final sample. Some Decky delta is restart-related, and shared pages mean RSS
  cannot be summed as unique physical savings. Locked orientation was measured;
  sensor-following mode establishes additional D-Bus state. Private snapshots
  are retained outside the repository.

### Native gyro, charging, thermal and modem-license relay (2026-10-18)

- **Request:** Extend the RAM optimization to the remaining resident Houji
  helpers. Added C++ implementations for gyro aiming, charging policy, thermal
  policy and QTEELS relay; Python sources remain as reference models and rollback.
- **Gyro:** Direct libssc callbacks preserve 200 Hz gyro / 50 Hz acceleration,
  the Deck unit conversion and display rotation, sensor-object lifetime,
  connected-controller/gamepad gating, and cleanup on light/native sleep.
  Controller disconnect entered waiting mode; reconnect resumed live samples.
- **Charging/thermal:** Differential tests cover thousands of thermal thresholds,
  hysteresis trajectories, battery limits, authentication flags, history resets,
  ramp and SIC states. Temporary-sysfs tests exercise missing telemetry, the
  charge-limit hold/resume, stop fallback and stronger kernel GPU cooling caps.
  The deployed charging process repeated the three adapter-auth attempts and
  ramped back from 500 mA to the configured 3 A standard-PD limit. Kernel lease
  and authentication checks remain independent of the native process.
- **License:** Fake-QTEE/datagram tests verify exact ACK/indication bytes,
  malformed/duplicate/truncated-field rejection, busy retry and output bounds.
  A temporary systemd direct auto-restart was first tested with dummy units,
  then used for the relay handover. Modem storage, modem-start and QTEE services
  retained their PIDs/invocations. Six real secure checks succeeded immediately
  after the native relay registered; the transient restart override was removed.
- **NFC:** Retained the existing protocol implementation and added idle exit
  when fully off; active reader/emulation/iPASS, recovery and in-flight work
  prevent exit. D-Bus actions reactivate it, while passive status polls do not.
  The installed update restored iPASS with byte-identical saved settings.
  No memory reduction is claimed while iPASS remains active.
- **Measured result:** Same-boot PSS fell from 22.6 to 7.4 MiB for active gyro,
  8.8 to 0.5 MiB for charging, 8.8 to 0.5 MiB for thermal and 7.0 to 0.4 MiB for
  the license relay: about 38 MiB saved. Ten-second CPU samples were 1.57% to
  0.69% for charging and 0.88% to 0.39% for thermal. Active gyro was 10.01% to
  9.65%; that does not establish a meaningful CPU reduction. All 41 Houji test
  scripts passed, including the native protocol/model/integration suites.

### MangoHud-only display after a blocked sleep request (2026-10-18)

- **Report:** Black Steam UI with only MangoHud after pressing Power. At
  13:54:30 (Taiwan UTC+8), Steam logged `Issuing Suspend Command`, but the
  system journal showed no suspend/resume transaction. A temporary development
  `sleep:idle` block inhibitor was still held by the native-helper validation.
- **Live state:** The boot ID was unchanged, both user/app slices were thawed,
  the display compositor and Steam processes were alive, and the sampled GPU
  stacks showed no fence/reclaim stall. Steam's own screenshot was black and
  its `SuspendResumeStore.suspending` flag remained true.
- **Recovery:** Removed the temporary inhibitor and invoked the running Steam
  UI's existing resume handler once. Its suspending flag cleared; the resume
  animation and then the library rendered again without rebooting or restarting
  Steam/Gamescope. This was an inhibited pre-sleep UI transition, not evidence
  of another kernel resume reset. The private UI progress enum stayed at 1 and
  is not used as proof of system sleep state.
- **Development rule:** Do not leave a sleep-blocking inhibitor around general
  UI validation. Restrict one to an actual short install transaction and remove
  it on success/failure; Power-button tests must first verify it is gone.

### MangoHud-only display after a late Steam resume notification (2026-10-23)

- **Report:** After a long sleep, Steam showed only the MangoHud overlay before the
  UI appeared. Times are UTC+8. The kernel resumed at 15:21:55, Steam logged
  `Resume complete` at 15:21:56 and Gamescope woke the display at 15:21:57. The UI
  logged `Received resume request` only at 15:22:28, 33 s after the resume. Until
  then its `SuspendResumeStore.suspending` flag stays true, and the UI draws a black
  screen. By the time it was inspected the flag had cleared on its own.
- **Not the 2026-10-18 cause:** No sleep inhibitor was held and the display wake worked.
  The Steam client stalled for the same 33 s: its background updater logged a manifest
  request at 15:21:56 but sent it at 15:22:29, and the client's update check timed
  out after 30 s. Wi-Fi was back at 15:21:58. The USB host controller registered
  again at 15:22:09 (only this incident's journal covers that, so it is not a pattern).
  The boot ended in a reset at 16:18, so the swap counters read afterwards belong
  to the next boot; page-in stalls are neither confirmed nor excluded.
- **How often:** Of 391 resumes in the phone's Steam logs from 2026-10-04 to 2026-10-23,
  371 reached the UI within 5 s (361 within 1 s) and 20 took 11 to 113 s; today's is
  the only one since 2026-10-17. Three of the four update-check timeouts in the
  archived logs had a late UI resume, but most late resumes had no timeout, so the
  update check is not the only cause. The earlier ones were not analysed.
- **Fix:** `houji-steam-resume` runs after every suspend (drop-in
  `40-houji-steam-resume.conf`). After 6 s (the slowest normal resume was 5 s, the
  fastest late one 11 s) and a 1 s recheck, if the UI is still suspending and no
  new suspend has begun, it calls `SuspendResumeStore.InitiateResume()`, the same
  handler the notification runs and the call that recovered 2026-10-18. It reaches
  Steam's own DevTools port on loopback with a built-in WebSocket client; upstream's
  `launch-steam` enables that port on every start. It then saves the state of every
  Steam, Gamescope and InputPlumber thread (wait channel, syscall, kernel stack),
  USB, network and Steam's logs to `/var/lib/armada/resume-stall/` (mode 0700, newest
  10), again 8 s later. Read `threads-stuck.txt` after the next occurrence to see what
  the client's main thread was blocked on.
- **Measured:** With every thread of the Steam client stopped (`SIGSTOP`), the UI left
  the black state 7.8 s after the stuck state began and showed Steam's resume
  spinner; the library returned 4 s after the client was continued. So a dead black
  screen becomes the normal spinner after about 7 s, but the client's own delay
  is not removed. A real suspend started the way Steam starts one (RTC wake after
  35 s) ran the helper, which found the UI resumed and did nothing. Not tested:
  an actual stall since installing, "lock on wake" enabled, a game suspended
  during sleep. When the late notification finally arrives the handler runs a
  second time, so the resume animation can show twice.
- **Tests:** `tests/houji-steam-resume-test.py` (15): acts only on a stuck UI, never
  during a new suspend, grace between the normal and late resumes, DevTools client
  (ping, fragmented reply, unreachable port), evidence bundle contents and
  permissions, packaging. Evidence: `build/houji-resume-mango-20261023/`.

### Android apps started nothing (2026-10-25)

- **Report:** A Firefox APK imported with Armada Store's "Add local .apk" did not launch. The store's own
  Android category lists also do not load; that was not looked at. Times are UTC+8.
- **Findings, in the order each hid the next.** Lepton (Steam app 3029110) was not installed; the first
  launch asked Steam to install it (as designed), and it finished at 17:29. Lepton's own log is
  `~/.local/share/lepton/steamvr/logs/lepton-steamlaunch-<app id>.log`, not Steam's.
  1. **Empty launcher activity.** The log ends `Early-exit due to malformed .apk metadata`. Lepton's
     `apk-info-extractor` reads only `<activity>` elements. Firefox's only enabled launcher is the
     `<activity-alias> org.mozilla.firefox.App` (24 other alias icons are disabled). `armada-apk-launcher`
     reads the same binary manifest, aliases included, and Lepton's wrapper calls it only when Valve's
     extractor returns nothing. Checked on the Firefox manifest against its decoded tree (and by test
     manifests built in the test). The same gap hides any app that launches through an alias.
  2. **Overlay on an overlay.** podman failed with the kernel message `overlay: filesystem on …/upper not
     supported as upperdir`. `/var/home` is itself an overlay (lower `/run/houji/lower/var/home`, upper on
     the ext4 userdata partition), and the container's root and each app's data are overlays whose layers
     podman keeps there (`~/.local/share/containers`, `steamapps/compatdata/<id>/baked`). Moving podman's
     storage to ext4 fixed only the first; Lepton deletes `baked` at every launch, so a symlink there does
     not survive. `fuse-overlayfs`, which the image already ships, needs no relocation.
  3. **Android's network setup looped.** With the container up, `adb` stayed `offline` and nothing was
     installed: Android's ethernet service failed 7,731 times in three minutes on `disabling IPv6 failed`,
     and `eth0` lost its address, so adb had nowhere to connect. Inside the container `/proc/sys` was
     read-only (podman's default). Adding `--security-opt unmask=/proc/sys` to Lepton's `podman run`, in the
     wrapper that already rewrites its arguments, let the write succeed. In a rootless container only the
     container's own network settings become writable: `kernel.printk`, `vm.swappiness` and `kernel.sysrq`
     were still refused.
- **Delivering the storage setting.** podman reads one `storage.conf` and none merge. The `/etc` one is
  ignored for rootless users, a drop-in directory is not read, and `CONTAINERS_STORAGE_CONF` fails because
  Lepton runs `env -i podman exec`. So each user needs their own `~/.config/containers/storage.conf`.
  `houji-podman-storage.service` (a user oneshot, enabled by the image) installs it only when missing, so an
  owner's own file is kept. The file is generated when the image is staged from the image's own
  `usr/share/containers/storage.conf` (`rootless_podman_storage_conf`): the same file with the root-only
  `graphroot` and `runroot` left out and `mount_program` added, verified by parsing so nothing else may
  differ. A `systemd-tmpfiles` copy was tried first and rejected: it creates the file but exits with an error
  (it cannot take root's ownership), which would leave the user manager degraded.
- **Staging.** `stage-root.py` copies `packages/lepton/files/` (the wrapper, its library and the new helper) over
  lepton-armada in the image until the pinned Armada image ships them, the way it already does for
  `armada-game-launch`, so no divergent copy exists. `lepton-guestos.spec` installs the helper and requires python3.
- **Result.** Firefox 158.0.2 (a 630 MB universal APK) installed, started through the alias and reported
  `Displayed org.mozilla.firefox/.App: +759ms`; the page rendered on the phone. After restarting Game Mode with
  none of the experimental settings left, a cold relaunch with the baked app data reached the same screen.
  Boot and install took about 50 s the first time.
- **Not verified:** any other Android app; audio, touch and controller input inside the container; speed,
  memory and battery cost of FUSE-based layers against native overlays (Firefox started quickly, nothing was
  measured); a full image build (the staging lines were checked by running the generator on the phone's real
  `storage.conf` and by tests, and the service was applied live, but `stage-root.py` itself was not run);
  why the store's categories do not load.
- **Rollback.** Originals of the two replaced Lepton files: `/var/lib/armada/lepton-armada-before-20261025T175301/`.
  Remove `~/.config/containers/storage.conf`, `/usr/lib/houji/containers-storage.conf`,
  `/usr/lib/systemd/user/houji-podman-storage.service` and its `default.target.wants` link.
- **Tests:** `tests/armada-apk-launcher-test.py` (17), `tests/houji-podman-storage-test.py` (11) and
  `tests/lepton-armada-test.sh` (the helper is copied into the derived tool, Valve's answer wins, an alias is
  used otherwise, every `podman run` carries the unmask). Evidence: `build/houji-firefox-apk-20261025/`.

### A large Android app ended its first launch right after installing (2026-10-25)

- **Report:** Brawl Stars (a Play split set of 1.77 GB, installed by hand from an XAPK and then
  re-downloaded from Armada Store) showed only Android's home screen and never started. Times are UTC+8.
- **How an XAPK got in:** "Add local .apk" accepts only a file ending `.apk` with `AndroidManifest.xml` at the top
  of the zip, so an XAPK is refused. It was installed by hand into the store's own layout
  (`~/Applications/Android/<package>/base.apk`, the splits, `.armada-apks`), recorded with the store's
  functions and given a shortcut the way the front end does. The store's later download replaced it.
- **Cause 1, a timer that outlasts the install.** Valve's `LeptonProcessObserver` (inside Android's system_server)
  gives the target app `lepton.active_app_launch_timeout` seconds, 60 unless `LEPTON_APP_LAUNCH_TIMEOUT` is set,
  counted from Android's boot. If the app has not come to the foreground by then it logs `Target app (…) did not
  start` and creates `/data/lepton-on-app-exit`, which ends the run. A first launch installs the app before it
  can start, and this set took about 100 s: the log shows the message at 11:42:27.000, 60.0 s after
  system_server started, and the install finishing at about 11:43:07. Lepton then printed
  `Waiting for app … to exit` and `Exited!` straight away. Firefox's 630 MB install fits inside 60 s, so it never
  showed this.
- **Fix:** the `setup_props` wrapper now sets `LEPTON_APP_LAUNCH_TIMEOUT` to 60 s plus one second per 8 MB of the
  app's APKs, at most 900 s (`armada_launch_timeout`); a value set by the owner is kept. This game gets 291 s,
  checked as the guest's property. In the next first-install run the 60 s mark passed during the install with
  no `did not start` line.
- **Cause 2, tapping the icon too early (not fixed).** The launcher shows the app's icon as soon as Android has
  registered the package, but Lepton is still moving the installed APKs. A tap at 11:57:21 started the game
  `from uid 10007` (the launcher), it crashed at once on a null `Resources` (`addLoaders`), and the process dying
  ends the run the same way. During a first install the screen is only Android's home screen, for about two
  minutes with no progress shown, and it has to be left alone.
- **Brawl Stars itself:** with the app installed, a launch reaches the game (sign-in screen, then the tutorial with
  the joystick) and about 10 to 20 s later the game ends itself: `Report: Exiting: elsm.g: 01`, then a fatal
  exception raised by the game. The host kernel log showed no GPU fault or out-of-memory kill. The log also says
  Google Play services and the Play Store are missing, and it creates a key named `PromonHardwareBoundIdRsaKey2`:
  the game ships Promon app protection, which ends apps in environments it does not accept. That is the likely
  cause, not a proven one. It was not investigated further: it is the game's anti-tamper protection, and no
  attempt was made to hide the container from it.
- **Not verified:** a first install left completely untouched, from install through to a running game, after the
  timeout change (the timed run was interrupted by the tap above); other large apps.
- **Tests:** `tests/lepton-armada-test.sh` covers the timeout for a split set, a single APK, no APK, the cap and an
  owner's value. Evidence: `build/houji-firefox-apk-20261025/`.

### Native SIM/eSIM settings broker and NFC off-state verification (2026-10-18)

- **Request:** Check the still-resident `houji-cellular/service.py` after the
  license relay conversion, and restart NFC after the owner turned iPASS off.
- **Cellular:** Replaced the settings broker with native C++ while retaining
  its Polkit checks, two-method D-Bus interface, operation lock, SIM/lpac command
  order, sanitized errors and profile-result filtering. Activation codes still
  go through stdin. Cancellation and timeouts restore ModemManager before the
  operation lock is released. The license relay and modem itself are separate.
- **Validation:** Seven private-bus tests cover authorized/denied status and
  mutation, invalid requests, busy/interprocess serialization, stdin/environment
  handling, profile filtering, command failures, timeouts and graceful shutdown.
  ARM64 target-loader validation passed. Live deployment used only Status:
  saved SIM selection and modem/storage/QTEE/license invocations were unchanged.
- **Memory:** At 14:16 UTC+8, settings-broker RSS fell from 35.2 to 10.9 MiB and
  PSS from 22.9 to 4.9 MiB. The new entry point is
  `/usr/libexec/houji-cellular/cellular-control`; the Python reference is retained.
- **NFC:** The owner had turned all NFC modes off. The existing Python service
  had already exited on its idle timer at 14:06:13. A requested restart preserved
  off mode and exited normally again after 30 seconds. It uses no resident RAM
  when off; no native NFC protocol implementation is claimed.

### Steam menu freeze with GPU reclaim and teardown blocked (2026-10-15)

- **Report:** Pressing Steam while GTA V Enhanced was running left the whole
  picture, including MangoHud, frozen. SSH still worked; InputPlumber remained
  responsive, Steam had logged the menu presses, and neither `user.slice` nor
  `app.slice` was frozen by the sleep mechanism.
- **Live evidence:** A Steam webhelper graphics thread was blocked in
  `msm_ioctl_gem_submit` → `msm_gem_pin_vma_locked` → shmem page allocation →
  direct reclaim → `msm_gem_shrinker_scan` → `active_evict` →
  `dma_resv_wait_timeout`. The GPU retirement worker was blocked in
  `msm_submit_retire` at the object reservation-lock acquisition. GTA, Xwayland,
  MangoHud and Gamescope threads subsequently waited on graphics fences during
  buffer or address-space teardown. CPU and major-fault counters were essentially
  stationary during a five-second sample, rather than showing swap-in thrashing.
- **Interpretation:** This is a kernel graphics synchronization/reclaim stall,
  distinct from the later-stage native-sleep reset signature. The reclaim wait
  is bounded per buffer but its context-switch count kept advancing while the
  retirement worker made no progress. The exact lock/fence dependency cycle has
  not been proven; a single sampled wait is not enough to call it an unbounded
  fence wait or identify a fix.
- **Recovery:** Steam's automatic session restart did not clear the blocked
  kernel tasks. Temporarily setting `msm.enable_eviction=N` also did not release
  the already-running reclaim scan, so the setting was restored. With owner
  approval a software reboot recovered the UI. No kernel or persistent memory
  policy was changed; raw evidence is saved privately on the handset and host.

### GPU retirement dependency follow-up (2026-10-15)

- **Source mapping:** The saved `msm_ioctl_gem_submit+0xe50` return address maps
  to buffer pinning. Submission has already acquired its VM/buffer reservation
  locks, which stay held across page allocation and direct reclaim. The saved
  `msm_submit_retire+0x6c` address maps to `msm_gem_lock(obj)` before dropping
  a `drm_gpuvm_bo` reference.
- **Signaling dependency:** `msm_update_fence()` updates the hardware completion
  counter but does not itself notify fence callbacks. The retirement loop calls
  `dma_fence_is_signaled()` on the first submitted job, which signals its hardware
  fence and the scheduler's user fence, then cleans up that job before checking
  the next one. Reservation-lock contention in cleanup therefore delays fence
  notification for later jobs, including jobs in other rings. Recovery work also
  shares the same GPU worker. The hardware hangcheck can see completed counters
  even while user-visible fences remain unsignaled.
- **Reclaim amplification:** `active_evict()` waits ten jiffies per object
  (40 ms at HZ=250) while holding that object's reservation lock. A submitter
  reaching this path retains its previously acquired locks too. The GEM LRU
  scanner's target counts pages freed, not time spent waiting, so scanning many
  active buffers can accumulate long delays. Disabling eviction after a scan
  starts does not change the already-selected stage or interrupt its waits.
- **New capture:** A bounded kprobe trace, with offsets checked against running
  BTF, identified a 42.746 ms retirement wait on a reservation lock whose owner
  was independently observed as Steam's GPU process. Separately, a GTA reclaim
  thread spent 43.386 ms waiting on a Gamescope scheduler fence and timed out.
  These are different events: they do not establish the original lock/fence
  cycle. The five-minute ring buffer overwrote some earlier CPU records, so
  unmatched entries alone are not evidence of a permanent wait.
- **Control capture:** A narrower 20-second capture had no overruns and matched
  11,411 retirement entries/exits; the longest was about 0.142 ms, with no active
  eviction wait recorded. During the reported transient recurrence, the later
  live sample found the GPU worker progressing and Steam's JS responsive; the
  owner confirmed recovery. It was not the earlier persistent blocked state.
- **Ordering experiment:** A host pthread harness extracted the actual
  `retire_submits()` loop and mocked two hardware-complete jobs plus a contested
  cleanup lock. The second fence stayed unchecked until cleanup of the first
  could proceed. A separate-signaler control permitted completion of the second
  fence even when hardware finished it after the first cleanup blocked. This
  verifies the code's head-of-line dependency, not the original handset cycle.
- **Next fix direction:** Separate fence notification from potentially blocking
  buffer/VM cleanup, maintaining references, active-submit accounting and teardown
  ordering. Merely signaling all currently completed jobs once before cleanup
  would still block notifications for jobs completing later. An address-matched
  capture of the persistent stall is still needed to close the causal chain.
  No kernel fix was flashed and all temporary probes were removed.

### Bounded lock-owner/fence tracing for the next stall (2026-10-16)

- **Request:** Capture whether GPU retirement is blocked by a submitter that
  is itself reclaiming memory and waiting for a later GPU fence.
- **Instrumentation:** `diagnostics/gpu-lock-trace.py` uses opt-in tracefs
  probes on the existing `kufs1` kernel, with member offsets read from running
  BTF. It records backing-page allocations, active eviction/purge, reservation
  waits, generic and typed scheduler/MSM fence waits, GPU-worker mutex slowpaths
  and return events. Mutex-owner flag bits are accounted for when fetching the
  owner PID. Captures contain no GPU-buffer contents or user command lines.
- **Correlation:** Candidate chains require matching owner pointer and TID,
  allocation/reclaim/reservation context, and an unreturned retirement lock
  attempt. Hardware-completed sequence comparisons are wrap-aware. Candidate
  status is explicitly not proof: nofault field reads can race ownership and
  signaling, and loss/missed-probe counters must be checked.
- **Retention:** Continuously drain kernel buffers into at most eight 8 MiB
  private chunks and checkpoint in-flight identities. Save automatically when
  retirement stays blocked for two seconds, on a manual SIGUSR1, or at the
  six-hour monotonic-time/8 GiB total-write limit; native suspend pauses the
  time limit. Remove only this collector's probes. The
  service is installed but not enabled at boot. No sleep or GPU policy changes.
- **Checks:** Synthetic tests cover owner/lock correlation, completed versus
  pending hardware fences, stale returns, probe ordering, bounded file rotation,
  BTF offsets and partial-setup cleanup. An eight-second live smoke capture
  recorded 3,331 retirement entry/return pairs with zero per-CPU overruns or
  missed probes and complete cleanup. This validates capture mechanics, not
  reproduction of the persistent stall. Manual SIGUSR1 capture also completed
  with full probe cleanup. The final collector was armed at 00:57 UTC+8; an
  initial sample used about 7% of one core and 53 MiB RSS with zero overruns,
  and all nine sampled contended owners resolved to plausible task IDs.

### Continuous debug tracing enabled (2026-10-17)

- **Request:** Keep tracing enabled indefinitely for debugging.
- **Change:** The handset's GPU trace service is now enabled at boot, uses
  continuous mode without the six-hour or cumulative-write expiry, and
  automatically re-arms five seconds after a stall/manual capture or collector
  failure. Explicitly stopping the service still stops it. Persistent suspend
  debug logging remains enabled. No reboot was needed to apply the change.
- **Retention:** Keep eight continuous runs, each with eight 8 MiB trace chunks
  (about 512 MiB of trace data plus metadata in total), under the separate
  `continuous/` directory. Interrupted/reset runs participate in retention;
  previous one-shot evidence remains outside it. A process lock and systemd
  post-exit cleanup prevent overlapping collectors and clean up owned probes
  after abnormal exits. Fresh images install this opt-in service without
  automatically enabling it.
- **Checks:** Eleven synthetic tests passed, including continuous-limit bypass,
  retention across incomplete runs, and scoped probe cleanup. The installed
  unit passed `systemd-analyze verify`. At 00:58 UTC+8, a live SIGUSR1 capture
  saved successfully and automatically re-armed under a new PID/directory;
  tracing was on, startup enabled, and `RuntimeMaxUSec=infinity`. The saved
  capture had zero overruns, dropped events, missed probes or cleanup errors,
  with 36,694 matched retirement entry/return events. Boot persistence is
  configured; no reboot was induced for this check.

### GTA V Enhanced thrashed: idle memory filled zram (2026-10-12)

- **Report:** GTA V Enhanced stalled while loading; later, with texture quality
  already at its lowest, Social Club still took a lot of RAM.
- **Measurement:** of the 12 GiB part, the firmware's memory map passes Linux
  about 11.0 GB. The rest holds remote-processor firmware (modem, ADSP, CDSP),
  two Gunyah VM carve-outs (`trust-ui-vm`, `oem-vm`) and TrustZone, hypervisor
  and debug ranges. The VM carve-outs were later returned to Linux (below). During the
  stall, zram (zstd, priority 100) held 10.1 GB and used about 3.5 GB of RAM,
  and the 4 GB swap file (priority 10) was empty. In play: GTA itself ~7 GB
  (2.3 GB process, 3.8 GB GPU memory in shared RAM, 1.1 GB swapped). Social Club
  ~4.5 GB, 3.9 GB of it already swapped into zram. The launcher's minimal mode
  (`-minmodeApp=gta5_gen9`) and `-scOfflineOnly` (game `commandline.txt`) left
  Social Club's Chromium unchanged at ~2.2 GB. Ending its processes only
  made the game respawn them, fully resident.
- **Change:** zram writes idle pages back to the data partition. The running
  `kufs1` kernel got a rebuilt `zram.ko` with `CONFIG_ZRAM_WRITEBACK` and
  `CONFIG_ZRAM_TRACK_ENTRY_ACTIME` (same source and config apart from two
  unrelated modules, vermagic set to `kufs1`; original kept at
  `/root/zram.ko.kufs1-orig`). `bringup.config` enables both for future
  kernels. `houji-zram-backing.service` attaches a 12 GiB direct-I/O loop file
  (`/run/houji/data/houji-zram-writeback.img`, ref `houji-zram-writeback`)
  before zram setup, the same service sets it as zram0's `backing_dev` when supported,
  and `houji-zram-writeback.timer` writes pages untouched for 5 minutes every
  2 minutes, within an 8 GiB/day budget. zswap was not used: enabling it needs a
  kernel flash, and writeback gives the same effect for idle pages.
  `MemoryKSM=yes` on `gamescope-session-plus@steam.service` makes the Game Mode
  session mergeable (inherited by Steam, Proton and games); `houji-ksm.conf`
  starts ksmd with zero-page merging at a 100 ms scan interval.
- **Verification:** a 600 MiB patterned buffer, capped to 150 MiB of RAM, went
  to zram; one writeback moved 454 MiB to the loop device (zram RAM 0 MiB),
  and the buffer read back with an identical SHA-256. After a reboot the
  backing device attached before zram setup (301 ms), zram reported it, the timer
  was active and Steam showed `ksm_merge_any: yes`. KSM saved 118 MB with only
  Steam's UI open; at the default 20 ms interval ksmd used ~9% of one core, at
  100 ms ~1%. Gains under GTA V have not been measured yet.

### Writeback tuning and the budget after a reboot (2026-10-13/14)

- **First game session:** with the 5-minute rule, the 8 GiB budget was used up in
  8 hours; 0.8 GiB stayed on disk and 1.1 GiB was read back, and zram regrew to
  2.4 GB of RAM with GTA V running. The rule became 30 minutes and the budget
  16 GiB. A manual 10-minute pass during the next session took zram from about
  2 GB of RAM to 100 MiB (available 0.5 → 2.4 GB) with almost nothing read back.
  The 8 FPS seen then came from thermal throttling (skin 48–50 °C, cpu3/4/7
  paused, cores at 1.0–1.2 GHz), not memory.
- **Bug:** the helper recorded "budget set for today" in a file that survives
  reboots, but zram resets `writeback_limit` to 0 at every boot, so after the
  first reboot of a day nothing was written back. It now carries pages written
  by earlier boots of the day and re-applies the remaining limit on every run.
  A host test covers same-boot growth, a reboot, a new day and exhaustion.

### Both slots stopped booting after `qbootctl -s b` (2026-10-13)

- **What happened:** while testing a device tree that frees the trusted-UI and
  OEM VM carve-outs, `qbootctl -s b` was run to reset slot B's boot counters,
  with B already active. The next boot showed the Mi logo and returned to
  fastboot after about 5 s. Restoring the original `vendor_boot_b`, toggling
  `set_active a`/`set_active b` in fastboot, flashing a checksum-verified Armada
  boot chain to slot A and booting A, and `fastboot boot` of a known-good kernel
  (`Failed to load/authenticate boot image: Device Error`) all failed the same way.
  The device tree was therefore not the cause.
- **Cause (inferred):** `boot_ctl_set_active_slot_for_partitions()` in qbootctl
  takes the type GUIDs from the currently active slot and always writes the
  "active" GUID to `_a` and the "inactive" one to `_b`. With B already active,
  that swaps the type GUIDs of every A/B pair while the attributes still mark B
  active. ABL's own `set_active` swaps the GUIDs again, so the mismatch follows
  whichever slot is made active.
- **Recovery:** Xiaomi's partition tables (OS3.0.303.0) match the handset partition
  for partition. The LUN 4 table holds 28 of the A/B pairs, including boot,
  vendor_boot, ABL and UEFI. After CRC checks, `fastboot flash partition:4
  gpt_both4.bin` followed immediately by `fastboot set_active b` restored a
  consistent table, and the phone booted `kufs1` from slot B. Userdata (LUN 0) was
  not touched. Slot A now holds the 2026-09-26 Armada boot chain instead of stock
  Android's boot images.
- **Rule:** never run `qbootctl -s` for the slot that is already active.
  `houji-boot-success.service` only uses `-m`, which changes attributes, not
  GUIDs.
- **VM memory, retried (2026-10-13):** the same `vendor_boot_b` was written from
  Linux with read-back verification and no slot commands. It booted:
  `MemTotal` rose from 11,113,252 to 11,256,332 kB, `/proc/iomem` shows System
  RAM over `e7830000-fc3fffff`, and the splash buffer stays reserved. The kernel's
  error lines matched the previous boot (16 against 17, same messages), with no
  hypervisor or VM complaints. Filling 6.8 GB of free RAM with a per-page pattern,
  without swap, verified all 1,737,728 pages with no new kernel warnings.

## Thermal limits

### Game Mode CPU/GPU budget rebalanced (2026-10-18)

- **Request:** Optimize thermals after repairing GTA's ARM64EC graphics path.
  The old policy combined normal/desktop CPU caps with a relaxed GPU ceiling;
  the generic Performance profile also fixed the GPU minimum at its maximum.
  Under the measured CPU-bound game load, prime was capped near 1.1 GHz while
  GPU utilization was about 40%. The stock virtual-skin formula was verified
  against the decoded configuration; it was not recalibrated or offset.
- **First comparison, Taiwan UTC+8:** At 17:11 the stationary outdoor baseline
  averaged 32.1 FPS, MangoHud 1% minimum 20.5. Removing the Performance GPU
  floor for five minutes lowered estimated skin from about 45.6 to 44.1°C,
  but the subsequent 60-second sample was only 32.4/20.9. The original CPU
  table still limited throughput. An unrelated debugfs GPU-state read occurred
  during this first trial's settling interval, so it is not a clean energy
  benchmark. Later frame captures avoided that read.
- **Revised curve:** The selected `balanced` Game Mode policy progressively
  caps GPU to 720/680/629 MHz at 39/42/44°C skin while retaining more CPU
  headroom. At 44°C the prime ceiling is 1939.2 MHz, big clusters 1824/1612.8;
  at 45°C, prime 1708.8 and big 1612.8/1497.6. At 46°C and above CPU ceilings
  converge to the previous limits; GPU caps become 578 MHz at 46°C and 500 MHz
  at 48°C. Original low-battery/core-pause rules and independent kernel GPU
  protection remain active. This is a measured Houji tuning curve, not the
  stock Android gaming policy. The stock mgame/hp-mgame profiles also budget
  less GPU clock (720/629 MHz), but use different CPU/framework controls.
- **Restart failure caught and fixed:** The first curve trial stopped on its
  initial sample when a die sensor reached 95°C. The old daemon released all
  CPU cooling votes on exit, so Performance briefly ran every cluster at max
  before its successor installed limits. That run produced no performance
  result and restored the old configuration. Subsequent transitions used
  independent temporary power-profile ceilings. The durable daemon/reference
  fix retains CPU cooling votes on exit/restart; an explicit
  `--exit-caps release` remains available for deliberate service removal.
- **Twelve-minute trial, 17:22–17:34:** Estimated skin averaged 45.10°C and ranged
  44.66–45.33°C, battery averaged 38.60°C. Maximum sampled die was 85.3°C early,
  and 79.5°C in the final three minutes. GPU averaged about 627 MHz; late CPU
  ceilings were 1344/1612.8/1497.6/1708.8 MHz. The 60-second captures at roughly
  three and ten minutes measured 37.0/23.5 and 38.7/26.2 FPS (average/1% minimum).
  Returning to the original policy/floor measured 35.0/21.9; final installed
  balanced policy measured 39.9/25.7 at 17:38:52–17:39:52. The same stationary
  view was used, but time-varying traffic/lighting and sequential testing mean
  these are not a universal percentage gain or evidence of sustained 60 FPS.
- **Tradeoff:** Late net battery discharge was about 5.2 W versus about 4.3 W
  in the GPU-DVFS-only trial. USB supplied only approximately 2.2 W, despite
  the battery status saying Charging; these are net battery readings rather
  than total system power. More CPU throughput costs battery power, and this
  curve's earlier GPU caps may reduce GPU-bound game performance. No claim
  of improved battery life or physical cooling capacity is made.
- **Installed:** Native daemon, matching Python reference, boot service selecting
  `--game-policy balanced`, and Houji factory Performance `gpu_min=0.0`.
  The stager reproduces the board-specific factory change while preserving
  `/etc` owner overrides and manual GPU selection. Private original files and
  install receipt are under `/var/lib/armada/thermal-before-20261018/`.
  At 17:38:49, a live daemon restart sampled 143 frequency sets over three
  seconds without any CPU cap exceeding its pre-restart value. Thermal and
  sleep-guard services remained active; no sleep inhibitor was added.
- **Checks:** Nine policy tests and eight native policy/runtime tests passed,
  including threshold/low-battery trajectories, restart with missing telemetry,
  explicit release, stronger kernel GPU votes and simulated resume resets.
  All 43 Houji scripts passed before the final added runtime case; its containing
  suite passed again afterward. AArch64 cross-build, stager factory/owner
  precedence check and diff checks passed. See [thermal policy](thermal/README.md)
  for the curve and rollback. Hour-long endurance and GPU-bound workloads remain
  unmeasured.

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

### Performance overlay stopped updating during a long Game Mode session (2026-10-11)

- **Report:** no MangoHud appeared over GTA V Enhanced despite Steam's overlay
  level being 4. Game Mode uses the separate `mangoapp` process, so the absence
  of a MangoHud Vulkan layer inside the game was expected.
- **Evidence:** the overlay process was alive but its statistics-reader thread
  waited on a futex. Gamescope's message queue held 188 messages (16,356 bytes),
  and its last read was more than an hour old. The exact blocking operation
  was not captured before recovery.
- **Recovery:** terminating only `mangoapp` let the existing session loop
  restart it. The queue drained and continued receiving/consuming messages;
  a full-composition screenshot confirmed the level-4 HUD over GTA. Steam and
  Gamescope were not restarted. This is a recovery, not a permanent fix for the
  underlying stall.
- **Screenshot detail:** `gamescopectl screenshot` defaults to the base plane
  and omits overlays. The X11 root property
  `GAMESCOPECTRL_DEBUG_REQUEST_SCREENSHOT=3` requests full composition at
  `/tmp/gamescope.png`, which includes the HUD. The installed `gamescopectl`
  forwards only one argument, so adding a third CLI argument does not select
  that screenshot type.

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

### Physical X2s gamepad in Desktop Mode (2026-10-10)

- **Report:** the controller did not work in Plasma; the owner requested its
  original X2s gamepad identity. InputPlumber hid the physical gamepad and exposed
  `deck-uhid`, which was bound to `hid-generic` with a hidraw interface but no
  standard virtual gamepad event device. Steam was not running in that session.
- **Change:** a Houji session watcher publishes an X2s-specific InputPlumber
  override. Plasma ignores the physical gamepad source, exposing its normal
  evdev/joystick interface. It keeps the Consumer Control source intercepted
  with a keyboard-only target so the record button's Power/VolumeDown report
  cannot sleep the desktop. Game Mode restores the existing complete X2s
  profile, including Deck emulation and the Home/record swap. Plasma takes
  precedence over a nested per-game Gamescope, and logout gaps retain the last
  profile. The override also applies to future hotplug.
- **Rediscovery:** this InputPlumber version's CreateCompositeDevice API is a
  stub and its evdev discovery watches devnode creation, not synthetic udev add
  events. The helper therefore restarts InputPlumber once at a session change.
  It does not reset the USB connection or require Steam to run in Plasma.
- **Verified:** five routing tests passed. Live profile switching restored two
  source interfaces and a Deck + keyboard target for Game Mode, then the raw
  gamepad and keyboard-only Consumer Control composite for Plasma. SDL detected
  exactly one `GameSir-X2s Type-C` with a gamecontroller mapping; the physical
  gamepad had `ID_INPUT_JOYSTICK=1` and no InputPlumber hide flags. An exclusive
  grab probe confirmed the Consumer Control interface remained intercepted.
  The phone was left in Plasma with the service enabled. In-game button/axis
  behavior in Heroic still needs the owner's game test.
- **Actual session-switch follow-up:** returning to Game Mode left the desktop
  override active. The watcher matched `/proc/comm` against `gamescope`, but the
  running compositor renames its main thread to `gamescope-wl`. Detection now
  uses `/proc/exe` and the Game Mode `--steam` argument; KWin still takes
  precedence over nested Gamescope. Seven tests cover the renamed thread,
  non-session Gamescope and exited processes. After installation the real Game
  Mode session selected both X2s sources and `deck-uhid`; the owner confirmed
  Home opened Quick Access and record opened Steam again.

### X2s stopped working in a running Proton game after a USB reset (2026-10-10)

- **Report:** Hogwarts Legacy launched through Heroic's Steam shortcut in Game
  Mode ignored the X2s, although Steam navigation still worked.
- **Cause:** about 25 seconds after launch the whole xHCI host controller was
  removed and re-added. Every such reset in that boot coincided with the charger
  dropping to "waiting for USB-PD"; this one followed a thermal charging pause.
  InputPlumber stopped the X2s composite device, Steam destroyed and recreated its
  virtual Xbox 360 pad, and Proton's winebus never opened the replacement. It had
  no `/dev/input` or hidraw descriptors open, while SDL in the game's environment
  could see the new pad. Proton ignores the Steam Deck-style device by design
  (Steam supplies the virtual pad), so the game was left with no controller.
- **Fix:** the X2s InputPlumber profile sets `persist: true`. A brief source
  disconnect now keeps the virtual Steam Deck target, so Steam keeps its virtual
  pad and the running game's handle. The session helper inherits the option in
  both Plasma and Game Mode. Eight routing tests passed; after relaunching, the
  owner confirmed the controller worked in Hogwarts Legacy over a longer session.
- **Trade-off:** after physically detaching the X2s, a disconnected virtual Steam
  Deck controller remains until InputPlumber restarts. The PD-triggered USB reset
  itself is not fixed.

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

### Wired eSE APDUs from Linux (2026-10-09)

- **Request:** exchange APDUs with the NXP embedded secure element under Linux.
- **Transport:** the stock houji wiring exposes the SN220 over I2C, without a
  separate eSE SPI node. Xiaomi's stock vendor diagnostic scripts describe its
  wired HCI path: NCI static connection 1, eSE host `0xc0`, APDU gate `0x30`.
  NFCEE discovery returned `0x80`, `0xc0` and `0x10`. The last advertises an APDU
  interface but serves internal NDEF storage, not the eSE.
- **Bring-up:** CORE_RESET (keep configuration), NCI 2.0 CORE_INIT, NFCEE
  discovery, HCI admin open, host whitelist check, eSE power/link-on and mode
  enable brought host `0xc0` onto the HCI host list. Creating an APDU pipe then
  failed with HCI status `03` (`ANY_E_NOK`); opening stock pipe `0x19` failed
  with `07` (`ANY_E_CMD_NOT_SUPPORTED`). NXP's Android HCI source reads config
  parameters `A023`/`A022` to find firmware-maintained APDU/connectivity pipes.
  They reported `01`/`02` on this phone. Using `0x19` directly, after querying
  its maximum command size (`0x8000`), produced a valid SELECT response.
- **Implementation:** `ese.py` handles NCI credits, HCP fragmentation and
  bounded replies/timeouts. It acknowledges incoming HCI connectivity commands
  and recognizes WTX/ATR events. It leaves firmware-owned pipes intact, releases
  forced link activity after use, retains eSE/eUICC power, and resets NCI with
  configuration kept. `controller.py` holds the supply while unbinding the
  reader driver, then restores the driver and previous reader state in cleanup.
- **API:** the existing polkit-protected NFC service now has
  `TransceiveEse(aay) → aay`, serializing a batch with its other operations.
  `houji-ese` accepts hexadecimal commands on stdin; its default output contains
  only lengths and status words. `--output` creates an owner-only response file.
  APDUs and response contents do not enter command arguments, service messages
  or journal logs. A running tag-emulation session returns busy.
- **Verified:** the installed service/CLI returned 108 bytes and `9000` for
  SELECT of the GlobalPlatform issuer security domain, and two bytes `6A82`
  for an absent private-use test AID, on repeated sessions. A read-only 260-byte
  SELECT with an overlong AID crossed the HCP fragmentation boundary and got
  `6A82`. Stdin input and private response-file output worked. NFC settings
  stayed byte-identical, the reader driver rebound, the temporary supply module
  unloaded, and the eSIM remained registered on LTE. Tests cover both packet
  orderings, fragmentation, errors, bounded waits, and cleanup failures.
- **Scope:** this provides wired transport to installed eSE applications.
  Application-specific keys, access permissions and applet provisioning remain
  outside this change. Incoming long-response fragmentation has a protocol test;
  the tested live responses fit in one packet.

### Off-host eSE RF routing experiment (2026-10-09)

- **Candidate application:** the owner's report names public AID
  `A0000006914D344D56434D0100`. SELECT succeeded and the returned FCI confirmed
  the exact AID. SEMS and CRS also selected. The report's balance and both
  history commands returned `6D00` (instruction not supported), so the presence
  of a personalized iPASS card remains unproven. The report's SELECT length
  was wrong: this AID needs `Lc=0D`, not `0C`.
- **CRS activation:** the read-only CRS GET STATUS command, with P1/P2 `40/00`
  and filters `4F,9F70`, returned `3F00`. On the owner's approval, a temporary
  SET STATUS activation returned `9000` and read-back `3F01`; deactivation
  returned `9000` and `3F00`, also verified in a fresh wired session.
- **RF experiment (phone only):** a temporary `houji-ese-listen-test.service`
  stops NFC Manager and takes the controller with the same shared-supply hold.
  It saves the original CRS activation state root-only, activates the candidate,
  installs exact-AID, ISO-DEP protocol and NFC-A technology routes to `0xc0`
  with power mask `39` (switched-on states), and reads the routing table back.
  It requests NFC-A passive listening only. An empty SET_LISTEN_MODE_ROUTING
  command returned NCI status `03`; cleanup instead clears temporary routes
  with reset/init, as the normal driver does.
- **First attempt:** the controller counted RF field changes but neither
  eSE-action nor interface-activation notifications, and the second phone saw
  no tag. Reading NCI configuration showed `NFCC_CONFIG_CONTROL` (`85`) was
  `00`. Stock Android sets `01`, allowing the controller to manage RF
  configuration supplied by NFCEEs. The experiment now sets `85=01`, enables
  field/action notifications (`80=01`, `81=01`), and sends HCI
  `EVT_END_OF_APDU_TRANSACTION` (`61`) on pipe `19` after releasing forced
  wired link power. Both changes were made together, so their individual
  contributions have not been isolated.
- **Result:** the owner could detect a tag with another phone. The controller
  recorded eSE-action notifications naming host `0xc0`, confirming RF activity
  reached that endpoint. Host/interface activation notifications also occurred;
  neither counter establishes a successful transit transaction or the contents
  of a virtual card. No transaction payloads or tag identifiers were logged.
- **State after the test:** off-host listening remains active for the owner's
  real-reader test; normal text-tag emulation is paused and saved settings are
  unchanged. The temporary service is not enabled at boot and no production
  routing code is staged into images. Keep the phone awake for testing. Stop
  with `systemctl stop houji-ese-listen-test`: cleanup restores the original CRS
  state and controller flags, clears routes, rebinds the driver and restarts
  NFC Manager. That cleanup was tested successfully before the final RF-config
  changes; recovery metadata stays under `/var/lib/houji-ese-listen` until CRS
  restoration is verified. Sleep, crash recovery and a real iPASS reader remain
  untested for this temporary routing mode.

### [UNTESTED] IPASS in both NFC interfaces (2026-10-09)

- **Request:** promote the temporary routing experiment into a selectable mode,
  labelled exactly `[UNTESTED] IPASS`, in Houji Settings and NFC Manager.
- **Backend:** `ipass.py` uses the working CRS activation, route read-back,
  `NFCC_CONFIG_CONTROL=01` and wired-to-RF handoff sequence. The existing NFC
  service owns it under the same hardware mutex and process lock as text
  emulation and wired APDUs. Status returns only the mode and RF/eSE counters;
  no transaction data or card identifiers. `SetMode` switches all four modes,
  waiting for the current listener's cleanup before starting its successor.
- **Persistence and cleanup:** saved settings gain `ipass_enabled`, defaulting
  false when migrating the older schema. Before activating, the service saves
  the prior CRS activation and NFC configuration in an fsynced, owner-only
  recovery record. Cleanup restores and verifies CRS, resets temporary routing,
  restores Type-A and notification/configuration parameters, and rebinds the
  reader. A failed cleanup keeps the recovery record and blocks the queued
  mode; service startup/ExecStopPost attempts recovery. The new mode remains
  explicitly unverified on a real iPASS reader or through native sleep.
- **Verified on the handset:** IPASS → Off returned CRS `3F00`, cleared the
  recovery record, rebound the reader and unloaded the temporary supply module.
  Reader → text tag → IPASS worked with tag settings unchanged and the eSIM
  still registered. A normal NFC service restart restored IPASS. From Steam's
  JS context the Decky call selected IPASS and reported ready; the owner
  confirmed it in Houji Settings. Constructing the actual GTK app and updating
  it from live status showed the `[UNTESTED] IPASS` option selected, its mode
  selector enabled and the untested notice visible.
- **Integration:** stage the new module with NFC Manager. The temporary
  `houji-ese-listen-test.service` was stopped, its restoration verified, and
  its unit removed before the integrated mode took over. Existing private tag
  text and serials were retained.

### IPASS stopped listening after an I2C readiness NACK (2026-10-10)

- **Report:** after the integrated mode had been running, another phone could
  no longer detect it. NFC Manager still requested IPASS, but its hardware state
  was `error`. The log showed `ENXIO` from the three-byte NCI header read; the
  listener exited and cleanup restored the eSE/controller. No recovery record
  or temporary supply module remained. The failure occurred just before a
  light-to-native sleep transition, but that timing does not establish its cause.
- **Cause and fix:** Linux's `nxp_nci_i2c_irq_thread_fn` explicitly treats
  `ENXIO` as a possible readiness race: the controller may assert IRQ before
  it has readable data. Our userspace transport instead treated the first such
  header NACK as fatal. It now rechecks IRQ and retries header `ENXIO` at 5 ms
  intervals, bounded by both 100 ms and the caller's receive deadline. Other
  errors, short headers and payload-read failures remain fatal; once a header
  has been consumed, retrying the payload could lose packet alignment.
- **Diagnostics:** IPASS cleanup retains the last RF/eSE counters instead of
  replacing them with zeros, so an error still shows the activity before it.
- **Verification:** tests replay transient and persistent NACKs, IRQ dropping
  before retry, receive deadlines, a following valid packet, and payload errors.
  After installing and restarting NFC, the owner confirmed repeated phone-to-
  phone detection. The mode remained active after 24 fields and 61 eSE actions,
  with no new NFC errors and the eSIM still registered. Long-term reliability,
  sleep behavior and actual iPASS transactions remain unverified.

### IPASS detection during native sleep and after resume (2026-10-10)

- **Request:** verify that the off-host card still responds during sleep and
  after waking. The phone was unplugged, with native `s2idle` selected and the
  integrated IPASS listener active after the header-read NACK fix.
- **Hardware test:** the owner reported repeated detection from another phone
  while Houji's screen stayed off, and again after waking with Power. A private
  before/after snapshot showed 321.51 s elapsed and 216.65 s actually suspended
  (CLOCK_BOOTTIME minus CLOCK_MONOTONIC). The journal confirmed `s2idle` entries,
  sensor-DSP dark wakes and Power-key resumes. The boot and NFC PID were unchanged;
  the listener remained in IPASS mode. RF-field counts rose from 24 to 55, and
  eSE-action counts from 61 to 111, with no new NFC errors.
- **No sleep workaround:** the existing shared-power hold, off-host routing and
  mask `39` were sufficient for this test. No RTC alarm, extra sleep hook,
  inhibitor or forced light sleep was installed. Both UIs now describe the
  verified asleep/awake detection instead of asking the owner to keep the screen
  on. `[UNTESTED]` remains because no real iPASS terminal transaction or
  personalized-card state has been established. Long-duration reliability still
  needs normal-use testing.

### NFC stuck after the eSE APDU pipe disappeared (2026-10-12)

- **Report:** NFC broken again. Every NFC service start logged `NFC operation
  failed during "Recovering interrupted IPASS mode"` and refused mode
  changes, so Reader and Text tag were unusable too. The saved setting was
  already IPASS off.
- **State:** `/var/lib/armada-nfc/ipass-restore.json` dated from the
  2026-10-10 00:35 IPASS activation. The record stays while IPASS is active,
  so a later reboot left recovery pending. The journal reached back only to
  2026-10-12, which leaves the exact interruption unrecorded.
- **Cause:** a traced recovery failed in `Session.open`: CORE_GET_CONFIG
  returned `A023 = 00` (`A022` still `02`), where every earlier session read
  `01`. Resetting the controller configuration to the saved values did not
  change it. In NXP's Android stack, `A023 = 1` means "APDU pipe created";
  anything else releases the pipe, and `nfa_hci_api_config_nfcee` recreates
  it with ADM_CREATE_PIPE (gate `0x30` → host `0xc0` gate `0x30`) and
  ANY_OPEN_PIPE. Our code assumed the firmware always keeps the pipe. The
  earlier refusals happened while the pipe already existed. Cleanup also
  discarded every exception, which hid the cause.
- **Recovery on the handset:** with the owner's approval, one manual run
  created the pipe (the firmware assigned `0x19`), opened it (`A023 = 01`,
  maximum APDU `0x8000`) and then completed the pending restore. The record
  was removed. The service starts without errors, `houji-ese --probe` again
  returns 108 bytes with `9000` and `6A82`, and the eSIM stayed registered.
  Backups: `/root/ipass-restore.json.bak-20261012` and
  `/root/nfc-manager-backup-20261012/`.
- **Fix:** `Session.open` recreates and opens the pipe only when `A023`
  reports none, and stops if the firmware assigns any pipe other than `0x19`.
  The IPASS failure message now includes the original and first cleanup
  exception (fixed strings, no APDU/RF/record contents). Tests replay a missing
  pipe and a wrong pipe number. `[UNTESTED] IPASS` itself is unchanged; why
  the firmware lost the pipe is still unknown.

### IPASS confirmed working by the owner (2026-10-14)

- The owner reported the IPASS function as confirmed working. This is the
  first owner confirmation beyond phone-to-phone RF detection. At the owner's
  request the `[UNTESTED]` label was dropped from NFC Manager, Houji Settings,
  the service notices and the README, which now state that the card must be
  provisioned in Android first: the mode activates the existing applet and
  cannot add one.

### iPASS balance and trips read from the card (2026-10-15)

- **Issue:** IPASS paid, but the balance could only be seen in Wallet, which
  the reflash removed.
- **Cause (Google Wallet 26.37, `MifareCommandRunnerImpl` and its parser):**
  - Wallet selects a 12-byte per-card AID that its server supplies
    (`00A404000C` + AID), then sends three `80 B0` reads: balance and two trip
    lists. Sent to the activated VCM applet itself, `80 B0` returns `6D00`.
  - The contactless registry (read-only `GET STATUS`) lists the data applet
    next to each VCM instance: `A0000006914D344D534D0100` ("M4MSM") for the
    activated `…56434D0100`.
  - Each reply is `[tag 61, length 48, data, 9000]`. The balance is a
    little-endian int32 at the start; the balance reply ends with the last
    top-up record, and each trip reply holds three 16-byte records (status
    byte, Unix time, amount and balance afterwards, little-endian).
- **Fix:** The NFC service's `ReadIpassCard` pauses IPASS through the normal
  restore path, runs the four commands in one eSE session, and starts IPASS
  again. Houji Settings shows the result through its private loopback channel.
  `GetStatus` now also reports busy while a worker thread is still finishing:
  after an eSE session it had reported idle during the final status refresh,
  so an immediate mode change was refused.
- **Result:** On the owner's card the balance and seven records matched each
  other (every balance-after followed from the one before), including two
  payments made with this port's IPASS mode. The read with the pause took about
  3 seconds, and IPASS was ready again afterwards.

### Automatic transaction names from Wallet metadata (2026-10-15)

- **Issue:** Card history exposed codes, while Wallet showed operator/station
  names. Mapping system and location alone merged distinct stations; assuming
  two terminal bytes for every system was also incomplete.
- **Trace:** Wallet 26.37's `acrq` fetches
  `g/ipassmifare/gettransactionmetadata`; `acry` builds its indexes and `acse`
  resolves transactions. Rows contain system, optional location, terminal
  prefix, operator name and terminal name. Wallet uses a location-specific
  group where defined, otherwise the system group, then the longest prefix.
  Location-only entries handle operators such as bus companies.
- **Result:** The original signed APK ran in the PC's rooted Waydroid with
  Google apps. After normal sign-in, a version-specific Frida script invoked
  the metadata request through Wallet's live RPC client. It succeeded without
  an iPASS card provisioned there and returned 2,776 entries. Authentication
  stayed in Wallet; only the decoded metadata response was exported. Frida was
  stopped and its temporary server/export removed afterwards.
- **Implementation:** A bounded protobuf importer supports both RPC responses
  and Wallet's cached table format. The NFC service resolves names locally
  before returning card data, retaining full terminal IDs inside the service.
  Houji Settings displays automatic names and permits local overrides. The
  table was initially installed only as local data; the built-in snapshot below
  makes names available on fresh installations too.
- **Validation:** Synthetic tests cover location-only entries, variable-length
  prefixes, duplicates, operator fallback, malformed imports and private
  terminal IDs. On the handset all seven records resolved to names, and IPASS
  resumed within about three seconds. The generic partner-shop label in the
  table cannot identify an individual shop.

### Bundle the transaction-name table in fresh images (2026-10-15)

- **Issue:** The lookup code shipped, but new installations still required a
  separate metadata import before operator and station names appeared.
- **Fix:** Include the decoded 2,776-entry snapshot under `nfc/data/` and install
  it to `/usr/share/houji/ipass-transaction-metadata.json`. Preserve row order
  and duplicate-key precedence. The asset contains only lookup keys and names;
  account data, transactions and personal overrides are not included.
- **Updates:** Valid imports under `/var/lib/armada-nfc/` override the built-in
  table. Missing, unreadable or malformed imports fall back to the snapshot.
  Local place-name overrides continue to take precedence over automatic names.
- **Validation:** Tests load the shipped snapshot with no imported table, check
  representative rail/bus/operator names, and cover import precedence and
  fallback when an imported file is corrupt, oversized or unreadable. On the
  handset, temporarily removing the imported table still resolved all seven
  records using only the built-in copy; IPASS was ready again in 2.7 seconds.
  The existing imported table was restored afterwards.

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

### Switch to Desktop ignored the selected desktop again (2026-10-22)

- **Report:** with KDE Plasma chosen in Armada Control, Steam's Switch to Desktop
  opened Plasma Mobile, or went straight back to Game Mode. Desktop switching had
  broken after an earlier release pull for the same underlying reason.
- **Cause:** the port shipped its own `/usr/libexec/os-session-select`, the hook
  Steam's switch runs, which replaced upstream's file of the same path. It started
  a Houji worker that always wrote `armada-plasma-mobile.desktop` into
  `zz-steamos-autologin.conf`. Armada 20261010 moved switching to steamos-manager:
  upstream's hook calls `steamosctl switch-to-desktop-mode`, the choice lives in
  steamos-manager's `desktop_session` (set by Armada Control), and autologin is
  `zz-holo-autologin.conf`, which `armada-fixups` migrates the old file to. The port
  copy ignored the choice, and `stage-root.py` also masked upstream's
  `armada-session-default.service`. Upstream's `plasma-session-test.sh` kept passing
  because it tests upstream's hook, which the image did not contain.
- **Fix:** removed the port's hook, worker, unit and sudoers rule, the autologin file
  seeded at staging and the mask. Session switching is upstream's code alone.
- **Gate:** `ports/houji/check-upstream.py` runs first in `build.py` and again on the
  staged root in `stage-root.py`. It fails the build if a port overlay replaces any
  upstream file, if staging or overlays touch session switching, autologin or
  `armada-session-default`, if upstream's session test fails, or if the staged
  session files differ from `system_files`. The port's six Wi-Fi/GPU firmware files
  that must replace upstream's are pinned to the upstream hashes they were reviewed
  against; when a release changes one, the build stops until it is reviewed again.
  Run `python3 ports/houji/check-upstream.py` right after every release pull.
  `tests/houji-upstream-session-test.py` covers the gate, including this bug.
- **Verified:** on the phone, with upstream's hook installed in place of the port
  copy and the session default unmasked, Switch to Desktop opened
  `armada-plasma-session standalone` (KDE Plasma, as selected), and Return to Game
  Mode brought Steam back. All 44 Houji unit test files, upstream's
  `plasma-session-test.sh` and `boot-hotkeys-test.sh` passed.

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

### NFC controls in Game Mode (2026-10-09)

- **Request:** expose NFC emulation and scanning controls in Houji Settings.
- **Controls:** replaced the single NFC toggle with Off / Reader / Text-tag
  emulation, an editor for saved text and automatic/custom four-byte serial,
  Save / Save and start, Scan again, Stop and Retry emulation, live status,
  detected-tag count and text read-request count. The editor calls the same
  NFC Manager service and shares its persistent settings. The owner verified
  the controls and reading the emulated tag from another device.
- **Busy state:** the service marks the emulator busy for its entire listening
  session. Stop and switching back to Reader remain available in that state;
  edits and competing eSE/reader operations wait for it to stop.
- **Privacy:** Decky logs RPC arguments and replies. Saved tag text and serials
  therefore travel through the private loopback endpoint in both directions,
  using single-use, endpoint-bound tokens. The general status reply contains
  modes and counters only. A Steam-context check loaded and saved the tag
  without changing it or returning its contents through the status RPC.
- **Scan notifications:** the owner requested a Reader-mode toggle for a toast
  on each new detection. It defaults off and persists in
  `/etc/armada/houji-nfc-notifications`. While enabled, the plugin monitors NFC
  outside the panel lifecycle and emits a payload-free event to the frontend.
  The native service counts arrivals of neard tag objects, keeping object paths
  private and avoiding duplicate notifications during repeated polls. Enable,
  reader-mode changes and service restarts establish a fresh baseline. Disabled
  monitoring reads only the preference file and does not query the modem or NFC.
  The owner verified one notification with the toggle enabled and none with it
  disabled, using Scan again between detections.

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

### Hogwarts Legacy crashed after the warning screen (2026-10-10)

- **Initial setup:** Heroic's apparent installation stall was slow prerequisite
  traffic: Visual C++ 2022 downloaded from Microsoft at roughly 50 KiB/s, then
  the enabled BattlEye runtime downloaded from GitHub at roughly 33 KiB/s. Both
  finished. The VC installer matched Winetricks' expected SHA-256 and its x86/x64
  installer logs ended with exit code 0. No prefix recreation was needed.
- **Startup failure:** the Epic build reached its photosensitivity warning,
  then reported `GPUCrash` after 51–52 seconds. Its error was
  `CommandList->Close() ... E_INVALIDARG`. Turnip had initialized a swapchain;
  neither a kernel GPU fault nor an OOM kill was recorded, and the crash context
  reported `bIsOOM=0`.
- **Cause traced:** VKD3D-Proton 3.1 rejected compute shader `55462a5ff5f1fbb5`
  for unsupported native 16-bit operations. A null resource/pipeline then made
  the command list invalid. On the installed Mesa 26.2.3, Adreno 750 exposes
  FP16 arithmetic and storage but not FP16 denormal preservation; VKD3D therefore
  does not expose the full D3D12 native-16-bit capability. Heroic's default
  `VKD3D_DEBUG=fixme` hid the decisive warning. A game-local `warn` setting needed
  Heroic to restart because it had cached the old configuration in memory.
- **Working workaround:** in this game's prefix, append the following to
  `drive_c/users/steamuser/AppData/Local/HogwartsLegacy/Saved/Config/WindowsNoEditor/Engine.ini`:

  ```ini
  [SystemSettings]
  r.FidelityFX.FSR2.UseNativeDX12=0
  r.FidelityFX.FSR3.UseNativeDX12=0
  ```

  These game-supported switches select the Unreal RHI fallback instead of FSR's
  native DX12 backend. FSR 2 Quality remains selected. An earlier attempt to set
  `UpscaleMode=None` was reset by the game and did not avoid the shader rejection.
- **Result:** after applying the fallback, the same shader rejection and invalid
  command-list failure disappeared, the game stayed active past the previous
  crash point, and the owner confirmed it reached setup/menu. Sustained gameplay
  and performance remain unverified. The fix is in the handset's game prefix;
  it does not replace Proton, Mesa or VKD3D. Original settings and diagnostic
  logs were backed up privately, and the temporary logging option was removed
  from Heroic's saved configuration after confirmation.

### GTA V Enhanced crashed during frame presentation (2026-10-11)

- **Launcher setup:** the official Rockstar installer was added to Steam as a
  non-Steam shortcut, then the same shortcut was pointed at the installed
  `Launcher.exe` to retain its prefix. CachyOS Proton's July build drew a frozen
  sign-in form; the installed October Proton Experimental ARM64 build made the
  form responsive. Login error `#1.000.7` cleared after selecting `Asia/Taipei`
  for the phone and `TZ=Asia/Taipei` for the shortcut. A Windows API probe in
  that prefix confirmed `Taipei Standard Time`, UTC+8. Rockstar downloaded
  through Akamai at approximately 11 MiB/s in the measured sample.
- **Failure:** Enhanced build `1.0.1158.16` exited about 27 seconds after launch
  with `0xc0000005`. Five retained minidumps showed an execute access violation
  at address zero. The return address was `GTA5_Enhanced.exe+0x1e7b330`, after
  the indirect call through vtable slot `0x40` in the DXGI `Present()` path.
  `SocialClubD3D12Renderer.dll` was loaded shortly before the crash. There was
  no retained kernel GPU fault or storage failure in the inspected interval.
- **Rejected workaround:** disabling `SocialClubD3D12Renderer` via a game-only
  Wine DLL override let the game display its loading artwork
  without the null-pointer exception, but the owner then confirmed it stayed at
  “Initializing.” Social Club reported its renderer could not load. This was
  not a working-game result, and the override was removed. Stable Proton
  `11.0-2e-arm64`, with the overlay restored, instead crashed with illegal
  instruction `0xc000001d` at `d3d12core.dll+0x525c0`.
- **Working setup:** keep stable `Proton 11.0-2 (ARM64)` selected and copy its
  bundled x86-64 graphics DLLs alongside `GTA5_Enhanced.exe`:

  | Source relative to `Proton 11.0 (ARM64)/` | Game-local file |
  | --- | --- |
  | `files/lib/wine/dxvk/x86_64-windows/dxgi.dll` | `dxgi.dll` |
  | `files/lib/wine/vkd3d-proton/x86_64-windows/d3d12.dll` | `d3d12.dll` |
  | `files/lib/wine/vkd3d-proton/x86_64-windows/d3d12core.dll` | `d3d12core.dll` |

  Scope their native overrides to this executable in the Rockstar prefix:

  ```reg
  [HKEY_CURRENT_USER\Software\Wine\AppDefaults\GTA5_Enhanced.exe\DllOverrides]
  "dxgi"="native"
  "d3d12"="native"
  "d3d12core"="native"
  ```

  Leave `SocialClubD3D12Renderer` enabled (no disabling override). The game-local
  DLLs were absent before this test and are byte-identical to the installed
  Proton's x86-64 copies. No system or Proton installation files were replaced.
  Delete these three local files and override values to undo the fallback.
- **Result:** the log confirmed all three graphics DLLs loaded from the game
  directory and the Social Club D3D12 renderer loaded successfully. Social
  Club completed its browser/sign-in initialization, the owner reported
  “Entering Story Mode,” and a screenshot confirmed actual gameplay in the
  opening bank-heist mission. This supports an overlay-hook incompatibility
  with the native ARM64/ARM64EC graphics path; using the x86-64 implementations
  lets the hook operate correctly. The fallback's performance cost is unmeasured.
- **Separate graphics limitation:** VKD3D rejected 11 shader creations with
  `Required WaveSize range [32, 32], but supported range is [64, 128]`. These
  same errors occurred on the run that reached gameplay, so they did not block
  this startup. No wave-size spoofing was applied. Rendering correctness and
  longer Story Mode sessions remain unverified.
- **Cleanup:** temporary `PROTON_LOG`, Wine trace and `VKD3D_DEBUG` launch
  options were removed for subsequent launches. The running game was left
  alone, so its current process retains logging until exit. The launcher wrapper and
  Taipei time-zone option remain. A trial launcher `-nobattleye` argument did
  not change the logged game entry point (`GTA5_Enhanced_BE.exe`) and was also
  removed; it was not the successful fix. Diagnostic evidence and the prior
  settings are private on the handset. GTA Online was not tested.

### GTA V Enhanced: sustained CPU-side limit near 30 FPS (2026-10-18)

- **Report:** Why does this Snapdragon 8 Gen 3 handset not reach 60 FPS when
  Snapdragon 8 Gen 2 demonstrations show 50–60? Captures were taken on Armada
  `20261010.c3d178c`, kernel `k20261010`, from 14:56 to 15:22 Taiwan time
  (UTC+8). The running game was Enhanced at 1280×720, largely minimum settings,
  ray tracing off, frame generation off, and in-game VSync on. Gamescope's
  limiter was 60, not 30. Recorded frame times varied substantially and included
  frames faster than 25 ms; the average near 30 is not evidence of a fixed
  30 FPS cap. VSync's contribution has not been isolated.
- **Measured load:** A 30-second Mangoapp frame log averaged 32.9 FPS with
  its reported 1% minimum at 19.9 FPS, 88% whole-system CPU load and 43.6%
  GPU load. A separate DRM fdinfo delta also showed the game GPU engine busy
  for only about 38% of its capture interval. Under sustained load the virtual
  skin estimate reached about 46°C; cpufreq caps were 1344 MHz on the little
  cluster, 1286.4 MHz on both big clusters and 1132.8 MHz on the prime core.
  The GPU was then 770 MHz. These are active stock-derived thermal-policy
  caps despite the Performance profile, not the silicon's maximum clocks.
- **Memory A/B:** Available RAM initially fell to roughly 200–300 MiB, with
  about 4.7 MiB/s of swap-ins during the moving baseline. The owner then held
  a stationary scene. One approved writeback pass selected pages untouched
  for ten minutes and imposed a 1 GiB limit within the existing daily budget.
  zram physical use fell by about 688 MiB; available memory rose by about
  607 MiB between the ends of the two samples. The kernel returned `EIO`
  after consuming the pass limit (an explicit path in `zram_writeback_slots`);
  counters show 1 GiB was written and the daily allowance was restored by
  `finally`. The subsequent capture was run separately after that exception.
  No persistent idle-age change was made; the normal 30-minute timer remains.

  | Capture | Average FPS | MangoHud 1% minimum | CPU load | GPU load |
  | --- | ---: | ---: | ---: | ---: |
  | Moving baseline, 30 seconds | 32.9 | 19.9 | 88.0% | 43.6% |
  | Stationary before writeback, 25 seconds | 30.0 | 19.0 | 89.1% | 43.5% |
  | Stationary after writeback, 25 seconds | 29.3 | 18.8 | 89.4% | 43.3% |
  | GPU at 578 MHz after two minutes, 25 seconds | 27.0 | 17.6 | 89.3% | 52.8% |

  The single short A/B does not show an FPS gain from writeback. Paging was
  already much lower in the stationary scene; this does not settle its impact
  on driving/streaming stutters. These runs are not repeated benchmark-suite
  results, and the moving baseline is not directly comparable to the others.
- **GPU/heat A/B:** With the owner's approval, the normal power-service API
  temporarily selected a 578 MHz manual GPU clock. GPU/CPU sensor averages
  fell to about 66/67°C, but the virtual skin estimate only fell to about
  45.4°C and CPU caps did not rise. FPS decreased. The original `auto` mode
  and stored manual-clock value were restored in `finally` and independently
  read back. This short trial does not measure performance with cold or
  externally cooled CPU clusters.
- **Translation path:** `/proc` mappings and SHA-256 identities confirm the
  game-local DXGI and D3D12 core match Proton's x86-64 fallback copies. The PE
  load-config CHPE pointer is zero in those files and nonzero in the bundled
  ARM64EC counterparts; a PE machine value of `0x8664` alone cannot distinguish
  them. Thus the graphics implementation also incurs x86 translation. That
  fallback previously resolved the launch/overlay crashes documented above;
  its incremental cost is still unmeasured. Live FEX shared-memory statistics
  identify the active ARM64EC translator as **FEX-2607**, bundled with Proton,
  rather than the host release's FEX-2610. A ten-second sample contained no
  SIGBUS or soft-float fallback events; this is not a CPU hotspot profile.
- **Launcher fix:** `STEAM_COMPAT_APP_ID=0` masked the non-Steam shortcut's
  real `SteamAppId`, so FEX cache names and performance overrides used app 0.
  The wrapper now skips zero/invalid IDs and decodes a shortcut `SteamGameId`
  only when its type word is valid. Regression coverage checks normal games,
  non-Steam shortcuts, packed IDs and invalid values. Performance-settings
  tests passed, and the installed wrapper matches the source hash with a
  rollback copy retained. It takes effect on the next launch. No per-game
  override was configured in this capture, so this fix alone is not a measured
  FPS improvement.
- **Comparison and conclusion:** The retrieved
  [Odin 2 Max demonstration](https://www.youtube.com/watch?v=tlRSQFOzYoA) uses
  Winlator 7.0.1, DXVK 2.3.1 and 720p; at 6:49–7:06 its author describes
  50–60 FPS in the indoor opening heist. That is the older D3D11/Legacy path,
  not an equivalent Enhanced/D3D12 outdoor test. The current captures support
  a CPU-side throughput limit under thermal caps, with additional translated
  graphics work and memory pressure. They do not quantify each contribution
  or demonstrate that Enhanced can sustain 60 FPS. A restart-controlled test
  of a compatible ARM64EC graphics path/newer bundled FEX and a matched
  cold-versus-hot workload are the next useful measurements. The current game
  stayed running, graphics preferences were not edited, and raw captures,
  process data and rollback receipts remain outside Git.

### GTA V Enhanced: repair the native ARM64EC overlay boundary (2026-10-18)

- **Request:** Repair the native graphics path and test a newer compatible
  bundled FEX. The owner authorized restarting saved gameplay and autonomous
  menu input while away. Prefix state, settings and the three working x64
  fallback DLLs were backed up privately. No game graphics preferences were
  changed; their SHA-256 matched the pre-test copy after gameplay.
- **Compatible stack:** The installed October 14 Proton Experimental ARM64
  contains FEX `2610-25-g5285020`, confirmed from the running game's shared-memory
  header. This is a matched Wine/FEX build rather than a host-FEX substitution.
  Stock native Experimental graphics still reproduced the null-execute failure
  just after Social Club's renderer loaded. Keeping x64 DXGI while switching
  the D3D12 pair to ARM64EC reached Story Mode, isolating the remaining boundary.
- **Root cause:** DXVK returns `DxgiSwapChainDispatcher` to callers. The inner
  swap chain has `DXVK_HOTPATCHABLE` annotations, but the dispatcher does not.
  Reverse engineering the unchanged Social Club renderer identified its eight
  MinHook installations. Live capture showed the factory and D3D12 queue hooks
  targeting proper hybrid thunks, while dispatcher `Present` and `ResizeBuffers`
  exposed raw ARM64 code. MinHook decoded those ARM64 bytes as x64, constructed
  invalid trampolines, then replaced their prologues with x64 `E9` jumps before
  the game crashed. Thus the x64 fallback was avoiding a specific missing
  hook-compatible entrypoint, not proving native D3D12 itself unusable.
- **Repair:** Add the same six `DXVK_HOTPATCHABLE` annotations to the dispatcher
  and rebuild only native ARM64EC DXGI from matching DXVK revision
  `a78a467cadb22bee928904290cf8e7921b121851` using llvm-mingw 20261006. The resulting
  public methods point to x64 hybrid patch thunks with native ARM64EC targets.
  The verifier checks the actual PE CHPE ranges, symbol addresses and thunk
  encodings. Live capture confirms all eight Social Club hooks installed on
  compatible entries, and the native `Present`/`ResizeBuffers` bodies still
  matched the on-disk instructions after gameplay.
- **Deployment:** Game-local patched DXGI plus Experimental's matching native
  `d3d12.dll`/`d3d12core.dll`, with existing executable-scoped overrides retained.
  The shortcut selects Experimental ARM64. Social Club remains enabled; no
  proprietary DLL was patched. Build inputs, source patch, offline builder,
  entrypoint verifier, identities and rollback instructions are under
  [`graphics/dxvk/`](graphics/dxvk/README.md). Raw captures and backups stay
  outside Git.
- **Validation, Taiwan UTC+8:** The all-native configuration reached Story Mode,
  rendered indoors/outdoors and accepted movement. The clean Steam-launched
  game had `WINEDEBUG=-all`, no `PROTON_LOG`, the corrected non-Steam app-ID FEX
  configuration, and the intended native DLL hashes mapped. At 16:47:52 the
  process remained alive with intact native method bodies. Samples: 47.9 FPS
  indoors (30 s, 1% minimum 30.1), 36.0 outdoors (45 s, 23.9), and 33.7 in a later
  hot outdoor capture (60 s, 20.3). Scene/temperature differences prevent
  assigning these changes solely to native graphics or FEX. The hot run again
  capped big cores at 1286.4 MHz and prime at 1132.8 MHz; 60 FPS remains unmet.
  A second clean full-prefix Steam launch also reached Story Mode by 16:57;
  file identities, native method bodies and unchanged graphics settings were
  verified again. The game was left paused at 16:58, with diagnostic units
  inactive and the normal thermal/sleep services active.
- **Test-harness corrections:** The initial stop check missed a trailing slash
  in `WINEPREFIX`; registry/save state was captured again after explicit prefix
  shutdown. Steam temporarily lost the shortcut from its in-memory overview;
  a Game Mode session restart restored both existing shortcuts and normal Steam
  launch. Temporary direct-launch focus overrides and virtual input were
  cleared. A Meson fallback fetched one submodule before the workflow was made
  strictly offline; the retained builder now requires all submodules in advance
  and uses `--wrap-mode=nodownload`. Subsequent GitHub fetches used verified WARP
  connections with immediate disconnection afterward.

### Phone coolers on GTA V's built-in benchmark (2026-10-19)

- **Correction after profile audit:** saved metadata shows Performance for the
  bare/controller-only/FunCooler runs, but Balanced for the initial FX5 blue and
  orange runs. The table below therefore mixes power profiles and cannot establish
  a controlled cooler ranking. A new FX5 orange series explicitly verified the
  `performance` governor on every CPU policy at each benchmark start: 37.70,
  36.60 and 36.44 FPS (mean 36.91, sample SD 0.68). These were three successive
  built-in benchmarks in one session, not three independent cold starts.

- **Request:** Compare the GameSir FX5 Lite (a thermoelectric cooler for the X5
  controller) and the Black Shark FunCooler 2 Pro on GTA V Enhanced.
- **Method:** GTA's own performance test (Settings > Graphics > Tab; five passes,
  about 3.3 minutes) at the shipped settings, with the `balanced` thermal policy.
  Phone unplugged, brightness 500, no downloads. Each run: quit the game, cool to a
  start state, fresh Steam launch, Story Mode, then every menu step by hand with a
  screenshot after it. The recorder only logs temperatures, frequency caps, power and
  MangoHud frame times (`thermal/cooler-benchmark/`). Two earlier methods were dropped:
  an idle-scene capture whose camera drifted, and freezing the game between runs,
  which ended in an out-of-memory kill of the Game Mode session.
- **Results** (mean of GTA's five pass averages, one run each):

  | configuration | mean FPS | skin / battery at start | battery draw |
  |---|---|---|---|
  | bare phone | 29.4 | 46.3 / 43.6 °C | 5.9 W |
  | bare + FunCooler 2 Pro (max) | 46.9 | 43.8 / 36.0 °C | 8.9 W |
  | phone in X5 controller | 28.7 | 46.6 / 44.1 °C | 6.0 W |
  | controller + FX5 Lite (blue) | 42.4 | 45.1 / 38.2 °C | 7.6 W |
  | controller + FX5 Lite (orange) | 37.4 | 45.0 / 37.5 °C | 7.8 W |

  The orange run's temperatures, frequency caps and draw match the blue run, yet its
  frame rate is 12% lower (slightly above the 9% spread seen between repeats), so one run
  each cannot tell the two modes apart. The cooler's mode was set by hand and not verified.
  The green mode has not been tested.

  Without a cooler the CPU caps fell to 1.0–1.3 GHz and the GPU to 578 MHz. With one
  they stayed around 1.4–1.9 GHz and 600–620 MHz. The cooler did not keep skin below the
  policy's 43–44 °C step (it held 44–45 °C); it kept the battery near 37–39 °C instead
  of 45 °C and the die and GPU less capped. The controller alone changed nothing.
  MangoHud frame times over the sustained last stretch agree (29.4 / 44.1 / 38.1 fps).
- **Caveats:** One run per configuration; earlier repeats varied by about 9%, so the
  FunCooler's lead of about 10% over the FX5 Lite is within noise. The FunCooler run
  started from a colder phone (battery 23.7 °C against 29–32 °C for the others), the
  FX5 run followed a fresh boot with no swap in use and a longer lead-in (9.8 against
  7.8–8.3 minutes), and ambient temperature was not measured. The recorder stopped on the
  45 °C battery limit before the benchmark ended in the bare and controller-only runs;
  GTA's reports are complete, the telemetry covers 165 s and 94 s of them.
- **Harness change:** The recorder's own 92 °C die limit aborted the first FunCooler
  attempt on a single spike while GTA kept running. Stock Android has no die limit and
  the kernel's critical trip is 110 °C, so it now aborts at 105 °C. The skin (49 °C),
  battery (45 °C) and low-battery (12%) limits are unchanged.
- **Battery:** Across all runs the main percentage stayed within about 1–3 points of the
  raw gauge while unplugged (80% to 29%), with no jump and no 0%.

## Open problems

- Intermittent animation freeze around Steam wake: never reproduced or explained.
- Native sleep still resets the phone occasionally. From Oct 18 to 22 there were
  5 resets in 371 recorded sleep cycles (about 1.3%), each right after a sensor-DSP
  dark wake. Each console log (pstore) ends on the next suspend just after
  `psci-exit state=0x41000044`, with no panic, oops or watchdog. Charger plug-in,
  the power key and short gaps between wakes were not more common before resets
  than overall. The Oct 22 one followed four dark wakes in a minute while the
  phone was handled; neither the power key nor USB had registered. Light sleep
  avoids it at higher drain. A sixth, on Oct 23 at 16:18, matches: the console log
  ends at cycle 32, the suspend after a dark wake that served the sensor DSP, with no
  panic. Evidence: `build/houji-day-report-20261022/`,
  `build/houji-resume-mango-20261023/last-boot-end.private.txt`.
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
- Cellular, cameras and fingerprint are not implemented.
- Other panel revisions, storage sizes and regional firmware are untested.
