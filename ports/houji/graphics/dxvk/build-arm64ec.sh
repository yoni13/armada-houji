#!/usr/bin/env bash
# Offline, pinned DXGI-only build. Fetch source/submodules with WARP beforehand.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ $# != 2 || -z ${LLVM_MINGW:-} ]]; then
    echo "Usage: LLVM_MINGW=/path/to/llvm-mingw $0 DXVK_SOURCE BUILD_DIRECTORY" >&2
    exit 2
fi
SOURCE=$(realpath "$1")
mkdir -p "$2"
BUILD=$(realpath "$2")
LLVM_MINGW=$(realpath "$LLVM_MINGW")
REV=a78a467cadb22bee928904290cf8e7921b121851
[[ $(git -C "$SOURCE" rev-parse HEAD) == "$REV" ]] || {
    echo "Expected DXVK revision $REV" >&2; exit 1;
}
for sub in include/spirv include/vulkan subprojects/dxbc-spirv subprojects/libdisplay-info \
           subprojects/dxbc-spirv/submodules/spirv_headers; do
    [[ -f "$SOURCE/$sub/.git" ]] || {
        echo "Initialize pinned submodule $sub before the offline build" >&2; exit 1;
    }
done
if git -C "$SOURCE" submodule status --recursive | grep -Eq '^\+'; then
    echo "Submodule checkout differs from its pinned revision" >&2
    exit 1
fi
PATCH="$HERE/0001-dxgi-make-swapchain-dispatcher-hookable-on-arm64ec.patch"
if ! git -C "$SOURCE" apply --reverse --check "$PATCH" 2>/dev/null; then
    git -C "$SOURCE" apply --check "$PATCH"
    git -C "$SOURCE" apply "$PATCH"
fi
# Restrict generated Meson string values to ordinary path characters.
[[ "$LLVM_MINGW" != *"'"* && "$LLVM_MINGW" != *$'\n'* && "$LLVM_MINGW" != *\\* ]]
cat > "$BUILD/arm64ec-cross.ini" <<EOF
[binaries]
c = '$LLVM_MINGW/bin/arm64ec-w64-mingw32-clang'
cpp = '$LLVM_MINGW/bin/arm64ec-w64-mingw32-clang++'
ar = '$LLVM_MINGW/bin/llvm-ar'
strip = '$LLVM_MINGW/bin/llvm-strip'
windres = '$LLVM_MINGW/bin/arm64ec-w64-mingw32-windres'
[properties]
needs_exe_wrapper = true
[host_machine]
system = 'windows'
cpu_family = 'aarch64'
cpu = 'arm64ec'
endian = 'little'
EOF
args=()
[[ ! -f "$BUILD/build.ninja" ]] || args+=(--reconfigure)
meson setup "${args[@]}" "$BUILD" "$SOURCE" --wrap-mode=nodownload \
    --cross-file "$BUILD/arm64ec-cross.ini" --buildtype=release \
    -Denable_d3d8=false -Denable_d3d9=false -Denable_d3d10=false \
    -Denable_d3d11=false -Dbuild_id=true
ninja -C "$BUILD" -j "${JOBS:-8}" src/dxgi/dxgi.dll
python3 "$HERE/verify-entrypoints.py" "$BUILD/src/dxgi/dxgi.dll" \
    --nm "$LLVM_MINGW/bin/llvm-nm"
sha256sum "$BUILD/src/dxgi/dxgi.dll" > "$BUILD/dxgi.sha256"
git -C "$SOURCE" submodule status --recursive > "$BUILD/submodules.txt"
git -C "$SOURCE" diff --binary > "$BUILD/source.patch"
"$LLVM_MINGW/bin/arm64ec-w64-mingw32-clang" --version > "$BUILD/compiler.txt"
