#ifndef HOUJI_FSR4_FRAME_WIRE_H
#define HOUJI_FSR4_FRAME_WIRE_H
#include <stdint.h>
#define FSR4_WIRE_MAGIC 0x34525346u
#define FSR4_WIRE_VERSION 1u
#define FSR4_WIRE_PORT 48621
/* Inputs below the offset, RGBA16F output above it: 32 MiB covers 2048x2048. */
#define FSR4_SHARED_OUTPUT_OFFSET (32u*1024u*1024u)
#define FSR4_SHARED_SIZE (64u*1024u*1024u)
#define FSR4_MAX_OUTPUT 2048u
/* Output sizes the server's stages accept (1080 is not a multiple of 16). */
#define FSR4_OUTPUT_ALIGN 8u
/* Little-endian wire contract. Both tested peers are little-endian.
 * Raw inputs are tightly packed; never send a process pointer or account data. */
typedef struct {
    uint32_t magic,version,sequence,width,height,out_width,out_height;
    uint32_t color_format,depth_format,mv_format;
    uint32_t color_bytes,depth_bytes,mv_bytes,reset;
    float jitter_x,jitter_y,mv_scale_x,mv_scale_y,pre_exposure;
    uint32_t render_width,render_height;
    float clip_to_previous[16];
} Fsr4FrameHeader;
typedef struct { uint32_t magic,sequence,status,bytes; } Fsr4Reply;
#endif
