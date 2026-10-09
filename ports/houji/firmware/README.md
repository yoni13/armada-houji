# Houji firmware

These bundled build inputs were extracted from Xiaomi's public
OS3.0.303.0.WNCTWXM Taiwan fastboot archive. `firmware.json` records the source
archive hash and each bundled file's SHA-256. Firmware remains proprietary to
its respective owners; the port's source license does not relicense these files.

`root/` contains the GPU and Wi-Fi firmware, stock touch core and configuration,
charger authentication executable, sensor defaults and speaker protection data.
`stock/` contains boot header metadata, DT references, AVB metadata and the DTBO
selector table required to package images for stock ABL. The DTBO file is trimmed
to its declared length; its original padded hash is retained in stock-report.json.

ADSP, CDSP, Bluetooth and modem/GPS firmware comes from the installed stock firmware
partitions or Armada's linux-firmware package. Factory sensor/audio calibration
and modem NV data are read from the user's own handset at runtime. They are
never included here. No Wi-Fi profiles, pairing keys or location history belong
in this directory.

To regenerate the firmware inputs, run `../firmware.py` with the exact archive
listed in `../sources.json`. Its output is verified against that archive's hash.
