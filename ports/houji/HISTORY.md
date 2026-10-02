# Houji porting history

This is a technical summary of the work through October 2026. Current status,
build instructions and installation instructions are in [README.md](README.md).
Development image names, device identifiers, network profiles, coordinates,
recordings and raw logs are intentionally omitted. Most investigation and
implementation was performed by an AI agent, with hardware observations and
supervised tests supplied by the device owner.

## Follow-up regressions after the release migration

**Game Mode switch:** Houji wrote its session choice to
`zz-holo-autologin.conf`, while Armada's session settings wrote
`zz-steamos-autologin.conf`. SDDM reads the latter afterward, so an existing
Plasma preference overrode a request to enter Game Mode. Use Armada's canonical
file in both the image builder and the switch worker, and remove obsolete Houji
and temporary overrides. Keep the worker outside the session it stops and clear
the previous graphical session's environment. An isolated regression test starts
with conflicting settings and checks both directions and a second Game Mode
switch.

**Battery power:** The firmware's translated `POWER_NOW` field remained at
10,000,000 microwatts while the gauge's voltage and current changed. btop preferred
that field and displayed a fixed 10 W. Remove this unsupported property from the
Houji battery descriptor; retain measured voltage, current and charge. Consumers
can calculate watts from voltage and current and use charge for runtime estimates.
The other Qualcomm battery descriptors are unchanged. This reading represents
net battery power, not wall-charger input or the phone's total consumption while
external power is connected.

**Native sleep:** Later Game Mode testing recorded an ADSP sensor watchdog after
resume, followed by audio errors. Add a native-sleep hook that stops the active
orientation consumer and sensor proxy before sleep, then restarts only those
that were running. Keep the DSP file server alive for its other clients. This
supplements the touch decoder's existing sleep hook.

A 35-minute baseline still crashed the sensor DSP and lost the soundcard. Its
FastRPC trace showed a listener reply while userspace was frozen. The SSC
accelerometer driver released its client at shutdown without explicitly
disabling an active measurement subscription. Patch its close path to disconnect
the callback and synchronously send the SSC disable request before releasing
the client; pin that source patch in the build. With the original kernel and
this userspace fix, a 40-minute RTC-backed native sleep returned in about three
seconds, logged no ADSP crash or suspended listener reply, retained the Xiaomi
soundcard, and played audio through the speaker. Steam and touch were responsive
afterward. This comparison initially implicated the stale subscription, but
the recurrence below shows that explicit subscription teardown is insufficient.
It does not identify the DSP's internal blocked thread.

A later ordinary Power-button sleep reproduced the same sensor watchdog with
the patched proxy still installed. The AP spent about two minutes asleep;
audio graph restoration then incurred two roughly five-second DSP timeouts.
During remoteproc recovery, `soc_resume_deferred` faulted while traversing the
soundcard's components. ADSP remained offline, so the soundcard, battery and
charging telemetry, sensor service and USB-C control did not recover. The
asynchronous sleep hooks did release the desktop promptly after kernel resume,
but they cannot repair this DSP/kernel failure. A normal reboot also stalled.
The earlier 40-minute pass is a single successful test, not a resolution of the
watchdog. Raw logs remain outside the repository.

An experimental kernel patch that voted for a wake on every sensor FastRPC
listener response did not complete its startup validation. The previous boot
image was restored and the patch was omitted from the build. The trial recorded
many wake events but retained no usable boot trace, so it did not establish why
startup failed. Retain the explicit SSC teardown, but it has not eliminated the
watchdog.

**DSP callback wake handling:** Stock `sscrpcd` enables FastRPC wake-lock
control before starting its listener. The mainline transport did not wake the
AP when the sensor DSP returned a reverse RPC to the frozen file-service
process. Add a bounded wake for successful sensor `NEXT2` replies during
suspend. Also track unanswered replies before freezing and reject the device
prepare stage while one remains. A successful subsequent `NEXT2` send clears
the obligation; a generation counter preserves a newer request if its callback
arrives before that send returns. Idle listener waits do not block sleep.

The early-after-boot hardware test entered native sleep and woke on a real
FastRPC reply after about 228 seconds, before its RTC alarm. The trace recorded
the reply, the FastRPC wake event, and the listener sending its response about
1.25 seconds later. There was no DSP watchdog or kernel fault. Speaker PCM
playback ran through the hardware afterward, live accelerometer samples
arrived, and battery queries succeeded. This exercises the missing service
wake path; it is not an uninterrupted long-sleep pass. Such firmware requests
can now wake the system, and automatic return to sleep after servicing them
has not been implemented.

A follow-up Power-key sleep with a three-minute RTC backstop was woken by a
physical Power tap. The owner confirmed that Steam, touch and the battery
indicator worked afterward. Kernel counters showed seven successful s2idle
cycles on this boot, no failures, no sensor watchdog, no ADSP crash and no
kernel fault, and speaker PCM playback, accelerometer samples and battery
queries still worked. The test service's result files were not retained, so
this check rests on the owner's report and kernel counters.

Separately, drain ASoC's deferred resume worker before an instantiated card is
unbound. DSP recovery must not remove the component lists while that worker
is traversing them. The new kernel and every in-tree and port module were built
together and installed under a separate release for safe rollback. Normal
resume and playback passed; the teardown protection has not been validated by
deliberately crashing the DSP. Long-term reliability remains under test.

A follow-up trace found the new sensor restart held `user.slice` frozen for
almost two seconds after the kernel had resumed. The sensor proxy's startup
includes an SSC warm-up. Queue that restart without blocking the sleep hook;
the orientation service's systemd ordering still waits for the sensor proxy.
Steam can render while sensor initialization completes, and an unavailable
sensor no longer delays thawing the desktop. This addresses the measured wake
pause, but does not prove the owner's earlier persistent Steam freeze had the
same cause. Long-term reliability remains under investigation.

The before/after trace measured the remaining frozen-user interval after kernel
resume at 1.927 seconds with synchronous sensor startup and 0.102 seconds with
queued startup. DRM page flips resumed about 0.16 seconds after restoring the
display in the latter test, with no suspend error or reboot. The trace records
timing and frame counters, not display contents.
The owner still observed a brief animation pause followed by recovery in a
manual test with queued startup. The earlier permanent Steam freeze has not
been reproduced after these changes, so that reported failure remains open;
the measured improvement must not be described as a complete fix.

**Game Mode display handoff:** Subsequent tests inject a short `KEY_POWER`
press/release through the real PMIC evdev device, so they exercise the normal
power-button handler rather than calling Steam's sleep URI directly. RTC wake
remains only an automated backstop; separate physical-button tests establish
Power-key wake. Temporarily stopping the handler while retaining logind's key
inhibitor produced no Steam sleep request, ruling out duplicate dispatch through
Gamescope in that test.

The owner clarified that waking showed a two-second black interval, then the
finished sleep animation before the wake animation. Native DRM resume restored
the saved scanout before Steam could draw its resumed state. Have Gamescope
disable its internal output before `sleep.target`, while userspace is still
running, and restore it after native sleep has finished. This belongs in a
service ordered before sleep, not a pre-sleep hook that runs with `user.slice`
already frozen. Preserve an already-disabled display and leave Plasma and light
sleep alone. `ExecStopPost` also restores a display disabled by a failed start.
The owner confirmed that the old sleep animation no longer appeared. The
approximately two-second wake delay remained; the callback trace attributed
about 0.39 seconds to PCIe resume and 0.87 seconds to Wi-Fi resume. Removing the
stale frame does not establish a fix for the earlier permanent freeze.

**Wi-Fi wake latency:** NetworkManager's previous default disconnected Wi-Fi
for sleep, so ath12k powered down the radio and reloaded its firmware before
userspace could resume. A trial selected WoWLAN magic-packet mode for the
onboard `ath12k_wifi7_pci` adapter. Its driver already supports retaining the
firmware through suspend. The same injected-Power test measured wake to first
DRM frame at 0.884 seconds instead of 1.636 seconds with the display handoff
alone; kernel resume fell from 1.250 to 0.443 seconds. Wi-Fi remained usable
afterward and the suspend-failure count did not increase. This is a measured
latency improvement, not proof of long-term reliability or lower standby power.
Ordinary traffic is not selected as a wake trigger; a matching magic packet can
wake the phone. Explicit per-network WoWLAN preferences override the default.

**Remaining failure:** Faster resume exposed another stale-frame race. A
compositor trial waits for a completed fresh frame before enabling the panel,
with a bounded fallback for a static client. Its first test measured 0.827
seconds to the first frame, and the owner initially confirmed the old frame
was gone with working touch. Repeated physical-button cycles then froze during
the animation and the phone became unreachable on the network. This overrides
the initial successful observation: the combined fast-wake path is not yet
validated for repeated use; a single successful cycle is insufficient evidence.

After recovery, the previous boot's journal ended at suspend entry and contained
no matching exit or surviving crash dump. That alone does not locate the stall:
messages produced while userspace is frozen may never reach the journal. A
bounded test freezing the desktop document portal did not block filesystem
sync, so the suspected FUSE/freezer interaction was not reproduced and the
freezer policy was left unchanged.

The next staged freezer/device tests and three full injected-key/RTC cycles all
completed in the same boot. SSH became unavailable during the full-cycle batch,
but the owner confirmed responsive Steam. Toggling Wi-Fi restored access; saved
traces showed successful kernel resumes and new DRM frames in every cycle.
This was a network recovery failure, not a reproduced animation freeze. Disable
WoWLAN as the onboard adapter's default pending investigation. Use an explicit
zero value: removing the trial setting can leave the driver's old wake policy
in place. Three comparison cycles with WoWLAN confirmed disabled restored
network access each time. Explicit per-network preferences remain respected.

The fresh-frame display handoff now has a timerfd in Gamescope's event loop,
so the static-client fallback does not depend on more client or vblank events.
A new sleep request cancels both a pending wake and its deadline. Both requests
are applied on the compositor thread; only the request crosses threads. The
public build applies this patch and copies its helper header, rather than
relying on the experimental binary installed on the test phone. These changes
do not establish the cause of the earlier reported animation freeze.

The revised compositor built through the public build entry point, and both
patches replayed against the pinned clean source exactly. On the phone, pausing
Steam's UI processes exercised the static-client fallback: the panel returned
after 0.506 seconds. A new sleep request cancelled an earlier pending wake.
Two recorded physical Power-key wakes and three further injected-key/RTC cycles
completed without a reboot or suspend failure; DRM frame delivery and network
access returned. The owner confirmed a completed wake animation and working
touch. The rebuilt preserving-update image contains the tested binary, helpers
and Wi-Fi policy; it has not itself been reflashed. The intermittent animation
freeze remains open because these tests did not identify its original cause.

**Validation:** Rebuilt the battery module from the public patch series and
loaded it through a normal reboot. btop then displayed changing signed battery
power (about -1.2 to -2.4 W during the test), instead of a constant 10 W. A live
round trip using Armada's desktop switch and the normal Game Mode launcher
returned to Steam. Four Steam-requested, RTC-woken native sleeps completed,
including approximately 58, 89 and 179 seconds actually suspended after the
reboot, with no recorded suspend failure, DSP watchdog or unexpected reboot.
Touch and sensor services resumed. The existing ath12k hwmon resume warning
remains. The preserving root image was rebuilt and checked for the updated
module, session preference and executable sleep hook. A subsequent manual test
stayed in native sleep for about 18 seconds and woke on the PMIC Power-key IRQ,
with no suspend failure or DSP crash. The owner observed a brief Steam animation
pause before the interface returned.

## Armada 20260926 migration

**Issue:** The fork's shared source was based on the September 19 tree and
Houji still built Linux 7.2.3. Its OCI userspace pin was a September 26
prerelease, rather than the final release. The kernel therefore lacked the
release's SM8650 PCIe sleep operating point, RPMh regulator sleep-state support
and AudioReach system-suspend changes.

**Change:** Merge the exact 20260926 release (`c2fd0485b4db`) and pin its final
OCI image by digest. Move Houji to the release's Linux 7.2.6 and use the Linux
manifest pin for the touch, NFC and GPS modules as well as root staging. Keep
the stock-ABL installer, board firmware and separately pinned touch-aware
Gamescope build. Preserve NFC Manager's saved settings during local updates.
After successful RTC and Power-button wake tests, select native `s2idle` as the
Houji default and let NetworkManager participate in suspend. Light sleep remains
selectable. Upstream's battery-saving figures do not establish Houji consumption.

**Compatibility:** Linux 7.2.6 already includes the GLINK detach and DPU/DSI
power-vote fixes, so their duplicate backports were retired. Keep the shared
battery property-set reply handler, merge the duplicate charge-current cases
with variant-specific decoding, and initialize Houji's current-limit watchdog
alongside the newer driver initialization. Refresh the audio patch
without restoring the resume capability that upstream removed when adding
AudioReach graph teardown/recreation.

**Validation:** All 181 shared and Houji kernel patches apply without fuzz or
failures. Installer, sleep-diagnostic, power-button, performance-policy,
charging-policy, panel-gamma and Wi-Fi parser regression checks pass. The final
battery driver also exactly reproduces from pristine source and the patch
series after reconciling those overlaps. The Linux 7.2.6 kernel and modules
compile successfully and boot to Steam on the development handset. The existing
light-sleep path still powers down the display domain and resumes. Native
`pm_test` freezer, devices and platform stages return, and an RTC-timed `s2idle`
cycle resumes without a reboot. A separate Power-button wake returns to Steam,
with working touch confirmed by the owner. Wi-Fi reconnects and touch frames continue.
AOSS/CX power-collapse counters did not advance; battery savings and long-term
reliability are not established. The complete final-release root image builds
from the pinned public inputs and boots from internal userdata, preserving home
and owner settings. A further RTC-timed cycle using its native default resumes
with no recorded suspend failure, reboot or touch-service restart. The owner
confirmed normal Steam rendering, taps and swipes after that final wake. The ath12k
resume path still emits a hwmon parent-sleep warning; Wi-Fi reconnects afterward.

**Native touch resume:** The first device-suspend test exposed an interrupt
racing the parent SPI controller's suspend. Its `-ESHUTDOWN` error permanently
disabled the touch IRQ, leaving the userspace service in a restart loop. Add
child-device PM callbacks that drain and disable the IRQ before the SPI parent
suspends and enable it after the parent resumes. Xiaomi's temporal touch decoder
also rejects frame gaps across sleep, so a native system-sleep hook stops it
before suspend and starts a fresh decoder session afterward, only if it was
previously running. The corrected RTC test resumes a healthy stream without an
unplanned service restart. Light sleep continues streaming as before.

## NFC clock and NCI packet parsing

**Issue:** The SN220 accepted NCI commands but found no cards. A vendor
notification was initially unrecognized by Linux.

**Fix:** Trace the notification in Xiaomi's HAL with IDA: it reports an
unexpected clock. Xiaomi's kernel enables GPIO35's always-on wake route for
CLKREQ, in addition to setting the pin as an input. Requesting that optional
interrupt through the normal GPIO/IRQ driver enables the route in mainline.
Add the I2C0 wiring, VEN and IRQ pins, and a vote on the shared L3C I/O supply.
Card discovery then works with the unmodified NXP NCI core; the experimental
vendor initialization commands are not part of the port.

**Issue:** Discovery succeeded, but connecting to the card failed. The
controller includes an additional byte in its NFC-A RF parameter block.
Linux advanced by the fields it decoded, treating that byte as the discovery
notification type. It reported an incomplete target list and misaligned the
subsequent activation fields.

**Fix:** In discovery and activation notifications, advance to the end of the
validated parameter block after parsing its known fields. The tested card
then activates through ISO-DEP and answers a standard NDEF application select
with `6A82` (application not found). This proves command/response transport;
it does not establish NDEF content reading or protected MIFARE Classic access.
Package the standard neard service, with the radio and continuous polling off
by default. No tag identifiers, contents or development packet captures are
included in the build.

**Validation:** After installing the updated root and boot images and rebooting,
the controller probes automatically without a temporary overlay. neard discovers
the card, and an independent ISO-DEP check repeats the successful activation and
command/response exchange. The radio is off on startup and is powered down after
testing. Reading NDEF contents from physical cards, other tag families and writes
still need separate validation.

**Follow-up:** Another phone successfully read a standard Type 4 text tag from
a temporary host responder on Houji. The installed neard service has no
emulation method, and the kernel's existing listen path handles NFC-DEP rather
than ISO-DEP card emulation. The diagnostic temporarily takes control of the
NCI transport and serves only a fixed, read-only test message. It leaves the
data-ready IRQ trigger unchanged and uses the normal driver's NCI configuration
reset when restoring reader mode. This established the hardware proof of concept
used by the NFC Manager implementation below.
A second test configured a synthetic four-byte NFCID1. The other phone confirmed
both the requested identifier and the text record. The original controller
settings were retained only in memory, restored and checked after the test;
reader mode was then restored. This does not establish other UID lengths or
emulation of a physical card's application and authentication behavior.

## NFC desktop controls and text-tag emulation

**Issue:** Plasma has no controls for this NFC backend. The temporary responder
required manual driver switching, and removing the reader driver released its
vote on a supply shared with Wi-Fi.

**Fix:** Add NFC Manager to KDE's application menu, with reader on/off,
rescanning and read-only text-tag emulation. A Polkit-protected D-Bus service
serializes controller access and restores the reader when emulation stops.
An explicitly loaded supply-hold module keeps NFC powered while the normal
driver is detached; it is unloaded after the driver is restored. Driver and
GPIO chip names are discovered at runtime. No received card data is saved.

The controller service checks current power state before changing it: neard
returns an error if asked to disable an already disabled adapter. Emulation
preserves the original protocol settings in memory, clears temporary routing
using the normal NCI reset, and verifies restoration. A service cleanup hook
reattaches the normal driver if the application backend exits unexpectedly.

**Validation:** The supply module loads and unloads on the installed kernel.
Reader on/off, rescanning, both serial modes and reader restoration pass through
the app's D-Bus service. The unprivileged local desktop can control it. Protocol
checks cover NDEF/CC reads, UTF-8 text, input limits and rejection of writes.
The owner also confirmed reading the text tag through the app with another phone.

**Background operation:** The initial app stopped emulation on close. At the
owner's request, the service now runs independently of the UI, saves tag settings
and the desired mode privately under `/var/lib/armada-nfc`, and restores that mode
at boot. Closing the window leaves emulation running; Stop emulation persists an
off choice. The service still clears temporary hardware routing before shutdown.
Client-disconnect and service-restart tests confirm that emulation continues,
settings reload and an explicit Stop remains off after restarting the service.

## Booting with Xiaomi ABL

**Issue:** Armada's handheld installation path expects a modified ABL and a
`/KERNEL` image. Xiaomi's stock bootloader instead loads Android boot-v4
partitions. Early candidates immediately returned to fastboot or hung at the
Xiaomi logo.

**Fix:** Package the Linux Image in `boot`, the initramfs in `init_boot`, and
the board DTB in `vendor_boot`. Use the stock load addresses and version metadata
expected by ABL. Preserve stock DTBO selectors while replacing the Android
payload with a no-op overlay. Export DT symbols, including the actual ARM timer
as `arch_timer`, for firmware-provided overlays. Match stock reserved-memory
and reserved-GPIO ranges. An unlocked bootloader still needs these conventions;
unlocking does not make arbitrary boot headers or device trees compatible.
Stock ABL successfully boots the resulting Linux system and is never flashed.

## Internal storage and sessions

**Issue:** Early tests used RAM or a computer-supplied USB root filesystem and
timed returns to fastboot. Disconnecting USB stopped those systems.

**Fix:** Install a new ext4 filesystem in `userdata`, containing the Armada
EROFS base and a persistent writable overlay. Mount it directly in the
initramfs, expand it on first boot, and remove network-root and reboot timers.
Disable generic ABL/bootc disk-update hooks for this custom layout. Session
switching runs in a detached system service so stopping Plasma does not kill
the worker that starts Game Mode. Gamescope uses direct DRM output and forced
composition rotation for the portrait panel.

**Issue:** The generic installer treated the overlay root as a live system and
exposed an installer for Armada's incompatible handheld partition layout.

**Fix:** Mask its visibility service and remove its desktop launcher from Houji
images. Installation and updates use the port's stock-ABL tools.

## Display, colors and sleep

**Issue:** Stock ABL's framebuffer was insufficient for a native display path.
Early full-screen updates showed colored noise, and mixed colors appeared pink.

**Fix:** Add the N3 panel's initialization sequence and 1200×2670 mode, with
RGB101010 DSI and 10-bit DSC. Restore each panel's validated gamma table from
SMEM where available. The decisive color fix was keeping the DDIC's RGB
rendering defaults: the stock command table bypassed them for Xiaomi's
source-rendered native-4:2:2 stream. Skip that stream-specific configuration
and use the stock SM8650 DSC precision setting. Primary and mixed-color charts
confirmed the gross tint correction; this was not a colorimetric calibration.

**Issue:** Waking could produce static or reboot the phone. The
light-sleep helper blanks the OLED and retains USB runtime power, but tracing
shows that Plasma's power manager independently shuts down the panel and DSI.
The helper therefore does not guarantee that display state survives sleep.
Deep suspend remains unsupported, with higher standby consumption than Android.

**Investigation:** Repeated display-only off/on cycles usually recovered, but
one wake stalled before its first completed frame and reported DPU timeouts.
A later cycle recovered. Full logind sleep/wake also reproduced reboots,
including with the Armada sleep helper bypassed and the power daemon stopped.
Keeping the GPU and display power domains active, separately or together,
did not prevent the resets. Disabling deep CPU idle did not help either.
Persistent function traces reached DPU plane programming on wake, before panel
initialization; they did not contain a panic stack or identify an exact faulting
instruction. Stock-driver comparison confirmed the basic N3 off-command, reset
and rail delays. Retaining the normal display operating point as well as its
runtime power then allowed three consecutive full sleep/wake cycles to pass.

**Fix:** Backport the upstream DPU and DSI power-vote corrections
[811c38907eab](https://git.kernel.org/linus/811c38907eab0f66c22c5e5708e6f8eab14d76fa)
and [06b7ba206561](https://git.kernel.org/linus/06b7ba206561619bb34116f49e0ef26b867ce3aa).
The pinned Linux 7.2.3 code calls `dev_pm_opp_set_rate(0)` during display shutdown.
That removes the required voltage request without reducing the configured
clock rate. On wake, plane registers can be programmed before the normal
performance update restores that request. Preserve the operating-point request
and let runtime PM remove and restore it with the device's power state. The
fix keeps normal clock gating and display power-down; it does not hold the
display or GPU permanently on or bypass Plasma's sleep handling.

**Validation:** The patched kernel and 1597 matching modules built successfully.
Twelve full sleep/wake cycles passed, with screen-off intervals from five to
60 seconds, automatic runtime PM, tracing disabled and the normal cold-reset
policy. GPU, GMU, MDSS, DPU and DSI reached runtime suspend, and the MDSS power
domain switched off. Display interrupts advanced after every wake, without a
reboot, underrun or frame-done/kickoff timeout. The new patches reproduce the
built source when applied through the shared and Houji patch series to the
pristine pinned kernel. The owner then confirmed normal wake, working touch and
no static after a physical Power-button off/on cycle; the boot ID stayed the
same and the kernel reported no new display errors. Long-term reliability and
deep suspend are not established by these tests.

**Steam Game Mode issue:** An initial 30-second sleep test confirmed GPU
and GMU runtime suspend and a frozen application group. However, MDSS, DPU and
DSI stayed active, the display power domain and panel supplies remained on,
and the DRM connector stayed on while `bl_power` changed to 4. Houji selected
the helper's backlight-only policy; Steam did not independently shut the
display down as Plasma did in the tests above.

**Fix:** With the DPU/DSI wake corrections installed, select the helper's DRM
display policy for Houji. It calls Gamescope's `drm_sleep_internal_screen` on
sleep and wake. Three hardware cycles, with 30-, 10- and 60-second sleep
intervals, confirmed GPU/GMU/MDSS/DPU/DSI runtime suspend and MDSS GDSC power-off.
The 30-second test also sampled the connector switching off and the panel's
supply requests dropping to zero. Wake restored the connector, panel supplies
and display interrupts, thawed applications, and kept the same boot ID without
GPU/DPU errors. Steam remained running afterward. This enables display
power-down within light sleep; whole-system deep suspend remains unsupported.

**Separate DSP interrupt fix:** Linux 7.2.3 cleared SMP2P's remembered interrupt
bits on ordinary notifications, replaying unchanged ADSP ready and handover
signals. Preserve that history unless the remote-restart flag changes. A
regression check covers ordinary notifications and both restart transitions;
the phone's ready and handover counts now remain at one after boot. This
correction alone did not eliminate the wake resets; the display power-vote
fixes above address a separate defect.

## Touchscreen

**Issue:** A Synaptics transport alone did not produce useful Linux touch
contacts. Repeated calibration patterns could not reproduce Xiaomi's complete
host-side processing.

**Fix:** Trace the stock touch library and driver with IDA, implement a buffered
SPI transport, and execute the hash-checked stock processing core through a
small ARM64 compatibility adapter. Convert contacts to Linux multitouch uinput
events. Continuous tapping and swiping worked in Plasma and Steam. Firmware
and touch configuration are extracted from the pinned public firmware image;
no owner's captured touch frames are build inputs.

## Wi-Fi

**Issue:** Generic WCN7850 firmware failed board-data loading. Stock Kiwi-v1
failed startup; Kiwi-v2 reached firmware but asserted during initialization.

**Fix:** Use the matching Kiwi-v2 firmware, global N3 board data and regulatory
files from the firmware baseline. Trace the firmware assertion and stock CNSS
initialization. Advertise the expected single-chip QMI MLO resource topology
before WMI initialization, even though mac80211 MLO is not enabled. Parse the
nested DBS/SBS capability TLVs correctly.

**Issue:** 5 GHz capabilities were truncated by a single-PDEV frequency-range
interpretation.

**Fix:** Correct that range handling. A nearby 80 MHz Wi-Fi 5 access point then
negotiated two spatial streams; local iperf tests measured about 509 Mbit/s
receive and 424 Mbit/s transmit. The tested 2.4 GHz access point advertised only
one HT stream at 20 MHz, explaining its 72.2 Mbit/s link rate. These measurements
are examples, not throughput guarantees. NetworkManager owns normal connections;
no test credentials or saved connection profiles ship with the port.

## Sensors, rotation and battery reporting

**Issue:** The SSC sensor service needed Xiaomi's registry layout, and initial
orientation was rotated by 180 degrees.

**Fix:** Integrate libssc, hexagonrpc and iio-sensor-proxy; adapt registry handling
and apply the corrected accelerometer mounting matrix. Read calibration from
each handset's own read-only `persist` partition. Plasma follows SensorProxy.
A Gamescope orientation service uses its rotation protocol, and a compositor
patch associates the uinput touchscreen with the internal display. Physical
alignment after every Steam orientation still needs broader testing. Gyro
input for aiming in games is not implemented.

**Issue:** Generic PMIC GLINK battery property IDs and current polarity did not
match this firmware, and Steam's battery display missed charging estimates.

**Fix:** Implement the Houji property mapping, correct current sign and expose
UPower telemetry to Steam's expected power interface. Preserve suspend limits
when changing desktop power profiles. State-of-charge and time estimates remain
firmware estimates, not proof that charge is increasing.

## Charging

**Issue:** Firmware could leave input current at 100 mA. Wireless charging could
start and then stop. An early diagnostic module also exposed a GLINK cleanup
hang.

**Fix:** Trace the stock HyperOS charger path. Implement the normal verification
completion message and a hash-checked adapter for the stock authentication
program. Apply conservative current limits, bounded leases and thermal checks
using actual board thermistors. Port the GLINK detach fix. Computer USB can
still supply less power than a running system consumes. Wall charging was
observed to increase charge with the screen off. Rated 90 W operation,
comprehensive thermal behavior and sustained wireless charging remain unverified.

### Fast charging after the 20260926 rebuild

**Issue:** The tested 3 A ceiling existed only in the installed system. Rebuilding
from the public sources omitted it, exposing the kernel's 1 A staging default.
The stock 90 W adapter still authenticated and entered fast-charge mode, but
battery power fell to roughly 3.7 W.

**Fix:** Ship the 3 A normal ceiling and retain the same fallback in the policy.
Use the stock firmware's read-only fast-charge mode (XM55), advertised APDO
power (XM61), real battery/adapter authentication and PD-PPS state to select a
separate HyperCharge ceiling. The normal-mode stock ceiling is 15.6 A at the
battery; this is an upper bound on a thermal vote, not a promised charging
current. Keep the stock SIC and display-dependent thermal tables, firmware
JEITA limits, gradual ramp and ten-second current lease. A staged 6 A test
reported up to about 19 W at the battery; stock thermal control held FCC near
4.6 A as the device warmed. Advertised adapter wattage is never reported as
measured power.

Native suspend stops both the host thermal daemon and its delayed-work lease
timer. A kernel PM notifier now reduces high FCC to 500 mA before freezing,
blocks racing increases, and aborts suspend if the reduction fails. Resume
invalidates board telemetry; the policy uses CLOCK_BOOTTIME to discard a ramp
across sleep. New high-current selection requires this kernel guard. Service
shutdown also relinquishes its high-current vote.

To maintain fast charging with the display off, an authenticated wired charging
session uses Armada's light-sleep handler. The host thermal loop stays active;
unplugging, reaching full charge or losing the charging service ends light
sleep and returns to the configured sleep mode. Battery-powered sleep remains
native by default. Direct native suspend is still protected by the kernel's
500 mA fallback. Wireless charging retains its separate conservative ceiling.

**Validation:** Charging light sleep kept the display blank and the current
policy running; an injected Power key woke the device. Stopping the charging
service during light sleep reduced a 4.6 A vote to 500 mA and transitioned into
native suspend, followed by a successful RTC wake. Cable-detach and full-charge
selection are covered by policy tests; those physical transitions still need
longer validation. The rebuilt root image includes both ceilings, the policy,
sleep handler and guarded charger module. Sustained 90 W charging is unverified.

### HyperCharge parity investigation

**Issue:** Restoring the configured ceiling and observing successful adapter
authentication does not establish stock-equivalent HyperCharge. The host policy
still falls back to 500 mA above a 38°C battery temperature, and the kernel also
enforces a conservative high-current temperature gate. Those bring-up limits
can dominate the stock thermal votes and reduce charging after the phone warms.

**Findings:** Re-extracted `mi_thermald`, `qti_battery_charger.ko`, thermal
configuration and ADSP firmware from OS3.0.303.0.WNCTWXM. The exact charger module
differs from the device repository's module, so function offsets must be taken
from the firmware actually used. IDA confirms that XM55 (`fastchg_mode_show` at
`0x10e90`) reports firmware state; it is not an enable switch. XM61 reports
advertised APDO power, while the stock `power_max_show` uses XM62. Neither is a
measurement of battery power.

The normal and global-normal thermal configurations contain identical charging
tables. The existing port matches their virtual-skin weights, wired mitigation
levels and SIC coefficients. IDA's `timer_expires_2` at `0xa7d64` confirms that
SIC uses the firmware FCC readback and temperature history, applies its
configured minimum/maximum, and runs when USB is online. The 15.6 A value is a
ceiling in that controller, not a request to charge continuously at that rate.

The firmware also confirms what that FCC interface controls. BAT SET property
12 reaches `battmngr_plat_xiaomi_sic_vote_for_fcc` at `0x600936a0` through
`0x602db7f0` and `0x6002cf60`, converting microamps to milliamps. It submits a
normal SIC vote to the `fcc_0` minimum aggregator; it does not use the separate
force-override operation. BAT GET property 12 follows `0x602db1f0` into
`0x60091660`: its property-12 branch reads the aggregator result through
`0x600aab80`, then converts milliamps back to microamps. Thus the feedback is
the resulting FCC limit, not merely an echo of the host request or a measurement
of actual battery current. This preserves other firmware voters, but does not
prove that every protection or charging transition behaves correctly on Armada.

The stock ADSP configuration sets fast-charge eligibility to 15–47°C and below
95% charge. The firmware's `battmngr_plat_xiaomi_set_fastchg_mode` at
`0x6008d010` checks those configuration fields before enabling the mode; the
JEITA path also checks adapter verification and whether fast charging has
already completed. Separate charge-pump, battery-health, voltage, connector
and thermal votes still determine the result. These eligibility bounds alone
do not justify replacing the port's temperature protections.

**Warm-charge observation:** The running build still has the original 38°C
host high-current gate; the proposed guarded 47°C range has not been flashed.
With the stock Xiaomi adapter authenticated and firmware `fastchg_mode=1`, a
read-only trace at 63–65% charge and about 37.5°C battery temperature measured
18.9–20.7 W at the battery. The FCC vote was 4.6–5.1 A, near the stock SIC
minimum for that virtual-skin range. At 70% charge, the battery reading rose
from 38.0 to 38.1°C and the host vote fell from about 4.6 A to 0.5 A. The
charging-service journal independently recorded that 500 mA cap. Firmware
still reported fast-charge mode and the wired mitigation level remained zero.
USB voltage subsequently stepped from about 17.8 V to 9 V and then 4–5 V;
the FCC vote ramped again as the battery cooled. This directly identifies the
port's 38°C gate as a cause of the abrupt slowdown in this run, without proving
the full stock charging curve or the charge-pump state. In particular,
`fastchg_mode=1` persisted through the voltage collapse, so that status alone
must not be interpreted as proof that the charge pump is operating.

IDA inspection of the exact stock `micharge` HAL shows its named
`getFastChargeModeStatus` method reads the status node; it has no corresponding
named fast-charge enable method. The stock charger module exposes charge-pump
mode as XM property 36 with read and write handlers, but this does not establish
when stock userspace or firmware changes it. Do not force that property based
on the status value alone. The stock ADSP function identified by its
`xm_is_force_exit_cp_mode` log string reads charge level and adapter identity,
branches at 25% and 94% charge, and logs a switch to PMIC at high charge. This
shows firmware participates in charge-pump mode decisions. The stock `cp_mode`
node is root-writable, while the `micharge` HAL runs as `system`; extracted
vendor and ODM init rules do not grant it write access. This supports a
firmware-managed transition, without ruling out another privileged host
request. [Xiaomi describes 90 W](https://www.mi.com/global/product/xiaomi-14/)
as the charger output rating, not a battery-power measurement. The phone's
USB-current node returned zero in this run, so actual input power was not
measured.

**High-charge observation:** A later read-only trace on the same installed
build followed the stock adapter from 87% to a displayed 100% at about
37.1–38.0°C. The effective FCC readback stayed at 4.279 A while measured
battery current fell from roughly 3.4 A to about 1.7 A; USB voltage stayed
near 9 V.
At the displayed 95% boundary, neither the fast-charge status bit nor USB
voltage changed abruptly. The installed kernel does not expose charge-pump
mode, and the firmware may use a different internal charge estimate, so this
trace cannot identify the physical pump transition. The phone still reported
`Charging` through the three-minute observation after the display reached
100%. A subsequent read-only check reported `Full` with zero battery current
while the FCC readback remained 4.279 A. A later 40-second trace still showed
`Full`, but battery current was 0.48–0.53 A and the effective FCC readback was
0.50–0.55 A. The host charging service remained active and logged a 3 A
request, so the lower limit came from another charging voter. The zero-current
snapshot does not establish a permanent stop; the late low-current behavior and
eventual stop need a longer observation.

The sleep selector therefore keeps the host thermal monitor running while
status is `Charging`, even at a displayed 100%, and releases it once charging
ends. This source change has not yet been installed on the phone.

The pending kernel patch also exposes read-only charge-pump mode, smart-charge
settings and raw gauge charge so a later hardware trial can identify which
stock controller is limiting the charge. The updated module compiles, but its
new diagnostic reads and 47°C range remain untested on the phone.

**Status:** Partial support. Authentication and increased charging current have
been observed, but the full stock charging curve, charge-pump transitions,
taper and thermal behavior remain under investigation. The proposed 47°C path
is covered by source tests but has not been installed or hardware validated.
Charging light sleep is a port workaround to retain host monitoring;
stock-equivalent suspend behavior is unverified.

## Speakers and microphone

**Issue:** The AudioReach TDM endpoint, clocks and board routes were missing.
Initial playback formats could crash ADSP; microphone streams were silent, and
one speaker channel was absent.

**Fix:** Add the TDM endpoint and clock support, use 48 kHz S32_LE stereo
end-to-end, and configure the tested four-slot timing. Slot mask 5 selects the
two I²S phases, with each CS35L41 amplifier routed through its protected DSP
path. Load the matching top/bottom coefficients and each phone's factory
resistance calibration before enabling output. Use the correct microphone
capture DAI, ADC startup order, low-power clock selection and SoundWire lane 1
when remapping ADC1 to master port 3. ALSA UCM exposes the speaker and microphone
to PipeWire. Both speaker channels and microphone capture passed acoustic tests.

## Bluetooth and vibration

**Issue:** UART/power wiring and PMIC haptic support were absent.

**Fix:** Add the stock board wiring and periodic force-feedback support.
Controller pairing and input were confirmed, as were short and long vibration
pulses. Bluetooth audio, calls and broad controller interoperability were not
part of those tests.

## GPS

**Issue:** The stock modem rejected generic QMI LOC registration and initially
reported sessions without satellite fixes.

**Fix:** Trace the stock client metadata, hold one QRTR client across the session,
start the firmware file service before MPSS, and map its MCFG requests to stock
firmware. Support UTC/orbit assistance and the expanded satellite-report format.
Satellite fixes were obtained with better sky visibility. Position and raw
uncertainty can travel over private Unix sockets without logging coordinates.
Use private, read-only-shadow NV snapshots rather than sharing or modifying
another handset's calibration.

**Remaining issue:** Restarting MPSS in the same boot caused a phone reboot in
one test. GPS remains an opt-in development feature, not an enabled GeoClue
provider. Receiver-reported uncertainty is not measured ground-truth accuracy.

## Normal USB, OTG and SuperSpeed

**Issue:** Development images exposed USB serial consoles. Early DWC3 startup
also timed out, and the temporary USB2 workaround prevented SuperSpeed.

**Fix:** Remove diagnostic gadgets and serial logins. Charging is the default;
Armada's MTP switch enables one file-transfer interface on demand. Wire the
Type-C graph and QMP PHY, its stock supply and cable-orientation GPIO, and let
ADSP UCSI own roles and VBUS. Remove the USB2-only clock override. Enable uMTP's
SuperSpeed descriptors and query FunctionFS for the negotiated packet size so
USB2 and USB3 termination both work. PC transfers passed at 5 Gbit/s in both
connector orientations, and USB2 fallback passed too.

**Issue:** A SHANWAN Android gamepad's Home button lived on a separate Consumer
Control interface; an initial generic axis mapping triggered Steam screenshots.

**Fix:** Combine its interfaces in InputPlumber, map Home to Guide, map Z/RZ to
the right stick, and GAS/BRAKE to triggers. Home then opened the Steam menu
normally. Powered host operation was verified with that gamepad. A Pixel 7a
webcam negotiated 5 Gbit/s and streamed raw and 1080p MJPEG video. Its UVC stream
occasionally flagged bad frames; the cause was not established. USB4 and
DisplayPort support are not claimed.

## Preparing a reusable build

The initial installers repacked earlier diagnostic images and assumed one
workspace layout, filesystem UUID and storage capacity. The publication cleanup
replaces those dependencies with explicit source/firmware inputs, a matching
kernel/module build and image-generation parameters. Runtime support is staged
into the image instead of relying on changes already installed on the owner's
phone. Historical diagnostics and private validation records are not distributed.

The cleanup reflash exposed a host fastboot failure while rewriting the AVB
flags. Packaging now applies the same two disable flags to the pinned stock
vbmeta image, and the installer validates the AVB header, bounds and flags
before any write. The installed image is then sent with an ordinary flash
command. The flag layout follows [Android's fastboot implementation](https://android.googlesource.com/platform/system/core/+/683f225ad4c259d0fe63d583e051840b12039d00/fastboot/fastboot.cpp).

The first standalone ramdisk omitted `/bin/sh`: its init script installed
BusyBox applets, but the kernel needed the shell interpreter before that script
could run. The phone rebooted repeatedly and returned to fastboot. Packaging
now includes the shell symlink directly. Installer preflight checks the CPIO
archive, executable init, shell link and static arm64 BusyBox before touching
the phone; a regression test covers the missing-interpreter case.

The next boot reached init but exposed a second omission: BusyBox lacked
`realpath`, required to locate the preserved home. That applet is now enabled.
Packaging executes the built BusyBox, using QEMU on non-arm64 hosts, to check
the required applets, shell syntax and builtins, large-file sizes and path
resolution before generating any images.

The corrected clean build booted from internal storage through the cleaned
installer's preserving-update path. It loaded the rebuilt kernel and modules,
the new EROFS root and the existing home through separate overlays. The owner
confirmed normal display and touch, then launched games. Both Plasma and Steam
sessions started; Wi-Fi negotiated two streams at 80 MHz, and PipeWire exposed
the speaker and microphone. Touch, sensors, Bluetooth and charging services
were active, the boot slot was marked successful, and no USB gadget was bound
by default. GPS, Bluetooth pairing, acoustic audio and USB host peripherals
were not retested during this cleanup verification.

The fresh sparse userdata filesystem passed local filesystem checks; the
physical reflash preserved the owner's data. Installer regression tests cover
the destructive write sequence separately. An initial Flatpak key-import error
cleared after clock synchronization and a setup retry; signed repository
metadata then loaded. The base image's controller-type startup service still
reports failure when no controller is attached.

An additional clean-build audit copied only the files intended for publication
into a separate snapshot and hid the original workspace. With empty build
output and compiler caching disabled, the full build downloaded its sources,
compiled the kernel and userspace, and generated all six flash images plus the
EROFS root. Only the pinned public OCI image download cache was reused; its
manifest was independently fetched without credentials and its digest verified.
The installer regression tests and final image validation also passed with the
original workspace hidden. No extracted firmware, source checkout or previous
build artifact from the porting workspace was required.
