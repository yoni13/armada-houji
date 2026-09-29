# Armada on Xiaomi 14 (houji)

Experimental Xiaomi 14 port using the **stock, unlocked Xiaomi ABL**. It boots
Armada from internal userdata with Plasma Mobile and Steam Game Mode. It does
not use Armada's modified ABL installation path for supported handhelds.

Most of this port was implemented and debugged by an AI coding agent, with the
owner testing the physical phone. Treat it as an experimental community port;
review the changes before installing. Investigation notes and fixes are in
[HISTORY.md](HISTORY.md).

## Hardware status

These results describe the development handset. A newly built image still needs
its own hardware validation; a successful build does not establish hardware support.

| Feature | Status and limitations |
| --- | --- |
| Internal boot | Working with stock ABL, unlocked bootloader, slot B. No USB root server or automatic reboot timer. |
| Plasma Mobile / Steam Game Mode | Both launch with GPU rendering; session switching works. Game compatibility depends on Armada's translation stack. |
| Display | N3 panel, 1200 × 2670, fixed 120 Hz target. Kernel vblank measurements were about 119 Hz. RGB format correction fixes the gross pink tint; no color calibration claim. |
| Touch | Stock Xiaomi processing core with a Linux transport and uinput. Taps and swipes work. |
| Rotation | Sensor-driven Plasma and Game Mode rotation. Plasma touch alignment tested; recheck touch in all Game Mode orientations after installing. Gyro aiming is not implemented. |
| Wi-Fi | WCN7850, 2.4 and 5 GHz; two streams and 80 MHz tested. Local iperf reached about 509 Mb/s down and 424 Mb/s up on the tested AP. Rates depend on signal and AP settings. |
| Bluetooth | Controller pairing and control tested. Audio profiles and other accessories untested. |
| Haptics | Short and long vibration effects tested. Application integration varies. |
| Speakers / microphone | Stereo playback and microphone recording tested, using the handset's factory speaker calibration. |
| Battery / wired charging | Battery telemetry, Steam estimates and conservative USB-PD charging work. Computer USB may supply less than the running system consumes. Full Xiaomi 90 W charging is not supported or validated. |
| Wireless charging | Starts and then stops in testing; unresolved. |
| Sleep | OLED blanking workaround. Deep suspend/resume is unreliable, so standby power is higher than Android. |
| USB device | Charging by default; MTP on demand through Armada's switch. USB 3 at 5 Gb/s tested in both connector orientations, with USB 2 fallback. No USB shell or network gadget. |
| USB host / OTG | Wired gamepad and Pixel webcam tested, including 5 Gb/s webcam transport. No USB 3 storage-drive test; USB4/DisplayPort support is not claimed. |
| GPS | A satellite fix was verified with the opt-in development tools. No default GeoClue provider, navigation integration or location logger. Modem restart during testing remains unreliable. |
| Cellular / internal cameras / NFC / fingerprint | Not implemented or validated. A GPS fix does not establish cellular service. |

Only the development handset's N3 panel and storage variant were tested. Other
panel revisions, capacities and regional firmware combinations need validation.
The installer checks capacity, rather than requiring a particular storage size.

## Firmware and boot layout

The reference firmware is **OS3.0.303.0.WNCTWXM**, Taiwan, from Xiaomi's
[public fastboot archive](https://bigota.d.miui.com/OS3.0.303.0.WNCTWXM/houji_tw_global_images_OS3.0.303.0.WNCTWXM_20260727.0000.00_16.0_tw_9ffae02e56.tgz).
The archive SHA-256 and all source revisions are pinned in [sources.json](sources.json).
Required extracted firmware is included under [firmware/](firmware/README.md).
It contains stock defaults, not another phone's calibration, saved connections,
Bluetooth pairing keys or location data.

The build uses Linux 7.2.3 plus the patches in `patches/series`, and a pinned
Armada arm64 OCI image. It compiles the port's touch, sensor, charging, Gamescope,
MTP, audio and GPS components from the supplied sources and pinned dependencies.

`boot_b` holds the Linux kernel; `init_boot_b` holds the early-boot ramdisk;
`vendor_boot_b` holds the mainline device tree. `dtbo_b` retains Xiaomi's
selection fields with a no-op overlay. `vbmeta_b` is packaged with verification
disabled. **No ABL, partition table or stock firmware partition is flashed.**

Xiaomi's ABL also requires nonzero boot version metadata even when unlocked.
The images retain the stock compatibility values (Android 14 / 2026-02-01).
Those values describe the bootloader contract, not Armada's security patch level.

Userdata contains an ext4 filesystem with a read-only EROFS system image and a
writable overlay. First boot grows ext4 to the partition's available capacity.
Stock `modem_b`, `dsp_b` and `persist` are read for ADSP firmware, DSP libraries
and this handset's calibration. Ext4 firmware/calibration mounts use `ro,noload`.
GPS additionally needs the handset's stock modem firmware and NV data; these
partitions must not be erased or replaced with files from someone else's phone.

## Build

Use a Linux host with rootless Podman configured, at least 80 GiB of free build
space and an arm64 cross toolchain. The scripts use these host tools:

- Python 3.11 or newer, including `libfdt`; Bash, Git, curl, tar, patch, rsync,
  Perl, bc and kmod (`depmod`).
- On non-arm64 hosts, QEMU arm64 user emulation (`qemu-aarch64-static` or
  `qemu-aarch64`) to check the early-boot commands before packaging.
- `aarch64-linux-gnu-gcc`, `g++`, binutils; make, Meson, Ninja, pkg-config, patchelf,
  ccache, flex, bison, OpenSSL development headers, ELF development headers and pahole.
- `dtc`, `fdtget`, `fdtput`, `fdtoverlay`; `wayland-scanner`, GLib code generators,
  protobuf-c compiler, glslangValidator, m4 and the host ALSA topology library.
- `bsdtar`, zstd, `mkfs.erofs`, `fsck.erofs`, e2fsprogs and Android sparse-image tools
  (`img2simg`, `simg2img`); Android platform-tools for flashing.

The build fetches pinned source revisions, verifies pinned RPM and firmware
hashes, and links against libraries from an **unstarted public Armada image**.
It does not need a phone or the original porting workspace.

No sibling `build/`, `firmware/` or `sources/` directory is required. A normal
build uses the firmware already included in `ports/houji/firmware/` and downloads
the pinned public sources itself. The full HyperOS archive is needed only if
you choose to regenerate those bundled firmware files.

From the fork's root:

```sh
python3 ports/houji/build.py
```

Outputs are under `output/houji/` (ignored by Git). The complete flash bundle is
`output/houji/images/`. Build work and download caches stay under
`output/houji/work/`. To reuse an already completed kernel while rebuilding
userspace, use `--skip-kernel`. To choose another output directory, use `--output`.
`--image` accepts an arm64 Armada OCI image built from your fork; changing it may
also require updating the pinned development dependencies.

The default command builds the kernel, all port userspace, the root filesystem,
the boot images and sparse userdata. `--skip-userdata` omits only the destructive
fresh-install image, for an existing installation's preserving update.

To regenerate the bundled firmware from the exact pinned archive:

```sh
python3 ports/houji/firmware.py /path/to/houji-firmware.tgz output/houji/extracted-firmware
```

This regeneration additionally needs `lpunpack`, `mcopy` and roughly 20 GiB of
temporary space. `--scratch /path/to/temp` selects that space. The extractor
only reads allowlisted files and verifies the archive before extraction.

## Fresh installation — erases userdata

Back up Android data and know how to return to Xiaomi fastboot. Start from the
reference stock firmware with an unlocked bootloader. Connect the phone to the
host in **bootloader fastboot**, not fastbootd. The installer checks the product,
unlock state, pending snapshots, image checksums, boot headers and capacities
before any write. Never relock the bootloader with these images installed.

```sh
fastboot devices
python3 output/houji/images/flash-internal.py \
  --images-dir output/houji/images --serial YOUR_FASTBOOT_SERIAL --check-only
python3 output/houji/images/flash-internal.py \
  --images-dir output/houji/images --serial YOUR_FASTBOOT_SERIAL --erase-userdata
```

`--erase-userdata` explicitly authorizes losing **all Android/user files**.
The installer writes userdata first, then the boot components, activates slot B
and reboots. First boot should reach Plasma Mobile. Configure Wi-Fi through the
normal UI. Use the session switch to enter Steam Game Mode.

The pinned Armada image can report `armada-controller-type.service` failed
when no controller is attached; this does not prevent touch or session startup.
If initial Flatpak setup fails before the clock synchronizes, connect to Wi-Fi,
wait for the correct time, then run
`sudo systemctl restart armada-flatpak-setup.service`.

For reference, the write sequence after preflight is:

```sh
fastboot -s YOUR_FASTBOOT_SERIAL erase userdata
fastboot -s YOUR_FASTBOOT_SERIAL flash userdata output/houji/images/userdata.img
fastboot -s YOUR_FASTBOOT_SERIAL flash boot_b output/houji/images/boot.img
fastboot -s YOUR_FASTBOOT_SERIAL flash init_boot_b output/houji/images/init_boot.img
fastboot -s YOUR_FASTBOOT_SERIAL flash vendor_boot_b output/houji/images/vendor_boot.img
fastboot -s YOUR_FASTBOOT_SERIAL flash dtbo_b output/houji/images/dtbo.img
fastboot -s YOUR_FASTBOOT_SERIAL flash vbmeta_b output/houji/images/vbmeta.img
fastboot -s YOUR_FASTBOOT_SERIAL set_active b
fastboot -s YOUR_FASTBOOT_SERIAL reboot
```

Prefer the installer: the raw commands bypass its checks. Do not use another
Armada device's ABL scripts, format `persist`, or erase modem NV partitions.

## Updating an installed Armada system without erasing games

Build the new bundle, optionally with `--skip-userdata`. Copy `rootfs.erofs`,
`images.json` and `ports/houji/install/stage-update.py` to a temporary directory
on the phone through an owner-enabled transfer method. On the phone, run:

```sh
sudo python3 /path/to/update/stage-update.py /path/to/update \
  --serial YOUR_FASTBOOT_SERIAL
```

This verifies the root image, creates a clean system overlay, preserves the
existing home and selected owner settings, and leaves the old root available
for rollback. It does not transfer personal settings into the public bundle.
Copy its `staged-receipt.json` back to the host, then enter bootloader fastboot:

```sh
python3 output/houji/images/flash-internal.py \
  --images-dir output/houji/images --serial YOUR_FASTBOOT_SERIAL \
  --boot-only --staged-receipt /path/to/staged-receipt.json --check-only
python3 output/houji/images/flash-internal.py \
  --images-dir output/houji/images --serial YOUR_FASTBOOT_SERIAL \
  --boot-only --staged-receipt /path/to/staged-receipt.json
```

Keep the previous boot bundle until the new system is verified. A boot-only
flash without staging the matching root image is rejected. Ordinary bootc OTA
updates are not supported by this custom layout.

## USB and privacy

USB starts in charging mode. Enable file transfer using Armada's MTP switch;
OTG role detection remains available for wired peripherals. No automatic SSH,
ADB, serial login, network gadget or test-network connection is added by this
port. Remote access must be enabled by the owner.

GPS is opt-in and is not started at boot. Its client reports fix validity and
satellite counts without logging coordinates; position data is only sent through
an explicitly requested private output channel. Do not publish runtime logs,
recordings, modem NV, factory calibration, SSH keys or saved network profiles.
