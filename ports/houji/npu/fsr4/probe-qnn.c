/* Native Linux HTP smoke test: a quantized Add graph, no CPU fallback.
 * Build against user-supplied QAIRT headers; runtime libraries stay external. */
#include <dlfcn.h>
#include <stdarg.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <sys/ioctl.h>
#include <linux/dma-buf.h>
#include "QnnInterface.h"
#include "HTP/QnnHtpDevice.h"

typedef Qnn_ErrorHandle_t (*GetProvidersFn)(const QnnInterface_t ***, uint32_t *);

static void logger(const char *format, QnnLog_Level_t level, uint64_t timestamp, va_list args)
{
    (void)timestamp;
    fprintf(stderr, "QNN[%d] ", level);
    vfprintf(stderr, format, args);
    fputc('\n', stderr);
}

static Qnn_Tensor_t tensor(const char *name, Qnn_TensorType_t type, uint32_t *dims)
{
    Qnn_Tensor_t t = QNN_TENSOR_INIT;
    t.v1.name = name;
    t.v1.type = type;
    t.v1.dataType = QNN_DATATYPE_UFIXED_POINT_8;
    t.v1.rank = 4;
    t.v1.dimensions = dims;
    t.v1.quantizeParams.encodingDefinition = QNN_DEFINITION_DEFINED;
    t.v1.quantizeParams.quantizationEncoding = QNN_QUANTIZATION_ENCODING_SCALE_OFFSET;
    t.v1.quantizeParams.scaleOffsetEncoding.scale = 0.125f;
    t.v1.quantizeParams.scaleOffsetEncoding.offset = -128;
    t.v1.memType = QNN_TENSORMEMTYPE_RAW;
    return t;
}

#define CHECK(call) do { Qnn_ErrorHandle_t r = (call); \
    printf("%s: 0x%llx\n", #call, (unsigned long long)r); fflush(stdout); \
    if (r != QNN_SUCCESS) goto out; } while (0)

int main(int argc, char **argv)
{
    int shared = argc == 2 && strcmp(argv[1], "--shared") == 0;
    if (argc > 1 && !shared) { fprintf(stderr, "Usage: probe-qnn [--shared]\n"); return 2; }
    int status = 1;
    void *library = dlopen("libQnnHtp.so", RTLD_NOW | RTLD_LOCAL);
    if (!library) { fprintf(stderr, "%s\n", dlerror()); return 1; }
    GetProvidersFn providers_fn = (GetProvidersFn)dlsym(library, "QnnInterface_getProviders");
    const QnnInterface_t **providers = NULL;
    uint32_t count = 0;
    QNN_INTERFACE_VER_TYPE api = {0};
    Qnn_LogHandle_t log = NULL;
    Qnn_BackendHandle_t backend = NULL;
    Qnn_DeviceHandle_t device = NULL;
    Qnn_ContextHandle_t context = NULL;
    Qnn_GraphHandle_t graph = NULL;
    void *rpc = NULL, *buffers[3] = {NULL};
    Qnn_MemHandle_t handles[3] = {NULL};
    uint32_t registered = 0;
    void *(*rpc_alloc)(int, uint32_t, int) = NULL;
    void (*rpc_free)(void *) = NULL;
    int (*rpc_fd)(void *) = NULL;
    if (!providers_fn) goto out;
    CHECK(providers_fn(&providers, &count));
    for (uint32_t i = 0; i < count; ++i) {
        if (providers[i]->apiVersion.coreApiVersion.major == QNN_API_VERSION_MAJOR &&
            providers[i]->apiVersion.coreApiVersion.minor >= QNN_API_VERSION_MINOR) {
            api = providers[i]->QNN_INTERFACE_VER_NAME;
            printf("HTP provider: %s, API %u.%u\n", providers[i]->providerName,
                providers[i]->apiVersion.coreApiVersion.major, providers[i]->apiVersion.coreApiVersion.minor);
            break;
        }
    }
    if (!api.backendCreate) { fprintf(stderr, "No compatible QNN interface\n"); goto out; }
    CHECK(api.logCreate(logger, QNN_LOG_LEVEL_INFO, &log));
    CHECK(api.backendCreate(log, NULL, &backend));
    QnnHtpDevice_CustomConfig_t htp = {.option = QNN_HTP_DEVICE_CONFIG_OPTION_ARCH,
                                      .arch = {.deviceId = 0, .arch = QNN_HTP_DEVICE_ARCH_V75}};
    QnnDevice_Config_t device_config = QNN_DEVICE_CONFIG_INIT;
    device_config.option = QNN_DEVICE_CONFIG_OPTION_CUSTOM;
    device_config.customConfig = &htp;
    const QnnDevice_Config_t *device_configs[] = {&device_config, NULL};
    CHECK(api.deviceCreate(log, device_configs, &device));
    CHECK(api.contextCreate(backend, device, NULL, &context));
    CHECK(api.graphCreate(context, "houji_htp_add", NULL, &graph));
    uint32_t dims[] = {1, 4, 4, 16};
    uint8_t a[256], b[256], output[256] = {0};
    for (unsigned i = 0; i < 256; ++i) { a[i] = 96 + i%32; b[i] = 128 + i%16; }
    Qnn_Tensor_t inputs[] = {tensor("a", QNN_TENSOR_TYPE_APP_WRITE, dims),
                             tensor("b", QNN_TENSOR_TYPE_APP_WRITE, dims)};
    Qnn_Tensor_t result = tensor("sum", QNN_TENSOR_TYPE_APP_READ, dims);
    CHECK(api.tensorCreateGraphTensor(graph, &inputs[0]));
    CHECK(api.tensorCreateGraphTensor(graph, &inputs[1]));
    CHECK(api.tensorCreateGraphTensor(graph, &result));
    Qnn_OpConfig_t op = QNN_OPCONFIG_INIT;
    op.v1.name = "add";
    op.v1.packageName = "qti.aisw";
    op.v1.typeName = "ElementWiseAdd";
    op.v1.numOfInputs = 2;
    op.v1.inputTensors = inputs;
    op.v1.numOfOutputs = 1;
    op.v1.outputTensors = &result;
    CHECK(api.graphAddNode(graph, op));
    CHECK(api.graphFinalize(graph, NULL, NULL));
    inputs[0].v1.clientBuf = (Qnn_ClientBuffer_t){a, sizeof(a)};
    inputs[1].v1.clientBuf = (Qnn_ClientBuffer_t){b, sizeof(b)};
    result.v1.clientBuf = (Qnn_ClientBuffer_t){output, sizeof(output)};
    if (shared) {
        rpc = dlopen("libcdsprpc.so", RTLD_NOW | RTLD_LOCAL);
        if (!rpc) { fprintf(stderr, "%s\n", dlerror()); goto out; }
        rpc_alloc = (void *(*)(int, uint32_t, int))dlsym(rpc, "rpcmem_alloc");
        rpc_free = (void (*)(void *))dlsym(rpc, "rpcmem_free");
        rpc_fd = (int (*)(void *))dlsym(rpc, "rpcmem_to_fd");
        if (!rpc_alloc || !rpc_free || !rpc_fd) goto out;
        Qnn_Tensor_t *tensors[] = {&inputs[0], &inputs[1], &result};
        for (unsigned i = 0; i < 3; ++i) {
            buffers[i] = rpc_alloc(25, 1, 256);
            if (!buffers[i]) goto out;
            int fd = rpc_fd(buffers[i]);
            if (fd < 0) goto out;
            Qnn_MemDescriptor_t desc = QNN_MEM_DESCRIPTOR_INIT;
            desc.memShape.numDim = 4;
            desc.memShape.dimSize = dims;
            desc.dataType = QNN_DATATYPE_UFIXED_POINT_8;
            desc.memType = QNN_MEM_TYPE_ION;
            desc.ionInfo.fd = fd;
            CHECK(api.memRegister(context, &desc, 1, &handles[i]));
            ++registered;
            struct dma_buf_sync sync = { .flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE };
            if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync) < 0) { perror("sync write start"); goto out; }
            memcpy(buffers[i], tensors[i]->v1.clientBuf.data, 256);
            sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE;
            if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync) < 0) { perror("sync write end"); goto out; }
            tensors[i]->v1.memType = QNN_TENSORMEMTYPE_MEMHANDLE;
            tensors[i]->v1.memHandle = handles[i];
        }
    }
    struct timespec start, end;
    clock_gettime(CLOCK_MONOTONIC, &start);
    CHECK(api.graphExecute(graph, inputs, 2, &result, 1, NULL, NULL));
    clock_gettime(CLOCK_MONOTONIC, &end);
    if (shared) {
        int fd = rpc_fd(buffers[2]);
        struct dma_buf_sync sync = { .flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ };
        if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync) < 0) { perror("sync read start"); goto out; }
        memcpy(output, buffers[2], sizeof(output));
        sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
        if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync) < 0) { perror("sync read end"); goto out; }
    }
    unsigned mismatches = 0;
    for (unsigned i = 0; i < 256; ++i) mismatches += output[i] != (unsigned)(a[i]+b[i]-128);
    printf("HTP quantized Add (%s): %u mismatches, %.3f ms\n", shared ? "DMA-BUF handles" : "raw buffers", mismatches,
           (end.tv_sec-start.tv_sec)*1000.0+(end.tv_nsec-start.tv_nsec)/1e6);
    status = mismatches ? 1 : 0;
out:
    if (registered) api.memDeRegister(handles, registered);
    if (context) api.contextFree(context, NULL);
    if (device) api.deviceFree(device);
    if (backend) api.backendFree(backend);
    if (log) api.logFree(log);
    for (unsigned i = 0; i < 3; ++i) if (buffers[i] && rpc_free) rpc_free(buffers[i]);
    if (rpc) dlclose(rpc);
    dlclose(library);
    return status;
}
