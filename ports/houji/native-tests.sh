#!/usr/bin/env bash
# Build and run Houji's C and C++ unit tests.
#
#   ports/houji/check-patches.py userspace --keep DIR
#   ports/houji/native-tests.sh DIR
#
# DIR holds the pinned, patched hexagonrpc and iio-sensor-proxy trees that
# check-patches.py leaves behind. The tests exercise the patched sources
# themselves, so they must be built from those trees, not from the repository.
#
# Needs: gcc, g++, python3, cmake, dbus-run-session, pkg-config and the development
# files for Qt6 (Core, DBus, Xml), ModemManager, glib, gio-unix, gudev,
# libqmi-glib and libqrtr-glib. Everything else is built in a
# temporary directory.
set -euo pipefail

port=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$port/../.." && pwd)
trees=${1:?usage: native-tests.sh PATCHED_TREES_DIR}
trees=$(realpath "$trees")
hex=$trees/hexagonrpc
cc=${CC:-gcc}
cxx=${CXX:-g++}

for tree in hexagonrpc iio-sensor-proxy modemmanager-qt; do
    [[ -d $trees/$tree ]] || { echo "missing patched tree: $trees/$tree" >&2; exit 2; }
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

step() { printf '\n== %s\n' "$1"; }

step 'sensors: registry writes, appends and rename protection'
"$cc" -Wall -Werror -I"$hex/hexagonrpcd" -I"$hex/include" -o "$work/registry" \
    "$port/sensors/test-registry-rename.c" \
    "$hex/hexagonrpcd/hexagonfs.c" "$hex/hexagonrpcd/hexagonfs_mapped.c" \
    "$hex/hexagonrpcd/hexagonfs_virt_dir.c"
"$work/registry"

step 'sensors: an unsupported RPC gets an error reply and the service keeps going'
"$cc" -Wall -Werror -I"$hex/include" -I"$hex/hexagonrpcd" -o "$work/listener" \
    "$port/sensors/test-listener-reply.c" \
    "$hex/hexagonrpcd/listener.c" "$hex/hexagonrpcd/iobuffer.c" \
    "$hex/hexagonrpcd/interface/adsp_listener.c"
"$work/listener"

step 'sensors: front light lux formula, SSC reports and Xiaomi OEM messages'
"$cc" -Wall -Wextra -Werror -o "$work/als-math" "$port/sensors/test-als-math.c" -lm
"$work/als-math"

step 'sensors: iio-sensor-proxy SSC driver stops its measurement on close'
python3 "$root/tests/houji-ssc-close-test.py" "$trees/iio-sensor-proxy"

step 'charging: battery percentage stays near the gauge on plug-in, unplug and full'
# The function under test is taken from kernel patch 0028 itself.
awk '/^\+#define HOUJI_CHARGING_CAPACITY_MARGIN/ {on=1} on {line=$0; sub(/^\+/, "", line); print line} on && /^\+\}$/ {exit}' \
    "$port/patches/0028-houji-capacity-tracks-gauge.patch" > "$work/houji-capacity.inc"
[[ -s $work/houji-capacity.inc ]] || { echo 'houji_capacity() not found in patch 0028' >&2; exit 1; }
"$cc" -Wall -Wextra -Werror -DHOUJI_CAPACITY_INC="\"$work/houji-capacity.inc\"" \
    -o "$work/capacity-clamp" "$port/charging/test-capacity-clamp.c"
"$work/capacity-clamp"

step 'gamescope: wake waits for a fresh frame'
"$cxx" -std=c++17 -Wall -Werror -o "$work/frame-wake" "$root/tests/houji-frame-wake-test.cpp"
"$work/frame-wake"

gps_flags=$(pkg-config --cflags --libs qmi-glib qrtr-glib gio-unix-2.0)
for test in test-sv-parser test-position-frame; do
    step "gps: $test"
    # shellcheck disable=SC2086
    "$cc" -Wall -Werror=implicit-function-declaration -o "$work/$test" \
        "$port/gps/$test.c" $gps_flags
    "$work/$test"
done

step 'cellular UI: re-entrant modem and SIM lifecycle notifications'
bash "$port/cellular/test-ui-native.sh" "$trees/modemmanager-qt"

printf '\nAll native tests passed\n'
