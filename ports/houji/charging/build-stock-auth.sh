#!/bin/sh
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
output=${1:?usage: build-stock-auth.sh OUTPUT}
${CC:-aarch64-linux-gnu-gcc} -O2 -Wall -Wextra -Werror \
    -fstack-protector-strong -static -I "$here/../touch/native" \
    "$here/stock-auth.c" "$here/../touch/native/sha256.c" -o "$output"
