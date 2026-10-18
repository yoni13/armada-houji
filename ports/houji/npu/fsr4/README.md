# FSR 4 INT8 on Houji's Hexagon v75 — experimental bring-up

**Parked (2026-10-18):** runtime entry points now require the explicit master
opt-in `HOUJI_FSR4_EXPERIMENTAL=1`. Existing `FSR4_NPU_LIVE`, `FSR4_NPU_DIRECT`
or frame-generation config values alone cannot activate a newly built hook,
server or layer. The Decky backend refuses enable/restart without the master
opt-in and its toggle is disabled. All experiment sources, models and build
instructions are retained; measurements below are historical, not current
runtime defaults. The master gate also disables capture-only hooks by default.

**Decky memory:** a disabled toggle still loads the plugin's Python/FEX process.
The development handset therefore keeps its installed plugin directory at
`~/homebrew/disabled-plugins/fsr4-npu`, outside Decky's scanned `plugins/` tree.
The plugin is not included in fresh Houji images. Its settings, sources, models
and binaries are retained. To intentionally restore the UI, stop Decky Loader,
move that directory back to `~/homebrew/plugins/fsr4-npu`, then start the loader;
the separate experimental gates below still apply.

For intentional future testing, set the master opt-in in the game environment,
server environment and Decky Loader environment, as well as the existing
per-feature flags. The user service additionally checks the user manager's
environment (`systemctl --user set-environment HOUJI_FSR4_EXPERIMENTAL=1`).
Rebuild hooks/server before testing; old installed binaries predate this gate.
Normal gaming should have no experiment DLL injection, launch flags, services
or render-scale overrides. Frame generation remains parked.

On the development handset, the cleanup restored Goat's launch options to
`/usr/libexec/armada/armada-game-launch %command%`, removed the four forced
TAA/screen-percentage settings from `Engine.ini`, and moved the injected
`dxgi.dll`, `Houji-TAA.addon`, `_nvngx.dll` and `ReShade.ini` into the private
`~/.local/state/houji-game-debug/fsr-disabled-20261018T013752/` backup. Existing
game quality/resolution preferences were already identical to the pre-experiment
backup and were kept. Models, shaders and binaries in `~/.local/opt/fsr4-htp`
remain available. The inactive user service now checks the master gate, and the
Decky plugin's backend/frontend are updated for the parked state.

Goal: native ARM64 Linux inference of AMD's FSR 4 INT8 neural network on the
Snapdragon 8 Gen 3 HTP, with Turnip handling temporal preprocessing and final
image reconstruction. The experimental Wine/Proton bridge upscales Goat
Simulator 3 to 1280×720 on the NPU. At Medium settings, FSR 4 Performance
(640×360 → 720p) ran at 47.2 FPS against 57.0 FPS for the game's own TAA
upsample: about 3.6 ms more per frame, plus two frames of latency. Image
quality is validated only against the CPU-ONNX reference, not AMD's DX12
output.
This directory is not installed by the image builder.

**Current handset state:** the pipelined Goat test (ReShade, `Houji-TAA.addon`,
`_nvngx.dll`, test `Engine.ini`; no Luma, no registry change) is installed
game-locally. `goat-pipeline-test.py restore` removes it; see the backup notes
below. The frame server runs only as a transient user unit.

## Verified on the phone (2026-10-11)

- Kernel `7.2.6-armada-houji-kufs1`, CDSP running, stock `fastrpc_shell_3`
  and `fastrpc_shell_unsigned_3` available under `/run/houji/dsp/cdsp`.
- Built Qualcomm's mainline-compatible `libcdsprpc` from the revision in
  `sources.json`, installed privately under `~/.local/opt/fsr4-htp`.
  No sensor daemon/library was replaced.
- Qualcomm's v75 calculator ran in an unsigned CDSP user process domain:
  sum of 0..999 = 499500; maximum = 999.
- QAIRT 2.50's **aarch64-oe-linux-gcc11.2** QNN HTP libraries loaded the v75
  skeleton and executed a quantized Add graph with zero mismatches. This uses
  `libQnnHtp.so`, with no CPU backend fallback. First-call latency was about
  4.7 ms; it is a smoke test, not an FSR performance measurement.
- The same Add graph executed with three `QNN_MEM_TYPE_ION` registrations
  backed by **system-heap DMA-BUFs**, and again had zero mismatches. Despite the
  QNN enum's name, the allocation here uses `/dev/dma_heap/system`, not ION.
- Turnip imported a system-heap DMA-BUF into Vulkan, filled 1 MiB on the GPU,
  and the CPU read back the expected pattern with zero mismatches. This alone
  does not prove GPU→QNN→GPU synchronization or end-to-end zero-copy inference.
- AMD Quality-preset weight extraction passed the reference extractor's
  initializer layout, shader/comment, scale and provider cross-checks.

## Source versions and numerical contract

`sources.json` pins AMD's older public INT8 v07 implementation, the independent
Hexagon conversion reference, Qualcomm FastRPC, and the Metal reference.
Do not substitute the current SDK's binary FSR 4.1 provider and call it the
same model. Downloaded SDK/runtime files and generated weights live outside
this checkout. Upstream license files are fetched alongside their sources.

## Standalone pipeline and prototype game bridge

`build-standalone.sh` builds the native Vulkan/HTP pipeline, GPU pre/post
shaders, a fixed-size neural graph and deterministic frame inputs. `a16`
retains INT8 weights/quantized boundaries with **16-bit residual intermediates**;
`a8` also quantizes residuals to 8 bits and changes model numerics more.
Neither candidate is bit-exact FSR 4. `mixed` is the initial INT8/FP16 path.

At 1280×720 output, three synthetic frames completed through
GPU→DMA-BUF→HTP→DMA-BUF→GPU. A separate CPU-ONNX temporal sequence, with its
own history and recurrent buffers, gave these unit-peak RGB PSNR values:

| Variant | Frame 0 | Frame 1 | Frame 2 |
|---|---:|---:|---:|
| W8 with A16 residuals | 54.92 dB | 49.31 dB | 47.62 dB |
| W8 with A8 residuals | 48.00 dB | 44.88 dB | 40.76 dB |

These are comparisons against the candidate ONNX model using the same ported
pre/post shaders, **not AMD DX12 golden results**. The synthetic test has a
single motion field and does not establish occlusion, disocclusion, HDR, or
camera-cut quality. Its 720p render width is rounded down from the 1.5 ratio.

With a scoped nominal HTP corner vote, A16-residual inference took about
8.0 ms and A8-residual inference 3.8–5.0 ms. Tests ran alongside GTA V, so GPU
timings were highly variable and are not uncontended game-performance results.
The QNN power context is destroyed on exit. QNN O3 materially broke the
original mixed FP16 residual variant; the A16 variant produced identical small
tensor results with O1 and O3 in the observed comparison.

The standalone interop path does not stage neural tensors through the CPU.
`frame-server.cpp` is a separate **prototype staged game bridge**: an NGX
replacement in Wine reads D3D11 textures into shared RAM, signals the native
process over a loopback-only socket, then uploads its result to the game.
This adds copies and waits that
are absent from the standalone GPU/NPU measurements. It is not a game-to-NPU
zero-copy claim.

Profiling the first live bridge explained the owner's single-digit FPS report:
at native 720p, one call cost about 95–102 ms, of which 25–30 ms was D3D11
readback and 28–40 ms was CPU format conversion, versus roughly 7 ms HTP.
Uploading R11G11B10F and RG32F directly reduced conversion to about 3 ms.
RAM-backed shared frame memory removed socket payload copies; the socket now
signals readiness only. Readback, uploads, synchronization and the final
game upload still remain. Measured bridge calls fell to about 50 ms in a
steady native-resolution sample, not enough to claim a performance win.

Wire revision 4 adds actual render dimensions and the UE camera transform.
`FSR4_FRAME_FILE` names the same shared memory file from Wine, and
`FSR4_SHARED_FILE` names it from Linux. Use `/dev/shm` for this test, not a
userdata-backed file. The native server binds only loopback and validates
dimensions, formats and byte counts before accessing the payload.

The server reuses the file (`O_CREAT` + `ftruncate`, never unlink), and
launchers must not delete it either. Earlier launchers ran
`rm -f /dev/shm/houji-fsr4-850190.bin` before each server start. A game that
was already running kept its mapping of the deleted inode, so the server
processed a new, all-zero file. The game then read back its own stale output
every frame. This is the "scene frozen on one frame while NPU is on" bug, and
it invalidated every gameplay measurement where the server started after the
game. The bridge now also drops and reopens its mapping on every successful
reconnect.

### Decky Loader Plugin (`decky-plugin/`)

Installed on the handset at `/var/home/armada/homebrew/plugins/fsr4-npu` and
managed by the `houji-fsr4.service` systemd user unit (copy of
`decky-plugin/houji-fsr4.service`, installed in `~/.config/systemd/user/`). Accessible
directly from Game Mode's Quick Access (`...`) menu under **FSR 4 NPU**:
- **Enable FSR 4 NPU toggle:**
  - **On:** Ensures `/dev/dma_heap/system` permissions and starts `houji-fsr4.service`.
    The game hook immediately connects and runs FSR 4 on the Hexagon NPU (`npu=1`).
  - **Off:** Stops `houji-fsr4.service`. The game hook immediately falls back to
    native in-game rendering / TAA (`npu=0`) with zero stutter and no game restart.
- **Live status & metrics:** displays live resolution (`640×360 → 1280×720 (Performance)`
  or `960×540 → 1280×720 (Quality)`), HTP hardware identifier, and pipeline latency.
- **Restart Upscaler button:** resets and restarts the background server process.


Installed game: Steam app 850190, UE 4.27, no bundled DLSS/FSR2/FSR3/XeSS API.
Luma's Unreal TAA hook loads under Proton Experimental ARM64, and the owner
confirmed normal gameplay with the loader probe. Its low-AA path
(`37A2D4CA`) had no jitter and dummy motion vectors; forcing proper TAA and
100% render scale selects `8E2DC705` and supplies real temporal inputs.
Three frames were captured with color R11G11B10F, reversed-Z depth
R32G8X24, decoded RG32F motion vectors, changing jitter, and RGBA16F output.

The Luma release is an external binary (`latest-733`). Its NGX loader rejects
unsigned local providers. The handset's test copy has a three-byte change at
RVA `0x84c34` (`45 85 f6` → `eb 32 90`) to use the opened local provider in
that loader. Original SHA-256:
`8b127c72bbd543da16b4135efdd578936295eeac221404b2484f90bd68fcc052`.
Patched SHA-256:
`7250853b1c1aa6a7fa37261a8bc2e04ca079b14b04adadbbf2d33e2ddaf79832`.
No system trust library is modified. Luma's Custom MIT license requires
attribution and prior author permission for commercial reuse. Luma itself
is not vendored here. Authors/project: Filoppi/Luma-Framework, Izueh and Pumbo.

`ngx-capture.c` defaults to a three-frame capture with original-TAA fallback.
`FSR4_NPU_LIVE=1` opts into the staged frame server. The current live test
accepts 960×540 render inputs and 1280×720 RGBA16F output, and only the observed
color/depth/motion formats. Native 1280×720 was tested first. Exposure is fixed at one in
this prototype and requires further validation against real game luminance.
In synchronous mode, two failed exchanges disable the bridge for that process
and return to TAA. Pipelined mode (below) disables itself after three
consecutive protocol failures; a missing or restarting server is retried once
per second while TAA draws, and each completed frame clears the failure count.

The 75% render-scale path uses TAA shader CRC `2EFB4885`, with 960×540
color/depth/encoded velocity and a 1280×720 UAV. Luma recognizes this pass but
does not reach NGX evaluation. `goat-taa-probe.cpp` therefore has an opt-in
`FSR4_NPU_DIRECT=1` path bound to that exact shader. It reads the validated
view cbuffer (b1), projection at float4 row 28, clip-to-previous at row 122,
and render size at row 130, and calls our backend directly. It only suppresses
the original dispatch after a successful NPU output upload. Unknown shaders
retain normal rendering. This is specific to the observed game build and
must not be presented as a general Unreal hook.

UE 4.27 compiles a separate TAA permutation for each screen-percentage range.
At 50% (FSR 4 "Performance", 640×360 → 1280×720) the pass is `FDE55DEE`, not
`2EFB4885`. In addition, the game's resource bindings shift: color is provided
as `DXGI_FORMAT_R16G16B16A16_FLOAT` (format 10, 8 bytes/pixel) at `t1` rather
than `DXGI_FORMAT_R11G11B10_FLOAT` (format 26, 4 bytes/pixel), and motion
vectors are bound at slot `t6` (format 11) while slot `t3` is bound to a 1×1
dummy texture.

With `FSR4_NPU_DIRECT=1`, the hook now discovers the TAA pass and locates all
three temporal inputs dynamically across slots `t0`..`t7`:
- Color: matching render dimensions with format 26 or 10
- Depth: matching render dimensions with format 19 (`R32G8X24`)
- Motion vectors: matching render dimensions with format 11 (`RG16_UNORM`) or 16 (`RG32F`)
- Output: 1280×720 UAV at `u0` (format 10)
- View constants: 8 KiB constant buffer at `b1`

`frame-server.cpp` accepts both format 26 and format 10 for input color,
allocating the corresponding Vulkan image format (`VK_FORMAT_B10G11R11_UFLOAT_PACK32`
or `VK_FORMAT_R16G16B16A16_SFLOAT`). Both 75% (960×540) and 50% (640×360) upscaling
run cleanly through the pipeline with no hardcoded slot assumptions.

The camera-cache variant copies the view constants when the game writes them
(map/unmap or update), instead of synchronously reading them back from the
GPU. It falls back to TAA if that exact buffer has no valid cached view data.
This had zero cache misses in observed gameplay. It removes the small-buffer
GPU readback, but the frame-completion wait then moves into texture readback;
it does not eliminate the game's GPU→CPU synchronization.

### Pipelined mode (`FSR4_NPU_PIPELINE=1`)

The synchronous bridge made the game's render thread wait for its own GPU work
to reach the TAA pass, then for the native server, every frame. Pipelined mode
(only on the `Houji_EvaluateTAA` path) uses two staging slots. At frame N
it uploads the completed result for frame N−2, issues GPU copies of frame
N's inputs, maps frame N−1's staging copies (already complete) and submits
them to the server. The render thread then continues while the server
processes them.

**Trade-off:** the 3D scene in the TAA output is two frames older than the
game's later post-processing and HUD. Depth-dependent post effects can
therefore mismatch at moving edges. This is a measured latency increase, not
free performance. A gap over 250 ms (loading, menus) or a game reset flag
drops queued work and resets FSR history. The two priming frames draw normal
TAA but keep history continuous (result `0xbad0000a`).

Pipelining exposed the server's own GPU cost under game load. Every helper
in `frame-stages.cpp` submitted and waited separately, about 11 queue round
trips per frame, each queued behind game GPU work. Server upload time rose
from ~2 ms to 14–54 ms. `frame-server.cpp` now records two batches per
frame (uploads, motion decode and pre-pass; then post-pass and download).
The same game-side pipeline then cost 2.5–4 ms per call with ~1 ms reply wait
in gameplay. `FSR4_GPU_PRIORITY=high` optionally requests a high global-priority
queue. The msm driver gives Adreno 750 four preemption rings
(`ADRENO_QUIRK_PREEMPTION`) and has no privilege check on submit-queue priority.

The server no longer sets a receive timeout while idle. Previously a loading
screen longer than five seconds closed the connection and consumed one of the
bridge's failure allowances.

The render thread's remaining cost was mostly memcpy: about 1.2 ms reading
mapped staging inputs and about 1 ms inside `UpdateSubresource`. A worker
thread now copies the mapped inputs into shared memory, performs the socket
exchange, and writes the reply directly into a mapped `STAGING` texture. The
render thread only unmaps it and issues a GPU `CopyResource` into the game's
output. It never unmaps memory while the worker may still write: if a job
takes more than 3.5 s, the socket is shut down and the bridge disables itself.
This cut the hook's render-thread time from about 2.8 ms to 0.04–0.07 ms.
It did **not** raise the frame rate: 15% behind TAA in the matched
19:15–19:18 run, against 13% for the previous variant in the same spawn. The
difference is within session noise. Render-thread time therefore was not
the limit. The remaining cost is still unexplained. Candidates include GPU
preemption/context switching, CPU contention from the server and worker, and
shared SoC power limits; none is yet measured in isolation.

Server-side zero-copy of the shared file is **not available on this device**.
Turnip exposes no `VK_EXT_external_memory_host` (only llvmpipe does), so
Vulkan cannot wrap the `/dev/shm` mapping. The server keeps its CPU copies,
which run off the game's threads. `test-frame-server.c` sends three
deterministic synthetic Goat-format frames to a server on `FSR4_TEST_PORT`
and saves the outputs. Fresh server instances produced byte-identical,
finite results.

### Earlier gameplay measurements (REJECTED / INVALIDATED)

**All earlier gameplay benchmark numbers (17–40 FPS) have been removed and
invalidated.** During live investigation, it was discovered that when the NPU
bridge was enabled, the rendered 3D scene froze on a single static frame (while
the HUD continued updating and Gamescope continued presenting). As a result,
the recorded intervals were measuring repeated presentations of a static,
frozen frame rather than interactive gameplay. None of those numbers reflect
actual gameplay performance.

The cause was a launcher deleting the shared frame file under a running game
(see "Wire revision 4" above). It is fixed.

Results measured after the fix (2026-10-11 23:15–00:11) were also removed:
the game's graphics settings changed afterwards, so they are not comparable
with new runs.

### Gameplay measurements, Medium settings (2026-10-12)

Settings: every scalability group Medium (1), 1280×720, VSync on, test
`Engine.ini` at `r.ScreenPercentage=50` (640×360 → 1280×720). At these
settings the game passes color as R11G11B10 (format 26). One game session,
desert courtyard spawn, goat idle, no input; the phone was charging, so
battery power readings are not usable. Each phase waited 100 s, then
recorded 60 s with `record-frametimes`. The script checks the service
state and the hook's `npu=` flag at both ends of every phase and aborts on a
mismatch. The journal showed no suspend.

01:10–01:29 Taipei, server settings:

| Phase | FPS | p50 | p95 | 1% low |
|---|---:|---:|---:|---:|
| game TAA (mean of first and last phase) | 57.03 | 16.8 ms | 36.5 ms | 22.1 |
| FSR 4, high GPU priority, NPU `nominal` (mean of two phases) | 47.19 | 20.3 ms | 42.1 ms | 19.8 |
| default GPU priority | 41.96 | 21.6 ms | 42.6 ms | 18.4 |
| NPU corner `svs` | 45.49 | 20.9 ms | 41.8 ms | 18.1 |
| NPU corner `svs2` | 42.30 | 21.6 ms | 45.0 ms | 17.3 |

FSR 4 costs about 3.6 ms per frame (17.5 → 21.2 ms mean interval) and adds
two frames of latency. High GPU priority is worth 12% over default. Lower
NPU corners lengthen the server round trip (worker 14–15 ms at `nominal`,
18–22 ms at `svs`/`svs2`) until it no longer fits in one game frame: the
render thread then waits 4–6.5 ms on average instead of 0.15 ms.

An earlier cost breakdown at the previous settings (results removed with the
settings change, method kept in `FSR4_ABLATE`) showed the transport itself
is free. With GPU and NPU work skipped, the game ran faster than with TAA.
The GPU passes and the NPU each accounted for roughly half of the cost.
GPU timestamps (`FSR4_GPU_TIMESTAMPS=1`) per frame: upload 0.10 ms, motion
decode 0.05 ms, pre 1.82 ms, post 0.75 ms, image-to-buffer copy 0.36 ms.

01:40–01:57, `FSR4_LINEAR_OUTPUT=1` (`post-linear.spv` writes the result
into a mapped buffer; byte-identical output on the synthetic regression,
including denormals). It removes the copy pass and costs 0.14 ms more in
post: about 0.21 ms of GPU time saved. Interleaved phases (TAA 57.43,
base 47.69, linear 47.12, base 46.60, linear 45.71, TAA 56.98 FPS) drift
down about 0.6 FPS per phase, and the linear phases lie on that trend.
The gain is below run-to-run noise, so it stays opt-in.

Known issue: after a suspend/resume cycle, new HTP sessions failed with
`fastrpc_mmap ... Cannot allocate memory` while mapping
`libQnnHtpV75Skel.so`, until a reboot. The contiguous CMA pool was not the
cause (starting the server after the reboot did not consume it). Do not
let the phone suspend between server restarts during tests.

The owner's current measurement instruction is **100 seconds after gameplay
is active**, followed by a recorded interval. Confirm NPU activity at both ends
and ensure the service timeout cannot expire during the sample. Earlier
30-second-after-launch sampling included loading and must not be reused.

Game-local backup and restore metadata for the earlier Luma tests are in
`~/.local/state/houji-game-debug/goat-fsr4-probe-*`. They cover the added files,
original Engine.ini/GameUserSettings.ini, launch options and prefix NGX path.
The original NGX registry FullPath is `C:\Windows\System32`; those tests
pointed it at the game directory. The pipelined test does not use Luma or the
registry. Its manifest is in `goat-fsr4-pipeline-*`, recording that no
`Engine.ini` existed before the test. The frame server is a temporary user unit
with a one-hour limit, not a boot service.

### Rejected interop probe and recovery

The x64 addon could query DXVK's device interface and Vulkan handles. Its
first `IDXGIVkInteropSurface::GetVulkanImageInfo` call did not return: first
inside the TAA callback, then in a present callback. The latest Unreal
minidump (2026-10-11 17:50:06 Taipei) records `0xc0000005`, reading `0x6140`
in `ntdll.dll+0xd40a0`. The stack contains `Houji-TAA.addon+0x1990`, which
disassembly identifies as the instruction immediately after the indirect
surface-method call at `+0x198d`.

**Cause:** `test-dxvk-interop.cpp`, run alone in a separate prefix on the same
Proton build, received a valid `VkImage` from `GetVulkanImageInfo`. Interop
itself works. ReShade 6 vtable-hooks `ID3D11Texture2D::GetDevice` (slot 3)
to return its proxy `D3D11Device` (`d3d11_resource.cpp`). DXVK's
`D3D11VkInteropSurface::GetVulkanImageInfo` calls `m_resource->GetDevice()`,
then `static_cast<D3D11Device*>(...)->LockImage(...)`, treating ReShade's proxy
as DXVK's class. This is a ReShade/DXVK incompatibility, not an ARM64 thunk
failure.

The hook no longer includes this diagnostic. Under ReShade, DXVK texture
interop needs a route that bypasses the hooked `GetDevice`. Even then, it gives
`VkImage` handles only inside the Wine process. A native process could share
them only with exportable memory and a Wine unix-side handle-to-fd bridge.

Recovery archived all 57 experimental game files, restored the prefix's
NGX FullPath to `C:\Windows\System32`, restored the original Steam launch
options and `GameUserSettings.ini` (87% render scale, AA quality 1), and
removed the test-only `Engine.ini` because no original existed. The crash
reporter was closed without sending a report. Screenshots and process maps
then verified normal gameplay with no Luma, Houji addon, or NGX replacement
loaded. Saved games were not removed.

The neural input is **16 quantized feature channels at half the padded output
resolution**, not the game's RGB image. The neural output is **8 reconstruction
parameter channels**, not an upscaled screenshot. Motion vectors, depth,
jitter, exposure, reset state, reprojected history and recurrent state must all
be handled by the surrounding pipeline. A generic screenshot-resize hook is
not sufficient for temporal FSR 4.

The imported Hexagon reference needs corrections before numerical validation:

- Its simulator calls halfway rounding *away from zero*. HLSL `round()` uses
  **ties to even**. `check-model.py` corrects that behavior before comparisons.
- Its ONNX builder uses UINT8 quantization with zero point 128 internally but
  declares an INT8 input. `build-model.py` fixes the input declaration and
  documents the `signed + 128` representation.
- The original saturation workaround inserts an FP16 round trip at each
  quantization boundary. The candidate graph retains normal QDQ saturation
  without those extra round trips. QNN conversion must still be checked for
  clipping and requantization changes on actual hardware.
- The final ConvTranspose must round its unbiased result to FP16 before the
  FP16 bias addition. The candidate exporter restores that boundary too.
- CPU-simulator comparison is **not** proof of AMD-provider parity. Recorded
  multi-frame inputs and AMD per-pass golden outputs remain the acceptance
  test for image quality and temporal behavior.

## Host preparation

Use a scratch/build directory with enough space for QAIRT. The publicly
downloadable 2.50 archive is 2,601,473,189 bytes; its observed SHA-256 is
`a346ea0e2c8631b46d57261a4969994cd9cc34124a8355bbc7b08b2c8bd859a5`.
All ZIP members passed CRC validation. QAIRT headers/libraries remain external
and are used under Qualcomm's accompanying license.

```sh
python3 ports/houji/npu/fsr4/fetch-sources.py /path/to/work
uv venv --python 3.12 /path/to/work/venv
uv pip install --python /path/to/work/venv/bin/python \
  numpy==1.26.4 onnx==1.16.1 onnxruntime==1.23.2
/path/to/work/venv/bin/python ports/houji/npu/fsr4/build-model.py /path/to/work \
  --width 64 --height 32 --output /path/to/work/model-small.onnx
/path/to/work/venv/bin/python ports/houji/npu/fsr4/check-model.py /path/to/work \
  /path/to/work/model-small.onnx --report /path/to/work/model-small-check.json
```

Start with small fixed tensors. 1280×720 output uses a 640×360 neural input;
1920×1080 uses 960×540. The game's rendering resolution is a separate setting.

The first 64×32 candidate executed in ONNX Runtime. Against the corrected
simulation, random input had mean absolute error 0.000304 and maximum 0.03125
in the 8-channel reconstruction parameters. Saturated inputs showed larger
differences (maximum 0.6953), so these results are not a fidelity pass. They
are not RGB quality measurements either.

`qairt-host.sh TOOL ...` provides a private host tool environment using
`QAIRT_SDK_ROOT`, `QAIRT_PYTHON`, and optionally `QAIRT_HOST_LIBS` for LLVM
`libc++`, `libc++abi` and `libunwind`. The build host needs the LLVM unwind
library expected by QAIRT, not a renamed incompatible GNU libunwind.

## Private FastRPC build

`build-fastrpc.sh WORK DEPS [PHONE_PREFIX]` needs an ARM64 GCC toolchain,
autotools, and the following target dependencies:

```
DEPS/include/yaml.h
DEPS/include/bsd/...
DEPS/lib/libyaml.so       -> target libyaml-0.so.2.0.9
DEPS/lib/libbsd.so        -> target libbsd.so.0.12.2
DEPS/lib/libbsd.so.0      -> target libbsd.so.0.12.2
DEPS/lib/libmd.so.0       -> target libmd.so.0.2.0
```

The target runtime libraries can be copied from the phone, and matching
development headers obtained on the build host. No Android `libcdsprpc.so`
is used on glibc Linux. The output `stage/` belongs at `PHONE_PREFIX` on the
phone; copies should stay below 15 MB/s.

For the current boot's tests, `system_heap` was loaded with `modprobe`, and
the logged-in user was given an ACL on `/dev/dma_heap/system`. These changes
are not persistent image changes. The port already grants CDSP user access.

```sh
P="$HOME/.local/opt/fsr4-htp"
export LD_LIBRARY_PATH="$P/lib"
export DSP_LIBRARY_PATH="$P/dsp;/run/houji/dsp/cdsp"
export ADSP_LIBRARY_PATH="$DSP_LIBRARY_PATH"
timeout --kill-after=3 20 "$P/bin/fastrpc_test" -d 3 -U 1 -t linux -a v75
```

## Hardware probes

Build `probe-qnn.c` using `-I "$QAIRT_SDK_ROOT/include/QNN" -ldl` with the
ARM64 cross-compiler. It checks a tiny quantized Add graph, either with raw
buffers or `--shared` DMA-BUF registrations. Required private runtime files:

```
lib/libcdsprpc.so[.1[.0.0]]
lib/libQnnHtp.so
lib/libQnnHtpPrepare.so
lib/libQnnHtpV75Stub.so
lib/libQnnSystem.so
dsp/libQnnHtpV75Skel.so
dsp/libQnnHtpV75.so
```

Build `probe-dmabuf.c` against Vulkan headers and the ARM64 Vulkan loader.
It specifically selects the Qualcomm integrated GPU, not llvmpipe. Run each
probe with a timeout. All probe resources are released on normal exit.
`probe-platform.py` is a read-only inventory; it does not infer NPU success
merely from a running remote processor.

## Remaining acceptance gates

1. Build and validate the complete neural-core graph, preserving AMD's tensor
   scales, signed representation, grouped channels and saturation.
2. Execute it on HTP v75 and compare its tensors with the CPU/reference path.
3. Integrate GPU pre/post passes, history/reset behavior and shared-buffer
   synchronization; validate several consecutive recorded frames against AMD.
4. Measure total frame latency (GPU passes, synchronization, NPU, reconstruction)
   and memory/power at 720p and 1080p. TOPS or the Add-probe timing is not a
   usable FPS estimate.
5. Connect an opt-in game hook that supplies color/depth/motion/exposure before
   the HUD. OptiScaler's compatibility list identifies possible injection
   points; it does not provide a Qualcomm backend.

References: the repository URLs in `sources.json`, Qualcomm's
[shared-buffer tutorial](https://docs.qualcomm.com/nav/home/htp_shared_buffer_tutorial.html),
and [HLSL round semantics](https://learn.microsoft.com/en-us/windows/win32/direct3dhlsl/dx-graphics-hlsl-round).
