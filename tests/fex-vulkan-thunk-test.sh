#!/usr/bin/env bash
# armada-game-launch must point Steam's FEX Vulkan thunk at the pressure-vessel link to
# Armada's Arch-layout graphics provider, only for the Linux FEX compat tool, without
# disturbing other per-app ThunksDB entries.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export PYTHONDONTWRITEBYTECODE=1

python3 - "$ROOT" "$WORK" <<'PYEOF'
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import sys

ROOT, WORK = sys.argv[1], pathlib.Path(sys.argv[2])
sys.path.insert(0, os.path.join(ROOT, "system_files/usr/lib/armada"))
path = os.path.join(ROOT, "system_files/usr/libexec/armada/armada-game-launch")
loader = importlib.machinery.SourceFileLoader("armada_game_launch", path)
spec = importlib.util.spec_from_loader("armada_game_launch", loader)
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)

failures = []


def check(name, condition):
    if not condition:
        failures.append(name)
        print(f"FAIL: {name}", file=sys.stderr)


FEX = ["/s/steam-launch-wrapper", "--", "/s/reaper", "SteamLaunch", "AppId=730", "--",
       "/s/steamapps/common/FEX-Emu/fex-compat-tool", "waitforexitandrun", "--",
       "/s/SteamLinuxRuntime_sniper/_v2-entry-point", "--verb=waitforexitandrun", "--", "/game/cs2.sh"]
PROTON = ["/s/steam-launch-wrapper", "--", "/s/reaper", "SteamLaunch", "AppId=730", "--",
          "/s/Proton Experimental (ARM64)/proton", "waitforexitandrun", "/game/cs2.exe"]

compat = WORK / "compatdata" / "730"
db_file = compat / "fex-emu" / "ThunksDB.json"
env = {"STEAM_COMPAT_DATA_PATH": str(compat)}

launch.install_fex_thunk_overlays(PROTON, env)
check("arm64ec Proton launch writes nothing", not db_file.exists())

launch.install_fex_thunk_overlays(FEX, env)
data = json.loads(db_file.read_text())
overlays = data["DB"]["Vulkan"]["Overlay"]
check("guest library named", data["DB"]["Vulkan"]["Library"] == "libvulkan-guest.so")
PV = "/usr/lib/pressure-vessel/overrides/lib/x86_64-linux-gnu/"
check("pressure-vessel loader link overlaid", PV + "libvulkan.so.1" in overlays)
check("pressure-vessel unversioned link overlaid", PV + "libvulkan.so" in overlays)
check("depot multiarch entries kept", "@PREFIX_LIB@/libvulkan.so.1" in overlays)
check("depot pinned runtime entry kept",
      "@HOME@/.local/share/Steam/ubuntu12_32/steam-runtime/pinned_libs_64/libvulkan.so.1" in overlays)
check("no temporary file left", not list(db_file.parent.glob(".*tmp")))

before = db_file.stat().st_mtime_ns
os.utime(db_file, ns=(before - 10**9, before - 10**9))
stamp = db_file.stat().st_mtime_ns
launch.install_fex_thunk_overlays(FEX, env)
check("unchanged file is not rewritten", db_file.stat().st_mtime_ns == stamp)

db_file.write_text(json.dumps({"DB": {"GL": {"Library": "libGL-guest.so", "Overlay": ["/x"]},
                                      "Vulkan": {"Library": "libvulkan-guest.so", "Overlay": ["/old"]}},
                               "Other": 1}))
launch.install_fex_thunk_overlays(FEX, env)
data = json.loads(db_file.read_text())
check("other libraries preserved", data["DB"]["GL"]["Overlay"] == ["/x"])
check("other top-level keys preserved", data.get("Other") == 1)
check("stale Vulkan entry replaced", data["DB"]["Vulkan"] == launch.FEX_VULKAN_THUNK)

db_file.write_text("not json")
launch.install_fex_thunk_overlays(FEX, env)
check("malformed file replaced", json.loads(db_file.read_text())["DB"]["Vulkan"] == launch.FEX_VULKAN_THUNK)

override = WORK / "override"
launch.install_fex_thunk_overlays(FEX, {"STEAM_COMPAT_DATA_PATH": str(compat),
                                        "FEX_APP_CONFIG_LOCATION": str(override)})
check("FEX_APP_CONFIG_LOCATION honoured", (override / "ThunksDB.json").exists())

relative = WORK / "relative-check"
relative.mkdir()
cwd = os.getcwd()
os.chdir(relative)
try:
    launch.install_fex_thunk_overlays(FEX, {"FEX_APP_CONFIG_LOCATION": "rel/"})
    launch.install_fex_thunk_overlays(FEX, {"STEAM_COMPAT_DATA_PATH": "compat"})
finally:
    os.chdir(cwd)
check("relative locations are ignored", not any(relative.rglob("ThunksDB.json")))
launch.install_fex_thunk_overlays(FEX, {})
check("no location, no write", not (pathlib.Path.cwd() / "fex-emu" / "ThunksDB.json").exists())

if failures:
    print(f"{len(failures)} FEX Vulkan thunk check(s) failed", file=sys.stderr)
    sys.exit(1)
print("PASS: FEX Vulkan thunk overlays for the pressure-vessel graphics provider link")
PYEOF
