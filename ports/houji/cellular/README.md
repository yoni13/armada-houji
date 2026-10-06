# Houji cellular bring-up

LTE/5G data support for the development handset, using stock modem firmware.
Physical SIM slot 1 and eSIM internet access, boot-time initialization, KDE
controls and RTC-driven native suspend/resume have been tested. Physical slot 2
is implemented but untested; dual simultaneous data subscriptions are not offered.

## Verified so far

- MPSS boots stock firmware; IPA exposes `rmnet_ipa0` and QMAP bearers.
- A downloaded eSIM profile can be enabled and provisioned as the primary GW
  subscription. ModemManager reports LTE/5G registration and packet attachment.
- The former Smart Transmit license fatal is resolved in live testing by
  relaying QTEELS service `0x423` to stock QTEE object `119`. The request and
  cryptographic response are passed unchanged. Stock SAR/Smart Transmit remains
  enabled.
- QTEE needs RPMB **reads** as well as the FS/GPFS listeners. Error 20 from the
  license manager disappeared when the read-only RPMB listener was available.
- Plasma Dialer and Spacebar were installed on the development phone.
- Physical slot 1 registered on LTE/5G and completed HTTPS with roaming disabled.
- The eSIM completed HTTPS on LTE with roaming enabled, including after switching
  from the physical SIM through KDE Settings.
- Native s2idle with an explicitly armed RTC alarm resumed automatically after
  about 47 seconds. Wi-Fi, modem discovery and a SIM-bound data connection
  recovered; HTTPS succeeded after wake for both SIM types.
- The corrected IPA module remained running for more than 30 minutes, including
  NetworkManager bearer activation/deactivation and an HTTPS transfer. The stock
  modem NV partitions still matched their pre-test hashes.
- Plasma's quick-settings backend was exercised with repeated and rapid data
  toggles and across native sleep. The user confirmed the SIM remained visible
  after wake and the status-bar switch worked in both directions. Data switched
  off before sleep stayed off after wake, and could then be enabled normally.

The first profile is a provisioning test profile. A second, data-enabled eSIM
has now completed an HTTPS request over LTE through NetworkManager, using only
a few kilobytes. A modem fatal seen before the IPA correction was
`ipa_hal.c:8962: IPA Assert: status == GSI_STATUS_SUCCESS failed`. Patch `0030`
corrects endpoint differences found in stock's SM8650 table and its IMEM fallback.
Its live-test module received data and remained running beyond the previous
failure window. Long-duration standby and physical slot 2 remain unverified.
Voice calls are not working in the current port: outgoing attempts reach the
modem but terminate before connection. On the tested physical SIM, the IMSA
client bound to subscription 0 reports not registered, and IMS settings report
registration disabled (voice support itself is enabled). CallAudioD also rejects
the current audio card as lacking suitable speaker/earpiece ports. IMS bring-up
and a modem voice-audio route remain to be implemented. SMS transport, VoLTE and
emergency calls are not validated.

## Architecture

1. `houji-cellular-prepare` mounts `modemfirmware_b` read-only and creates
   root-only private EFS files from `modemst1`, `modemst2`, `fsg`, and `fsc` once.
   It also copies the existing encrypted secure-storage files on the device.
2. `houji-qtee` serves FS/GPFS/time/TA-loading requests inside a private mount
   namespace. Secure-world file writes go to userdata copies. Its RPMB listener
   rejects data writes and permits only counter/read frame types on UFS.
3. `houji-license` verifies that the existing license store is readable, then
   publishes the QTEELS relay. It does not download, install, forge, or disable
   licenses. No license identifiers or opaque payloads are logged.
4. TFTP and RMTFS start before MPSS. RMTFS has neither `-P` nor `-s`: it uses
   private files, and service termination must not hot-stop the modem.
5. MPSS is started once, with automatic recovery disabled. A crash needs a normal
   phone reboot; hot MPSS stop/restart has reset the handset.
6. The SIM helper selects the USIM application as primary GW provisioning.
   NetworkManager and ModemManager then own registration, APNs, routing and data.
   ModemManager is patched to follow the primary GW slot when both slots are
   active, retain its netlink callback before freeing a transaction, count saved
   bearers without applying the wrong active limit, and rediscover QRTR control
   nodes after suspend.
7. ModemManagerQt updates its cached modem/SIM list before emitting removal
   signals. Plasma NM serializes profile saves, activation and disconnection,
   coalesces rapid toggles, and releases obsolete modem models after observers
   have updated. This prevents a false "No SIM inserted" after resume.

The source tree, image and update archives must never contain EFS files, secure
storage, IMEI, EID, ICCID, activation codes, or subscriber connection profiles.

## SIM and eSIM controls

The service starts automatically at boot. CLI equivalents of the Settings controls:

```
sudo houji-cellular enable
sudo houji-cellular sim physical1
sudo houji-cellular sim physical2
sudo houji-cellular sim esim
sudo houji-cellular esim profile list
```

Only one primary data subscription is configured. Slot 2 is shared between the
second nano-SIM and the eUICC. Automatic selection prefers slot 1, then a visible
physical slot-2 card, otherwise the eUICC. Switching disconnects mobile data.

The **SIM Cards and eSIM** module in KDE Settings exposes selection, profile
download/list/enable/disable/delete, and links to KDE's **Cellular Network** page
for mobile data, APNs, roaming and operator selection. The D-Bus backend permits
only active local sessions through polkit and serializes operations. GUI
activation codes are sent over D-Bus, then via stdin to lpac; they are never
placed in command arguments or daemon logs. On-device testing confirmed both
Settings pages, switching back to eSIM, and listing the installed profiles.
Use SIM-bound NetworkManager profiles to prevent an APN intended for one SIM
from automatically activating on another. Roaming is a per-connection choice;
the tested physical SIM used home-only data, while the travel eSIM allowed roaming.

`lpac` is pinned to v2.3.0 with QMI_QRTR and curl, plus stdin activation-code
input and per-process HTTP connection/cookie retention. The latter resolved
SM-DP+ "Unknown transaction" and "Verification Failed" responses during a real
profile download. Cookies remain in memory and are discarded on exit. Always use
`LPAC_CUSTOM_ES10X_MSS=255`: this eUICC rejects the initial secure-channel segment
when split at lpac's default 120-byte size. Its curl backend disables TLS peer
verification; GSMA authentication is performed by the eUICC. Use trusted carrier
activation codes. The wrapper sets the slot and APDU size automatically.

The eUICC shares the SN220 NFC controller's VEN supply. Kernel patch `0029`
retains that supply while the NFC radio is off; userspace NFC emulation also
preserves it. The saved NFC emulation mode ran alongside the successful cellular
tests. Scanning a tag while a data transfer is active remains untested.

## Build and remaining validation

`build.py` uses pinned upstream revisions and SHA-256-checked RPM headers. It
builds the QTEE libraries, relay shim, lpac, arm64 KCM, the WWAN plugins
matching the pinned Armada NetworkManager, patched ModemManager and KDE cellular
libraries. A native regression test calls the real patched ModemManagerQt code
with re-entrant observers; it fails on the old removal order. The pinned
target Qt generators run natively on arm64 or through qemu-user on x86. CMake,
Meson, Ninja, patchelf, qemu-user-static and the aarch64 toolchain are required. The main port builder
invokes it. Standard Armada builds now include ModemManager, WWAN, provider
information, Plasma Dialer and Spacebar. The pinned legacy OCI base is augmented
with checksum-locked runtime RPMs using its own RPM tool under QEMU. Dependency
checks remain enabled; package scriptlets and services are not run while staging.
The complete staging step and systemd unit verification passed on this base.

The preserving-update image was flashed and booted; follow-up userspace fixes
were tested live and are included in the source build. Remaining coverage is
physical slot 2, more carriers and long-duration standby. No throughput test was
performed against the small prepaid allowance.

### RTC behavior in local and GitHub builds

An explicitly armed RTC alarm is a normal Linux wake source. The native sleep
loop respects its deadline even if a simultaneous sensor-DSP wake obscures the
RTC interrupt in `pm_wakeup_irq`. It reads the RTC's own epoch rather than UTC.
It **does not schedule alarms**. The 45-second test alarm, temporary post-wake
hook and sleep inhibitors are development-only and are absent from both local
release images and GitHub Actions builds.
