#!/usr/bin/env bash
# Phone-side environment, scoped to a single attended diagnostic process.
set -euo pipefail
prefix=${FSR4_HTP_ROOT:-"$HOME/.local/opt/fsr4-htp"}
tool=${1:?usage: run-probe.sh probe-qnn [--shared] | probe-dmabuf | fastrpc_test [ARGS...]}
shift
case "$tool" in
    probe-qnn|probe-dmabuf|fastrpc_test) ;;
    *) echo "Unknown diagnostic: $tool" >&2; exit 2;;
esac
export LD_LIBRARY_PATH="$prefix/lib"
export DSP_LIBRARY_PATH="$prefix/dsp;/run/houji/dsp/cdsp"
export ADSP_LIBRARY_PATH="$DSP_LIBRARY_PATH"
exec timeout --kill-after=3 40 "$prefix/bin/$tool" "$@"
