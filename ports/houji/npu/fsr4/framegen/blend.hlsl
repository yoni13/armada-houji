// Milestone-1 placeholder for frame interpolation: the midpoint blend of the
// two most recent real frames. Proves presentation and pacing only.
[[vk::binding(0)]] Texture2D<float4> previous_frame;
[[vk::binding(1)]] Texture2D<float4> current_frame;
[[vk::binding(2)]] [[vk::image_format("rgba16f")]] RWTexture2D<float4> generated;

[numthreads(8, 8, 1)]
void main(uint3 id : SV_DispatchThreadID)
{
    uint w, h;
    generated.GetDimensions(w, h);
    if (id.x >= w || id.y >= h) return;
    generated[id.xy] = 0.5f * (previous_frame[id.xy] + current_frame[id.xy]);
}
