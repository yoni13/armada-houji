#!/usr/bin/env python3
"""Compile an AudioReach m4 graph with the host libatopology library."""
import argparse
import ctypes
import ctypes.util
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("macros", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    config = subprocess.check_output(["m4", "-I", str(args.macros.resolve()), "-I", str(args.source.resolve().parent),
                                      str(args.source.resolve())])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".conf").write_bytes(config)
    library = ctypes.util.find_library("atopology")
    if not library:
        raise SystemExit("Install the host ALSA topology library first")
    lib = ctypes.CDLL(library)
    lib.snd_tplg_new.restype = ctypes.c_void_p
    lib.snd_tplg_load.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.snd_tplg_load.restype = ctypes.c_int
    lib.snd_tplg_build.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.snd_tplg_build.restype = ctypes.c_int
    lib.snd_tplg_free.argtypes = [ctypes.c_void_p]
    handle = lib.snd_tplg_new()
    if not handle:
        raise SystemExit("snd_tplg_new failed")
    try:
        result = lib.snd_tplg_load(handle, config, len(config))
        if result < 0:
            raise SystemExit(f"ALSA topology parse failed: {result}")
        result = lib.snd_tplg_build(handle, str(args.output).encode())
        if result < 0:
            raise SystemExit(f"ALSA topology build failed: {result}")
    finally:
        lib.snd_tplg_free(handle)
    print(f"{args.output}: {args.output.stat().st_size} bytes")


if __name__ == "__main__":
    main()
