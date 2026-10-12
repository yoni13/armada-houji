/* Native regression client for frame-server. Sends deterministic synthetic
 * 960x540 Goat-format frames (R11G11B10 color, R32G8X24 depth with non-zero
 * stencil bytes, RGBA16 UNORM packed motion) and writes each 1280x720 RGBA16F
 * result, so CPU-copy and zero-copy servers can be compared byte for byte.
 * Usage: test-frame-server PORT SHARED_FILE OUTPUT_PREFIX FRAMES [OUT_W OUT_H] */
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <unistd.h>
#include "frame-wire.h"

static uint32_t state = 0x12345678u;
static uint32_t next(void) { state = state * 1664525u + 1013904223u; return state; }
static int wire(int fd, void *data, size_t size, int sending) {
    char *p = data;
    while (size) {
        ssize_t n = sending ? send(fd, p, size, MSG_NOSIGNAL) : recv(fd, p, size, 0);
        if (n <= 0) return 0;
        p += n; size -= (size_t)n;
    }
    return 1;
}

int main(int argc, char **argv) {
    if (argc != 5 && argc != 7) { fprintf(stderr, "usage: %s PORT SHARED_FILE OUTPUT_PREFIX FRAMES [OUT_W OUT_H]\n", argv[0]); return 2; }
    const unsigned w = 960, h = 540, frames = (unsigned)atoi(argv[4]);
    const unsigned ow = argc == 7 ? (unsigned)atoi(argv[5]) : 1280, oh = argc == 7 ? (unsigned)atoi(argv[6]) : 720;
    const size_t pixels = (size_t)w * h, color = pixels * 4, depth = pixels * 8, motion = pixels * 8;
    int file = open(argv[2], O_RDWR | O_CLOEXEC);
    if (file < 0) { perror("shared file"); return 1; }
    uint8_t *shared = mmap(NULL, FSR4_SHARED_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, file, 0);
    if (shared == MAP_FAILED) { perror("mmap"); return 1; }
    int fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET; address.sin_port = htons((uint16_t)atoi(argv[1]));
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(fd, (struct sockaddr *)&address, sizeof(address))) { perror("connect"); return 1; }
    for (unsigned frame = 1; frame <= frames; ++frame) {
        uint32_t *c = (uint32_t *)shared;
        for (size_t i = 0; i < pixels; ++i) {
            /* Unsigned 11/11/10-bit floats with exponent below the Inf/NaN range. */
            uint32_t r = (next() >> 21) & 0x5ff, g = (next() >> 21) & 0x5ff, b = (next() >> 22) & 0x2ff;
            c[i] = r | g << 11 | b << 22;
        }
        float *d = (float *)(shared + color);
        for (size_t i = 0; i < pixels; ++i) {
            d[i * 2] = (float)(next() >> 8) / 16777216.f;
            ((uint32_t *)d)[i * 2 + 1] = next(); /* stencil/padding garbage must be ignored */
        }
        uint16_t *m = (uint16_t *)(shared + color + depth);
        for (size_t i = 0; i < pixels * 4; ++i) m[i] = (uint16_t)(32768 + (int)(next() % 512) - 256);
        Fsr4FrameHeader f = {0};
        f.magic = FSR4_WIRE_MAGIC; f.version = 4; f.sequence = frame; f.width = w; f.height = h; f.out_width = ow; f.out_height = oh;
        f.color_format = 26; f.depth_format = 19; f.mv_format = 11;
        f.color_bytes = (uint32_t)color; f.depth_bytes = (uint32_t)depth; f.mv_bytes = (uint32_t)motion; f.reset = frame == 1;
        f.jitter_x = .25f * (float)(frame % 3) - .25f; f.jitter_y = .125f * (float)(frame % 2);
        f.mv_scale_x = f.mv_scale_y = f.pre_exposure = 1.f; f.render_width = w; f.render_height = h;
        for (int i = 0; i < 4; ++i) f.clip_to_previous[i * 5] = 1.f;
        __atomic_thread_fence(__ATOMIC_RELEASE);
        Fsr4Reply reply;
        if (!wire(fd, &f, sizeof(f), 1) || !wire(fd, &reply, sizeof(reply), 0) || reply.magic != FSR4_WIRE_MAGIC ||
            reply.sequence != frame || reply.status || reply.bytes != (uint32_t)ow * oh * 8) {
            fprintf(stderr, "frame %u failed\n", frame); return 1;
        }
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
        char path[512]; snprintf(path, sizeof(path), "%s-%u.bin", argv[3], frame);
        FILE *out = fopen(path, "wb");
        if (!out || fwrite(shared + FSR4_SHARED_OUTPUT_OFFSET, 1, reply.bytes, out) != reply.bytes) { perror(path); return 1; }
        fclose(out);
    }
    close(fd); munmap(shared, FSR4_SHARED_SIZE); close(file);
    printf("Completed %u frames\n", frames);
    return 0;
}
