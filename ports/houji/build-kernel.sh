#!/usr/bin/env bash
# Build the complete Houji kernel and modules in an isolated source tree.
set -euo pipefail
port=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$port/../.." && pwd)
work=${HOUJI_WORK_DIR:-"$root/output/houji/work"}
out=${HOUJI_OUT_DIR:-"$root/output/houji"}
mkdir -p "$work" "$out"
work=$(realpath "$work")
out=$(realpath "$out")
source "$root/packages/kernel/BASE.env"
[[ "$VERSION" == 7.2.3 ]] || { echo 'Houji bring-up currently targets Linux 7.2.3' >&2; exit 1; }
archive="$work/kernel/linux-$VERSION.tar.xz"
mkdir -p "$work/kernel"
if [[ ! -f "$archive" ]]; then
    curl -fL --retry 3 "https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-$VERSION.tar.xz" -o "$archive.part"
    echo "8ba259e8e7b13ec6ef0941c8a39ad90b24bd4a4d6c0010ba6bafb794550ecd03  $archive.part" | sha256sum -c -
    mv "$archive.part" "$archive"
fi
echo "8ba259e8e7b13ec6ef0941c8a39ad90b24bd4a4d6c0010ba6bafb794550ecd03  $archive" | sha256sum -c -

# Do not edit the shared package or put Houji in supported-dtbs.
package="$work/kernel-package"
mkdir -p "$package"
cp -a "$root/packages/kernel/." "$package/"
while IFS= read -r patch; do
    [[ -n "$patch" && "$patch" != \#* ]] || continue
    cp "$port/patches/$patch" "$package/patches/"
    echo "$patch" >>"$package/patches/series"
done < "$port/patches/series"
cp "$port/sm8650-xiaomi-houji.dts" "$package/dts/"
cp "$port/bluetooth-haptics.dtsi" "$package/dts/"
cp "$port/charging/board-thermals.dtsi" "$package/dts/"
cp "$port/usb/otg-usb3.dtsi" "$package/dts/"
cp "$port/touch/touch.dtsi" "$package/dts/"
cp "$port/audio/"*.dtsi "$package/dts/"
cat "$port/bringup.config" >>"$package/config/armada-kernel.config.overrides"
# Merge duplicate settings before the shared builder validates the fragment.
python3 - "$package/config/armada-kernel.config.overrides" <<'PYCONFIG'
from pathlib import Path
import re, sys
path = Path(sys.argv[1])
settings = {}
for line in path.read_text().splitlines():
    match = re.match(r'(?:# )?(CONFIG_[A-Z0-9_]+)(?:=| is not set)', line)
    if match:
        settings[match[1]] = line
path.write_text('\n'.join(settings.values()) + '\n')
PYCONFIG

# Stock ABL applies firmware-supplied overlays as well as dtbo.img. Keep the
# base-tree symbols and their phandles available for those external fixups.
DTC_FLAGS="${DTC_FLAGS:+$DTC_FLAGS }-@" KERNEL_VERSION="$VERSION" WORK_DIR="$work/kernel" OUT_DIR="$out" bash "$package/scripts/build-kernel.sh"
source "$package/BASE.env"
cp "$work/kernel/linux-$VERSION/arch/arm64/boot/Image" "$out/Image"
cp "$work/kernel/linux-$VERSION/arch/arm64/boot/dts/qcom/sm8650-xiaomi-houji.dtb" "$out/"
cp "$work/kernel/linux-$VERSION/.config" "$out/kernel.config"
