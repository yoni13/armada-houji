#!/usr/bin/env bash
# Run a QAIRT host tool with its private Python and native-library environment.
set -euo pipefail
: "${QAIRT_SDK_ROOT:?point to the extracted QAIRT SDK}"
: "${QAIRT_PYTHON:?point to a Python 3.12 virtualenv interpreter}"
tool=${1:?usage: qairt-host.sh TOOL [ARGS...]}
shift
[[ "$tool" != */* ]] || { echo 'TOOL must be a tool name' >&2; exit 2; }
python_lib=$("$QAIRT_PYTHON" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')
export PYTHONPATH="$QAIRT_SDK_ROOT/lib/python${PYTHONPATH:+:$PYTHONPATH}"
export LD_LIBRARY_PATH="$python_lib:$QAIRT_SDK_ROOT/lib/x86_64-linux-clang${QAIRT_HOST_LIBS:+:$QAIRT_HOST_LIBS}"
exe="$QAIRT_SDK_ROOT/bin/x86_64-linux-clang/$tool"
[[ -f "$exe" ]] || { echo "Missing tool: $tool" >&2; exit 2; }
if head -c2 "$exe" | grep -q '#!'; then
    exec "$QAIRT_PYTHON" "$exe" "$@"
else
    exec "$exe" "$@"
fi
