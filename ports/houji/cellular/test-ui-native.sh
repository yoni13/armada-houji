#!/usr/bin/env bash
set -euo pipefail
port=$(cd -- "$(dirname -- "$0")" && pwd)
source=${1:?usage: test-ui-native.sh PATCHED_MODEMMANAGER_QT_SOURCE}
work=$(mktemp -d "${TMPDIR:-/tmp}/houji-ui-test-XXXXXX")
trap 'rm -rf "$work"' EXIT
cmake -S "$port/tests" -B "$work" -DMMQT_SOURCE="$(realpath "$source")" -DCMAKE_BUILD_TYPE=Release
cmake --build "$work" -j4
dbus-run-session "$work/lifecycle"
