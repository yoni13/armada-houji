# Houji porting history

This is a technical summary of the work through September 2026. Current status,
build instructions and installation instructions are in [README.md](README.md).
Development image names, device identifiers, network profiles, coordinates,
recordings and raw logs are intentionally omitted. Most investigation and
implementation was performed by an AI agent, with hardware observations and
supervised tests supplied by the device owner.

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

**Remaining issue:** Full display power cycling sometimes caused static or a
reboot. The current light-sleep workaround blanks the OLED while retaining
panel/controller state and USB runtime power. It is not validated deep suspend
and has higher standby consumption.

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
