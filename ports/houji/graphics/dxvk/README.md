# GTA V Enhanced: native ARM64EC graphics

The tested combination is Proton Experimental ARM64 `experimental-11.0-20261014-arm64`
with its bundled FEX `FEX-2610-25-g5285020`, native D3D12 pair, and DXGI rebuilt
from matching DXVK revision `a78a467cadb22bee928904290cf8e7921b121851` plus the
dispatcher hotpatch patch in this directory.

## Failure and repair

DXVK returns a `DxgiSwapChainDispatcher` object to applications. Its underlying
`DxgiSwapChain` methods already carry `DXVK_HOTPATCHABLE`, but the public
dispatcher methods lacked it. On ARM64EC, the dispatcher vtable therefore
pointed straight to ARM64 instructions.

GTA's unmodified `SocialClubD3D12Renderer.dll` installs x64 MinHook hooks on
`Present` and `ResizeBuffers`. A live capture showed it replacing ARM64 frame
setup bytes with an x64 `E9` jump and constructing invalid x64 trampolines from
ARM64 instructions. The game then called address zero and exited. Updating FEX
alone reproduced the failure. Using x64 DXGI with native D3D12 worked, isolating
the remaining failing boundary.

The patch adds the same six `DXVK_HOTPATCHABLE` annotations to the dispatcher
that its inner swap chain uses. LLVM emits x64-compatible hybrid entry thunks
which forward to native ARM64EC method bodies. Live verification after gameplay
confirmed Social Club patched the thunks and the native bodies remained intact.
The Social Club DLL and its functionality are retained.

## Rebuild

Build dependencies: Git, Meson, Ninja, Python 3, glslang, and llvm-mingw's
**20261006 UCRT Linux x86_64-host toolchain**. Download archive SHA-256:

```text
5f9c6ed95b2d4bdb2869a488c5fd5857fbdabcf288a0aa3eb1da43f6a08d8ab4
```

The archive is `llvm-mingw-20261006-ucrt-ubuntu-22.04-x86_64.tar.xz` from
<https://github.com/mstorsjo/llvm-mingw/releases/tag/20261006>.

Connect WARP and verify `warp=on` before GitHub downloads, then disconnect and
verify `warp=off` immediately after fetching. Fetch all dependencies explicitly;
the build script uses Meson's `--wrap-mode=nodownload`.

```sh
git clone https://github.com/doitsujin/dxvk.git /path/to/dxvk
git -C /path/to/dxvk checkout a78a467cadb22bee928904290cf8e7921b121851
git -C /path/to/dxvk submodule update --init --recursive \
  include/vulkan include/spirv subprojects/dxbc-spirv subprojects/libdisplay-info

LLVM_MINGW=/path/to/llvm-mingw JOBS=8 \
  bash ports/houji/graphics/dxvk/build-arm64ec.sh /path/to/dxvk /path/to/build
```

The script applies the patch, builds only DXGI and its required libraries, and
checks all six public entrypoints against the PE CHPE code map. The DLL is
`/path/to/build/src/dxgi/dxgi.dll`. Build receipts retain compiler identity,
source diff, submodule revisions and output hash. Keep its symbols for the
entrypoint check; do not mistake a PE machine value of `0x8664` for proof that
a DLL is pure x64.

Tested submodule revisions:

| Submodule | Revision |
| --- | --- |
| Vulkan-Headers | `6802bb4733b63ed5efd3adb308a6c885ef180ea1` |
| SPIRV-Headers | `04f10f650d514df88b76d25e83db360142c7b174` |
| dxbc-spirv | `ecb8308fe03fcad695a6ebdacbef6e7f61e77484` |
| dxbc-spirv's SPIRV-Headers | `c8ad050fcb29e42a2f57d9f59e97488f465c436d` |
| libdisplay-info | `275e6459c7ab1ddd4b125f28d0440716e4888078` |

## Per-game installation and rollback

Stop GTA and its prefix's Wine processes before changing versions. Back up the
prefix registries, user/save directories, compatibility selection, launch
options and existing game-local graphics DLLs. Preserve the game installation.

Select **Proton Experimental (ARM64)** for the Rockstar shortcut. Place these
files beside `GTA5_Enhanced.exe`, using atomic replacements:

| Destination | Source |
| --- | --- |
| `dxgi.dll` | Patched ARM64EC build above |
| `d3d12.dll` | Selected Experimental's `files/lib/wine/vkd3d-proton/aarch64-windows/d3d12.dll` |
| `d3d12core.dll` | Same directory's `d3d12core.dll` |

Retain the existing game-only `native` overrides for `dxgi`, `d3d12` and
`d3d12core` under `HKCU\Software\Wine\AppDefaults\GTA5_Enhanced.exe\DllOverrides`.
Do not disable `SocialClubD3D12Renderer`. No global Wine override or host FEX
replacement is needed. Verify the running game maps these files and its FEX
shared-memory header reports the intended bundled release.

The tested files have these SHA-256 identities:

```text
dxgi.dll       fa8652e726151b06a6ade105e667711653f72d4bfd2065ae4172a15c7fa711c5
d3d12.dll      0e4c875683c75eb7828b101bba3be9e5d0bca19fc22ed4086c23474981a97d59
d3d12core.dll  49570b7783b60a32bb7512e938c7a7c0318567e3007c7e46d835ef42b3675bc6
```

Rollback restores the backed-up three x64 DLLs and the previous stable Proton
selection, plus prefix state if required after a compatibility downgrade.
On the development handset, backups and receipts are under
`~/.cache/houji-gta-arm64ec-20261018/`; its `prefix-stopped-state.tar` is the
quiescent registry/save snapshot. The earlier full prefix archive was made
before the old processes fully exited, so use the stopped snapshot for those
mutable files. Avoid restoring older saves over newer user progress.

## Validation and limits

On October 18, 2026 (Taiwan time, UTC+8), patched native DXGI plus native D3D12
reached the menu, loaded Story Mode, and rendered indoor/outdoor gameplay with
keyboard movement. Social Club's eight hooks installed on compatible thunks;
the two previously corrupted native dispatcher bodies matched the on-disk
code after play. FEX identified itself as `FEX-2610-25-g5285020`. The game's
graphics settings hash matched its pre-test snapshot and diagnostic logging
was off for measurements.
A second clean full-prefix Steam launch reached Story Mode again; the same
native-body and settings checks passed. The game was left paused after testing.

| Full-native capture | Duration | Average FPS | MangoHud 1% minimum |
| --- | ---: | ---: | ---: |
| Indoor | 30 s | 47.9 | 30.1 |
| Outdoor | 45 s | 36.0 | 23.9 |
| Later hot outdoor | 60 s | 33.7 | 20.3 |

These short runs used different scenes/thermal states from the earlier x64
baseline and cannot quantify a causal speedup. The hot run still capped big
cores at 1286.4 MHz and prime at 1132.8 MHz. This repairs the demonstrated hook
crash; it does not establish sustained 60 FPS or long-session reliability.
Online gameplay was not tested. Future Steam updates must be checked against
the pinned game-local files rather than assuming they update automatically.
