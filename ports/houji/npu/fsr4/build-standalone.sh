#!/usr/bin/env bash
# Reproducible native ARM64 test pipeline. Model/runtime inputs stay outside git.
set -euo pipefail
port=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
work=$(realpath "${1:?usage: build-standalone.sh WORK WIDTH HEIGHT [mixed|a16|a8]}")
width=${2:?output width required}
height=${3:?output height required}
variant=${4:-a16}
: "${QAIRT_SDK_ROOT:?extracted QAIRT SDK directory required}"
: "${FSR4_PYTHON:?Python interpreter with numpy/onnx/onnxruntime}"
: "${DXC:?Linux DXC executable required}"
: "${VULKAN_INCLUDE:?directory containing vulkan/ and vk_video/ headers}"
: "${VULKAN_LIBDIR:?ARM64 directory containing libvulkan.so.1}"
case "$variant" in
    mixed) flags=();;
    a16) flags=(--int16-residual);;
    a8) flags=(--int8-residual);;
    *) echo 'Unknown variant' >&2; exit 2;;
esac
out="$work/standalone-${width}x${height}-$variant"
mkdir -p "$out"
"$FSR4_PYTHON" "$port/build-model.py" "$work" --width "$((width/2))" --height "$((height/2))" --output "$out/model.onnx"
"$FSR4_PYTHON" "$port/generate-qnn.py" "$out/model.onnx" "$out/graph.h" "${flags[@]}"
"$FSR4_PYTHON" "$port/compile-stages.py" "$work/amd" "$out/shaders" --dxc "$DXC" --width "$width" --height "$height"
"$FSR4_PYTHON" "$port/generate-frames.py" "$out/frames" --width "$width" --height "$height"
"${CC:-aarch64-linux-gnu-gcc}" -O2 -Wall -Wextra -Werror \
    -I"$QAIRT_SDK_ROOT/include/QNN" "-DFSR_GRAPH_HEADER=\"$out/graph.h\"" \
    -c "$port/qnn-session.c" -o "$out/qnn-session.o"
"${CXX:-aarch64-linux-gnu-g++}" -std=c++17 -O2 -Wall -Wextra \
    -Wno-missing-field-initializers -Werror -I"$VULKAN_INCLUDE" \
    "$port/npu-pipeline.cpp" "$out/qnn-session.o" \
    -L"$VULKAN_LIBDIR" -Wl,-l:libvulkan.so.1 -ldl -o "$out/npu-pipeline"
"${CXX:-aarch64-linux-gnu-g++}" -std=c++17 -O2 -Wall -Wextra \
    -Wno-missing-field-initializers -Werror -I"$VULKAN_INCLUDE" \
    "$port/frame-stages.cpp" -L"$VULKAN_LIBDIR" -Wl,-l:libvulkan.so.1 -o "$out/frame-stages"
printf 'Standalone bundle: %s\n' "$out"
