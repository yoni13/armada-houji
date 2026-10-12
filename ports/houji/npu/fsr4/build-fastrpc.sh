#!/usr/bin/env bash
# Cross-build Qualcomm's mainline-compatible CDSP client, without installing a
# daemon or replacing the sensor stack. DEPENDENCIES supplies target .so files
# and libyaml/libbsd headers; see README.md.
set -euo pipefail
port=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
work=$(realpath -m "${1:?usage: build-fastrpc.sh WORK DEPENDENCIES [PHONE_PREFIX]}")
deps=$(realpath "${2:?target dependency directory required}")
prefix=${3:-/var/home/armada/.local/opt/fsr4-htp}
revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["fastrpc"]["revision"])' "$port/sources.json")
mkdir -p "$work"
src="$work/fastrpc-$revision"
if [[ ! -d "$src" ]]; then
    curl -fLsS --retry 3 --max-time 120 --limit-rate 15000000 --proto '=https' \
        "https://codeload.github.com/qualcomm/fastrpc/tar.gz/$revision" -o "$work/fastrpc.tar.gz"
    mkdir "$src"
    tar -xf "$work/fastrpc.tar.gz" -C "$src" --strip-components=1
fi
cd "$src"
autoreconf -fi
./configure --host=aarch64-linux-gnu --prefix="$prefix" \
    --with-config-base-dir="$prefix/config" \
    CC="${CC:-aarch64-linux-gnu-gcc}" CXX="${CXX:-aarch64-linux-gnu-g++}" \
    YAML_CFLAGS="-I$deps/include" YAML_LIBS="-L$deps/lib -lyaml" \
    BSD_CFLAGS="-I$deps/include -isystem $deps/include/bsd -DLIBBSD_OVERLAY" \
    BSD_LIBS="-L$deps/lib -lbsd"
make -j"${JOBS:-4}" -C src libcdsprpc.la
make -C test fastrpc_test
mkdir -p "$work/stage/lib/fastrpc_test" "$work/stage/bin" \
    "$work/stage/share/fastrpc_test/v75" "$work/stage/config/conf.d"
install -m 0755 src/.libs/libcdsprpc.so.1.0.0 "$work/stage/lib/"
ln -sfn libcdsprpc.so.1.0.0 "$work/stage/lib/libcdsprpc.so.1"
ln -sfn libcdsprpc.so.1 "$work/stage/lib/libcdsprpc.so"
install -m 0755 test/fastrpc_test "$work/stage/bin/"
install -m 0644 test/linux/libcalculator.so "$work/stage/lib/fastrpc_test/"
install -m 0644 test/v75/libcalculator_skel.so "$work/stage/share/fastrpc_test/v75/"
install -m 0644 "$port/houji.yaml" "$work/stage/config/conf.d/"
ln -sfn /run/houji/dsp/cdsp "$work/stage/config/dsp"
printf 'CDSP client and bounded calculator test staged in %s\n' "$work/stage"
