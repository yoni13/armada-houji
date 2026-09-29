#!/usr/bin/env bash
# Exercise the Houji display/USB sleep transaction against temporary sysfs files.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
python3 - "$ROOT" "$work" <<'PY'
from pathlib import Path
import sys
root, work = map(Path, sys.argv[1:])
s = (root/'system_files/usr/libexec/armada/fake-suspend').read_text()
# Load functions without invoking the entry point or touching the host's devices.
s = s.split('case "${1:-sleep}" in', 1)[0]
s = s.replace('source /usr/lib/armada/input-lib', ':')
s = s.replace('eval "$(/usr/libexec/armada/device-env)"', ':')
s = s.replace('/run/armada/', str(work/'run')+'/')
s = s.replace('/sys/class/backlight/', str(work/'backlight')+'/')
s = s.replace('/sys/devices/platform/soc@0/', str(work/'soc')+'/')
(work/'functions').write_text(s)
PY
source "$work/functions"
log() { printf '%s\n' "$*" >>"$work/log"; }
mkdir -p "$SAVE_DIR" "$work/backlight/houji-n3" "$work/soc/a600000.usb/power"
bl="$work/backlight/houji-n3/bl_power"
usb="$work/soc/a600000.usb/power/control"
# Any accidental full display shutdown makes this test fail.
each_gamescope() { printf '%s\n' "$*" >>"$work/drm-calls"; return 0; }
ARMADA_FAKE_SUSPEND_DISPLAY_MODE=backlight
ARMADA_PRIMARY_BACKLIGHT=houji-n3
ARMADA_FAKE_SUSPEND_USB_RUNTIME_PM=on
for initial in 0 1 4; do
    printf '%s\n' "$initial" >"$bl"
    display_off
    [[ $(<"$bl") == 4 ]]
    display_on
    [[ $(<"$bl") == "$initial" ]]
    [[ ! -e "$work/drm-calls" ]]
done
for initial in auto on; do
    printf '%s\n' "$initial" >"$usb"
    suspend_runtime_pm
    [[ $(<"$usb") == on ]]
    resume_runtime_pm
    [[ $(<"$usb") == "$initial" ]]
done
# Failure to blank must unwind the input grabs and state, not enter sleep.
rm -f "$SAVE_DIR/backlight-power"
ARMADA_PRIMARY_BACKLIGHT=missing
mute_audio() { :; }; unmute_audio() { :; }; power_device() { :; }
block_input() { touch "$work/grabbed"; }
unblock_input() { rm -f "$work/grabbed"; }
unfreeze_processes() { :; }
freeze_processes() { touch "$work/froze"; }
timeout() { :; }
if (do_suspend); then echo 'Missing backlight accepted' >&2; exit 1; fi
[[ ! -e "$STATE_FLAG" && ! -e "$work/grabbed" && ! -e "$work/froze" && ! -e "$work/drm-calls" ]]
# Other devices retain the existing DRM-off/on policy.
unset ARMADA_FAKE_SUSPEND_DISPLAY_MODE
mkdir -p "$SAVE_DIR"
display_off; display_on
[[ $(cat "$work/drm-calls") == $'gamescopectl drm_sleep_internal_screen 1\ngamescopectl drm_sleep_internal_screen 0' ]]
unset ARMADA_FAKE_SUSPEND_USB_RUNTIME_PM
printf 'on\n' >"$usb"
suspend_runtime_pm
[[ $(<"$usb") == auto ]]
resume_runtime_pm
[[ $(<"$usb") == on ]]
# Confirm the selected device profile actually exposes the options.
env ARMADA_DEVICE_DIR="$ROOT/system_files/usr/lib/armada/devices" \
    ARMADA_MODEL='Xiaomi 14' ARMADA_SLEEP_CONFIG="$work/no-config" \
    ARMADA_MEM_SLEEP_PATH="$work/no-mem-sleep" \
    bash "$ROOT/system_files/usr/libexec/armada/device-env" >"$work/profile"
grep -qx 'ARMADA_FAKE_SUSPEND_DISPLAY_MODE=backlight' "$work/profile"
grep -qx 'ARMADA_FAKE_SUSPEND_USB_RUNTIME_PM=on' "$work/profile"
printf 'Houji fake suspend tests passed\n'
