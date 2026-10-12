// UE4 velocity encoding convention and camera reconstruction cross-checked
// against Filoppi/Luma-Framework (Izueh and Pumbo), Luma_MotionVec_UE4_Decode.
// A pixel stores NDC current-minus-previous motion, biased into UNORM16.
cbuffer Camera : register(b0) {
    row_major float4x4 clipToPrevious;
    float2 renderSize;
    float2 jitterPixels;
};
Texture2D<float> Depth : register(t0);
Texture2D<float4> PackedMotion : register(t1);
RWTexture2D<float2> Motion : register(u0);
[numthreads(8,8,1)]
void main(uint3 id : SV_DispatchThreadID) {
    if (any(id.xy >= uint2(renderSize))) return;
    int2 samplePos=clamp(int2(floor(float2(id.xy)+0.5+jitterPixels)),int2(0,0),int2(renderSize)-1);
    float2 uv=(float2(id.xy)+0.5)/renderSize;
    float4 current=float4(uv*float2(2,-2)+float2(-1,1),Depth[samplePos],1);
    float4 previous=mul(current,clipToPrevious);
    float2 delta=previous.xy/max(abs(previous.w),1e-8)*sign(previous.w)-current.xy;
    float2 encoded=PackedMotion[samplePos].xy;
    if(encoded.x>0)delta=-(encoded-32767.0/65535.0)/(0.499*0.5);
    Motion[id.xy]=delta*float2(0.5,-0.5)*renderSize;
}
