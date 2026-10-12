#!/usr/bin/env python3
"""Compile AMD preprocessing and reconstruction to Vulkan SPIR-V.

Uses externally fetched AMD sources with their license. The neural graph
replaces the fused model work in post, retaining AMD's reconstruction math.
"""
import argparse
from pathlib import Path
import re
import subprocess

POST_ENTRY = r'''
// Reconstruction math from AMD FidelityFX SDK, Copyright (c) 2025 AMD, MIT.
#include "post_common.hlsli"
ByteAddressBuffer NeuralOutput : register(t21);
[numthreads(8,8,1)]
void main(uint3 tid : SV_DispatchThreadID) {
    if (tid.x >= width || tid.y >= height) return;
    uint4 packed = NeuralOutput.Load4((tid.y * width + tid.x) * 16);
    half4 recurrent = Unpack4h(packed.zw);
    recurrent = 1.0f / (1.0f + exp(-recurrent));
    rw_recurrent_0[tid.xy] = recurrent;
    float4 parameters = Unpack4h(packed.xy);
    float exposure = LoadExposureValue();
    float3 color = apply_model_filter(tid.x, tid.y, parameters, exposure);
    rw_history_color[tid.xy] = color;
    color = clamp(RemoveMuLaw(color) / exposure, 0, 64000);
    OUTPUT_STORE
}
'''
# post-linear writes RGBA16F texels (alpha 0, matching Turnip's store of the
# float3 image) straight into a linear buffer, so the server needs no separate
# image-to-buffer copy before the CPU reads the result.
IMAGE_STORE = 'rw_mlsr_output_color[tid.xy] = color;'
# f32tof16 flushes fp16 denormals, which Turnip's image store keeps
# (truncated, measured byte-exact); color is clamped non-negative, so a
# denormal is trunc(c * 2^24).
LINEAR_STORE = ('uint3 h = f32tof16(color); '
                'h = select(color < 6.103515625e-5, (uint3)(color * 16777216.0), h); '
                'LinearOutput.Store2((tid.y * width + tid.x) * 8, uint2(h.r | (h.g << 16), h.b));')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('sdk',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--dxc',required=True);p.add_argument('--width',type=int,required=True)
    p.add_argument('--height',type=int,required=True)
    p.add_argument('--inverted-depth',action='store_true')
    a=p.parse_args()
    # Pre dispatches 16x16 tiles (rounded up, bounds-checked); post and the 2x
    # strided neural input need multiples of 8 (1080 is not a multiple of 16).
    if min(a.width,a.height)<16 or a.width%8 or a.height%8:
        p.error('output dimensions must be multiples of 8')
    root=a.sdk/'Kits/FidelityFX/upscalers/fsr4'
    a.output.mkdir(parents=True,exist_ok=True)
    # Pre normally writes into the provider's resolution-tier-strided scratch
    # buffer. The standalone QNN graph uses a tightly packed tensor instead.
    pre=(root/'include/gpu/fsr4/pre_common.hlsli').read_text()
    start=pre.index('#if FFX_MLSR_RESOLUTION == RESOLUTION_1080')
    end=pre.index('#endif',start)+len('#endif')
    w,h=a.width//2,a.height//2
    replacement=f'''
        const uint3 logicalSize_slice_0 = uint3({w}, {h}, 16);
        const int3 groupStart_slice_0 = int3(gid.xy, 1) * int3(8, 8, 16);
        const uint3 groupSize_slice_0 = uint3(8, 8, 16);
        const uint3 storageSize_slice_0 = uint3({w}, {h}, 16);
        const uint3 tensorByteStrides_slice_0 = uint3(16, {w*16}, 1);
'''
    pre=pre[:start]+replacement+pre[end:]
    # Share the QNN u8/zero-point-128 representation directly, avoiding a CPU
    # byte-bias pass at the GPU/NPU boundary.
    pre=pre.replace('output.storage.Store4(byteAddress, storageBytes);',
                    'output.storage.Store4(byteAddress, storageBytes ^ uint4(0x80808080, 0x80808080, 0x80808080, 0x80808080));')
    (a.output/'pre_common.hlsli').write_text(pre)
    (a.output/'post-from-qnn.hlsl').write_text(POST_ENTRY.replace('OUTPUT_STORE',IMAGE_STORE))
    (a.output/'post-linear.hlsl').write_text(POST_ENTRY.replace('OUTPUT_STORE',LINEAR_STORE).replace(
        'ByteAddressBuffer NeuralOutput : register(t21);',
        'ByteAddressBuffer NeuralOutput : register(t21);\nRWByteAddressBuffer LinearOutput : register(u22);'))
    # Reserve independent namespaces: b=0, s=16, t=32, u=64. The Metal
    # reference only shifts t/u; its sampler b0 collision is invalid in Vulkan.
    flags=['-spirv','-T','cs_6_6','-enable-16bit-types','-HV','2021','-O3',
           '-fspv-target-env=vulkan1.3','-fvk-s-shift','16','0',
           '-fvk-t-shift','32','0','-fvk-u-shift','64','0','-E','main',
           '-DDATA_TYPE=float','-DDATA_TYPE2=float2','-DDATA_TYPE3=float3',
           '-DDATA_TYPE_VECTOR=float4','-DNETWORK_DATA_TYPE=float16_t',
           '-DFFX_MLSR_RESOLUTION=0','-DFFX_MLSR_DEPTH_INVERTED='+str(int(a.inverted_depth)),
           '-DFFX_MLSR_LOW_RES_MV=1','-DFFX_MLSR_AUTOEXPOSURE_ENABLED=0',
           '-DFFX_MLSR_COLORSPACE=0','-DFFX_MLSR_JITTERED_MOTION_VECTORS=0',
           '-DFFX_DEBUG_VISUALIZE=0']
    includes=[a.output,root/'dx12',root/'include/gpu',root/'include/gpu/fsr4',a.sdk/'Kits/FidelityFX/api/internal/gpu']
    # Use a shadow pre entry so its include chooses our packed-layout version.
    pre_entry=(root/'internal/shaders/fsr4_model_v07_i8_quality/pre.hlsl').read_text()
    (a.output/'pre.hlsl').write_text(pre_entry)
    for name,src in [('pre',a.output/'pre.hlsl'),('post',a.output/'post-from-qnn.hlsl'),('post-linear',a.output/'post-linear.hlsl')]:
        dest=a.output/(name+'.spv')
        subprocess.run([a.dxc,*flags,*['-I'+str(x) for x in includes],str(src),'-Fo',str(dest)],check=True)
        subprocess.run(['spirv-val','--target-env','vulkan1.3',str(dest)],check=True)
    motion=a.output/'decode-motion.spv'
    subprocess.run([a.dxc,*flags,str(Path(__file__).with_name('decode-ue-motion.hlsl')),'-Fo',str(motion)],check=True)
    subprocess.run(['spirv-val','--target-env','vulkan1.3',str(motion)],check=True)
    # Pass-0's 1 KiB constant buffer mirrors the provider's Quality table.
    text=(root/'dx12/ffx_provider_fsr4_dx12.cpp').read_text()
    body=re.search(r'weights_quality\[256\] = \{(.*?)\};',text,re.S)[1]
    import struct
    weights=[int(s,16) for s in re.findall(r'0x[0-9a-fA-F]+',body)]
    assert len(weights)==256
    (a.output/'pass0-weights.bin').write_bytes(struct.pack('<256I',*weights))
    print('Validated Vulkan pre/post for',a.width,a.height)


if __name__=='__main__':main()
