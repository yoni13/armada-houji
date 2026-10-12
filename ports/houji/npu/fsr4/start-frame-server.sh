#!/bin/sh
# Usage: start-frame-server.sh BINARY [high]. Transient user unit, 1 h limit.
# "high" requests a high-priority GPU queue; it measured ~30% faster in Goat.
P="$HOME/.local/opt/fsr4-htp"
systemctl --user stop houji-fsr4-frame-test.service 2>/dev/null
systemctl --user reset-failed houji-fsr4-frame-test.service 2>/dev/null
priority=
[ "$2" = high ] && priority=--setenv=FSR4_GPU_PRIORITY=high
systemd-run --user --unit=houji-fsr4-frame-test -p RuntimeMaxSec=3600 \
  --setenv=FSR4_SHARED_FILE=/dev/shm/houji-fsr4-850190.bin --setenv=LD_LIBRARY_PATH="$P/lib" \
  --setenv=DSP_LIBRARY_PATH="$P/dsp;/run/houji/dsp/cdsp" --setenv=ADSP_LIBRARY_PATH="$P/dsp;/run/houji/dsp/cdsp" \
  --setenv=FSR4_HTP_PERFORMANCE=1 --setenv=FSR4_HTP_CORNER=nominal --setenv=FSR4_QNN_OPT=3 $priority \
  "$P/bin/$1" "$P/stages-goat" 1280 720
for i in $(seq 1 30); do [ -f /dev/shm/houji-fsr4-850190.bin ] && break; sleep 1; done
systemctl --user is-active houji-fsr4-frame-test.service
journalctl --user -u houji-fsr4-frame-test --since "-40s" --no-pager -o cat | grep -E 'priority|Listening|Adreno|error|failed'
