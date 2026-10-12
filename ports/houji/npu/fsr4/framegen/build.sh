#!/usr/bin/env bash
# Build the experimental frame-generation layer for the handset (aarch64).
# Usage: build.sh OUT_DIR   (needs DXC, VULKAN_INCLUDE; CC defaults to aarch64-linux-gnu-gcc)
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
out=$(realpath "${1:?usage: build.sh OUT_DIR}")
: "${DXC:?Linux DXC executable required}"
: "${VULKAN_INCLUDE:?directory containing vulkan/ headers}"
mkdir -p "$out"
"$DXC" -spirv -T cs_6_6 -E main -O3 -fspv-target-env=vulkan1.1 "$here/blend.hlsl" -Fo "$out/blend.spv"
spirv-val --target-env vulkan1.1 "$out/blend.spv"
python3 - "$out/blend.spv" "$out/blend.spv.h" <<'EOF'
import struct, sys
data = open(sys.argv[1], 'rb').read()
words = struct.unpack(f'<{len(data)//4}I', data)
body = ',\n'.join(', '.join(f'0x{w:08x}' for w in words[i:i+8]) for i in range(0, len(words), 8))
open(sys.argv[2], 'w').write(f'static const uint32_t blend_spv[] = {{\n{body}\n}};\n')
EOF
"${CC:-aarch64-linux-gnu-gcc}" -std=gnu11 -O2 -Wall -Wextra -Wno-missing-field-initializers -Werror -fPIC -shared -fvisibility=hidden \
    -I"$VULKAN_INCLUDE" -I"$out" "$here/houji-framegen-layer.c" -lpthread -o "$out/libVkLayer_houji_framegen.so"
# The layer runs against the host glibc inside Steam's container; refuse symbols newer than 2.34.
newest=$(objdump -T "$out/libVkLayer_houji_framegen.so" | grep -oE 'GLIBC_2\.[0-9]+' | sort -V | tail -1)
case "$newest" in GLIBC_2.1[0-9]|GLIBC_2.2[0-9]|GLIBC_2.3[0-4]|"") ;; *) echo "glibc requirement too new: $newest" >&2; exit 1;; esac
cp "$here/VkLayer_houji_framegen.json" "$out/"
echo "built $out/libVkLayer_houji_framegen.so (needs $newest)"
