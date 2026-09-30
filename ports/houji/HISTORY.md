# Houji porting history

This is a technical summary of the work through October 2026. Current status,
build instructions and installation instructions are in [README.md](README.md).
Development image names, device identifiers, network profiles, coordinates,
recordings and raw logs are intentionally omitted. Most investigation and
implementation was performed by an AI agent, with hardware observations and
supervised tests supplied by the device owner.

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
