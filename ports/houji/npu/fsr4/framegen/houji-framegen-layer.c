/* Experimental Vulkan frame-generation layer for DXVK games on Houji.
 *
 * Implicit layer, active only in processes started with FSR4_NPU_LIVE=1 (the
 * Goat FSR 4 test launch options). It interposes vkQueuePresentKHR: after each
 * real frame it acquires one more swapchain image, fills it with a generated
 * frame and presents it before the real one. Milestone 1 generates the
 * midpoint blend of the last two real frames; it validates presentation,
 * pacing and cost, not image quality.
 *
 * Runtime control: $HOME/.config/houji-fg/config holds "ENABLED PACE", e.g.
 * "1 0.5". PACE is the fraction of the measured real-frame interval to wait
 * between the generated and the real present (0 = back to back). A missing
 * file means disabled; the layer then only forwards calls.
 * Statistics are appended to $HOME/.cache/houji-fg.log. */
#define _GNU_SOURCE
#include <vulkan/vulkan.h>
#include <vulkan/vk_layer.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include "blend.spv.h"

#define EXPORT __attribute__((visibility("default")))
#define MAX_OBJECTS 16
#define RING 4

static void *key(const void *dispatchable) { return *(void **)dispatchable; }
static double now_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e3 + t.tv_nsec / 1e6; }

static FILE *logfile;
static void note(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void note(const char *fmt, ...)
{
    if (!logfile) {
        const char *home = getenv("HOME");
        char path[512];
        snprintf(path, sizeof(path), "%s/.cache/houji-fg.log", home ? home : "/tmp");
        logfile = fopen(path, "a");
        if (!logfile) return;
    }
    time_t t = time(NULL); char stamp[32]; struct tm tm; localtime_r(&t, &tm); strftime(stamp, sizeof(stamp), "%F %T", &tm);
    fprintf(logfile, "%s ", stamp);
    va_list ap; va_start(ap, fmt); vfprintf(logfile, fmt, ap); va_end(ap);
    fputc('\n', logfile); fflush(logfile);
}

typedef struct {
    void *key;
    VkInstance handle;
    PFN_vkGetInstanceProcAddr gipa;
    PFN_vkDestroyInstance DestroyInstance;
    PFN_vkGetPhysicalDeviceMemoryProperties GetPhysicalDeviceMemoryProperties;
} Instance;

typedef struct { VkImage image; VkDeviceMemory memory; VkImageView view; } Image;

typedef struct {
    VkSwapchainKHR handle;
    int supported;
    VkFormat format;
    VkExtent2D extent;
    uint32_t count;
    VkImage images[16];
    Image history[2], generated;
    int current, have_previous;
    VkDescriptorSet sets[2];
    VkCommandBuffer commands[RING];
    VkFence fences[RING];
    VkSemaphore acquired[RING], generated_ready[RING], real_ready[RING];
    uint32_t ring;
} Swapchain;

#define DEVICE_FUNCTIONS(F) \
    F(DestroyDevice) F(CreateSwapchainKHR) F(DestroySwapchainKHR) F(GetSwapchainImagesKHR) \
    F(AcquireNextImageKHR) F(QueuePresentKHR) F(QueueSubmit) F(CreateCommandPool) F(DestroyCommandPool) \
    F(AllocateCommandBuffers) F(BeginCommandBuffer) F(EndCommandBuffer) F(ResetCommandBuffer) F(CmdPipelineBarrier) \
    F(CmdBlitImage) F(CmdBindPipeline) F(CmdBindDescriptorSets) F(CmdDispatch) F(CreateFence) F(DestroyFence) \
    F(WaitForFences) F(ResetFences) F(CreateSemaphore) F(DestroySemaphore) F(CreateImage) F(DestroyImage) \
    F(GetImageMemoryRequirements) F(AllocateMemory) F(FreeMemory) F(BindImageMemory) F(CreateImageView) \
    F(DestroyImageView) F(CreateShaderModule) F(DestroyShaderModule) F(CreateDescriptorSetLayout) \
    F(DestroyDescriptorSetLayout) F(CreatePipelineLayout) F(DestroyPipelineLayout) F(CreateComputePipelines) \
    F(DestroyPipeline) F(CreateDescriptorPool) F(DestroyDescriptorPool) F(AllocateDescriptorSets) \
    F(FreeDescriptorSets) F(UpdateDescriptorSets) F(DeviceWaitIdle) F(CreateSampler) F(DestroySampler)

typedef struct {
    void *key;
    VkDevice handle;
    VkPhysicalDevice physical;
    Instance *instance;
    PFN_vkSetDeviceLoaderData set_loader_data;
    uint32_t family;
    VkCommandPool pool;
    VkDescriptorSetLayout set_layout;
    VkPipelineLayout pipeline_layout;
    VkPipeline pipeline;
    VkDescriptorPool descriptor_pool;
    VkSampler sampler;
    Swapchain swapchains[4];
    PFN_vkGetDeviceProcAddr GetDeviceProcAddr;
#define DECLARE(name) PFN_vk##name name;
    DEVICE_FUNCTIONS(DECLARE)
#undef DECLARE
} Device;

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static Instance instances[MAX_OBJECTS];
static Device devices[MAX_OBJECTS];

static Instance *find_instance(const void *d) { void *k = key(d); for (int i = 0; i < MAX_OBJECTS; ++i) if (instances[i].key == k) return &instances[i]; return NULL; }
static Device *find_device(const void *d) { void *k = key(d); for (int i = 0; i < MAX_OBJECTS; ++i) if (devices[i].key == k) return &devices[i]; return NULL; }

/* ---- runtime configuration and statistics ---- */
static int cfg_enabled; static double cfg_pace; static double cfg_checked; static time_t cfg_mtime = -1;
static void read_config(void)
{
    double t = now_ms();
    if (t - cfg_checked < 1000) return;
    cfg_checked = t;
    const char *home = getenv("HOME"); char path[512];
    snprintf(path, sizeof(path), "%s/.config/houji-fg/config", home ? home : "/tmp");
    struct stat st;
    if (stat(path, &st)) { if (cfg_enabled) note("config removed: frame generation off"); cfg_enabled = 0; cfg_mtime = -1; return; }
    if (st.st_mtime == cfg_mtime) return;
    cfg_mtime = st.st_mtime;
    FILE *f = fopen(path, "r"); int e = 0; double p = 0;
    if (f) {
        char line[64] = { 0 }, *end = NULL;
        if (fgets(line, sizeof(line), f)) { e = (int)strtod(line, &end); /* strtol maps to GLIBC_2.38 __isoc23_strtol */ if (end && *end) p = strtod(end, NULL); }
        fclose(f);
    }
    cfg_enabled = e == 1; cfg_pace = p < 0 ? 0 : p > 0.9 ? 0.9 : p;
    note("config: enabled=%d pace=%.2f", cfg_enabled, cfg_pace);
}
static struct { unsigned real, generated, skipped; double last_real, interval, last_log; } stats;

/* ---- instance ---- */
static VkResult VKAPI_CALL CreateInstance(const VkInstanceCreateInfo *info, const VkAllocationCallbacks *alloc, VkInstance *out)
{
    VkLayerInstanceCreateInfo *chain = (VkLayerInstanceCreateInfo *)info->pNext;
    while (chain && !(chain->sType == VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO && chain->function == VK_LAYER_LINK_INFO))
        chain = (VkLayerInstanceCreateInfo *)chain->pNext;
    if (!chain) return VK_ERROR_INITIALIZATION_FAILED;
    PFN_vkGetInstanceProcAddr gipa = chain->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    chain->u.pLayerInfo = chain->u.pLayerInfo->pNext;
    PFN_vkCreateInstance create = (PFN_vkCreateInstance)gipa(VK_NULL_HANDLE, "vkCreateInstance");
    VkResult r = create(info, alloc, out);
    if (r != VK_SUCCESS) return r;
    pthread_mutex_lock(&lock);
    for (int i = 0; i < MAX_OBJECTS; ++i) if (!instances[i].key) {
        Instance *in = &instances[i];
        in->key = key(*out); in->handle = *out; in->gipa = gipa;
        in->DestroyInstance = (PFN_vkDestroyInstance)gipa(*out, "vkDestroyInstance");
        in->GetPhysicalDeviceMemoryProperties = (PFN_vkGetPhysicalDeviceMemoryProperties)gipa(*out, "vkGetPhysicalDeviceMemoryProperties");
        break;
    }
    pthread_mutex_unlock(&lock);
    return r;
}

static void VKAPI_CALL DestroyInstance(VkInstance instance, const VkAllocationCallbacks *alloc)
{
    pthread_mutex_lock(&lock);
    Instance *in = find_instance(instance);
    PFN_vkDestroyInstance destroy = in ? in->DestroyInstance : NULL;
    if (in) memset(in, 0, sizeof(*in));
    pthread_mutex_unlock(&lock);
    if (destroy) destroy(instance, alloc);
}

/* ---- device ---- */
static VkResult VKAPI_CALL CreateDevice(VkPhysicalDevice physical, const VkDeviceCreateInfo *info, const VkAllocationCallbacks *alloc, VkDevice *out)
{
    VkLayerDeviceCreateInfo *chain = (VkLayerDeviceCreateInfo *)info->pNext, *callback = NULL;
    for (VkLayerDeviceCreateInfo *c = chain; c; c = (VkLayerDeviceCreateInfo *)c->pNext)
        if (c->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO && c->function == VK_LOADER_DATA_CALLBACK) callback = c;
    while (chain && !(chain->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO && chain->function == VK_LAYER_LINK_INFO))
        chain = (VkLayerDeviceCreateInfo *)chain->pNext;
    if (!chain) return VK_ERROR_INITIALIZATION_FAILED;
    PFN_vkGetInstanceProcAddr gipa = chain->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    PFN_vkGetDeviceProcAddr gdpa = chain->u.pLayerInfo->pfnNextGetDeviceProcAddr;
    chain->u.pLayerInfo = chain->u.pLayerInfo->pNext;
    PFN_vkCreateDevice create = (PFN_vkCreateDevice)gipa(VK_NULL_HANDLE, "vkCreateDevice");
    VkResult r = create(physical, info, alloc, out);
    if (r != VK_SUCCESS) return r;
    pthread_mutex_lock(&lock);
    Device *d = NULL;
    for (int i = 0; i < MAX_OBJECTS; ++i) if (!devices[i].key) { d = &devices[i]; break; }
    if (d) {
        memset(d, 0, sizeof(*d));
        d->key = key(*out); d->handle = *out; d->physical = physical;
        d->instance = find_instance(physical);
        d->set_loader_data = callback ? callback->u.pfnSetDeviceLoaderData : NULL;
        d->family = info->queueCreateInfoCount ? info->pQueueCreateInfos[0].queueFamilyIndex : 0;
        d->GetDeviceProcAddr = gdpa;
#define LOAD(name) d->name = (PFN_vk##name)gdpa(*out, "vk" #name);
        DEVICE_FUNCTIONS(LOAD)
#undef LOAD
        note("device created: queue family %u, loader callback %s, instance %s", d->family, d->set_loader_data ? "yes" : "no", d->instance ? "found" : "missing");
    }
    pthread_mutex_unlock(&lock);
    return r;
}

static void destroy_swapchain_state(Device *d, Swapchain *s);

static void VKAPI_CALL DestroyDevice(VkDevice device, const VkAllocationCallbacks *alloc)
{
    pthread_mutex_lock(&lock);
    Device *d = find_device(device);
    PFN_vkDestroyDevice destroy = d ? d->DestroyDevice : NULL;
    if (d) {
        d->DeviceWaitIdle(device);
        for (int i = 0; i < 4; ++i) if (d->swapchains[i].handle) destroy_swapchain_state(d, &d->swapchains[i]);
        if (d->pipeline) d->DestroyPipeline(device, d->pipeline, NULL);
        if (d->pipeline_layout) d->DestroyPipelineLayout(device, d->pipeline_layout, NULL);
        if (d->set_layout) d->DestroyDescriptorSetLayout(device, d->set_layout, NULL);
        if (d->descriptor_pool) d->DestroyDescriptorPool(device, d->descriptor_pool, NULL);
        if (d->sampler) d->DestroySampler(device, d->sampler, NULL);
        if (d->pool) d->DestroyCommandPool(device, d->pool, NULL);
        memset(d, 0, sizeof(*d));
    }
    pthread_mutex_unlock(&lock);
    if (destroy) destroy(device, alloc);
}

/* ---- resources ---- */
static int memory_type(Device *d, uint32_t bits, VkMemoryPropertyFlags flags)
{
    if (!d->instance) return -1;
    VkPhysicalDeviceMemoryProperties p;
    d->instance->GetPhysicalDeviceMemoryProperties(d->physical, &p);
    for (uint32_t i = 0; i < p.memoryTypeCount; ++i) if ((bits & (1u << i)) && (p.memoryTypes[i].propertyFlags & flags) == flags) return (int)i;
    return -1;
}

static int make_image(Device *d, VkExtent2D e, Image *out)
{
    VkImageCreateInfo ci = { VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO };
    ci.imageType = VK_IMAGE_TYPE_2D; ci.format = VK_FORMAT_R16G16B16A16_SFLOAT; ci.extent = (VkExtent3D){ e.width, e.height, 1 };
    ci.mipLevels = 1; ci.arrayLayers = 1; ci.samples = VK_SAMPLE_COUNT_1_BIT; ci.tiling = VK_IMAGE_TILING_OPTIMAL;
    ci.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    if (d->CreateImage(d->handle, &ci, NULL, &out->image)) return 0;
    VkMemoryRequirements r; d->GetImageMemoryRequirements(d->handle, out->image, &r);
    int type = memory_type(d, r.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if (type < 0) return 0;
    VkMemoryAllocateInfo mi = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, r.size, (uint32_t)type };
    if (d->AllocateMemory(d->handle, &mi, NULL, &out->memory) || d->BindImageMemory(d->handle, out->image, out->memory, 0)) return 0;
    VkImageViewCreateInfo vi = { VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO };
    vi.image = out->image; vi.viewType = VK_IMAGE_VIEW_TYPE_2D; vi.format = ci.format;
    vi.subresourceRange = (VkImageSubresourceRange){ VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
    return d->CreateImageView(d->handle, &vi, NULL, &out->view) == VK_SUCCESS;
}

static void free_image(Device *d, Image *i)
{
    if (i->view) d->DestroyImageView(d->handle, i->view, NULL);
    if (i->image) d->DestroyImage(d->handle, i->image, NULL);
    if (i->memory) d->FreeMemory(d->handle, i->memory, NULL);
    memset(i, 0, sizeof(*i));
}

static int ensure_pipeline(Device *d)
{
    if (d->pipeline) return 1;
    VkCommandPoolCreateInfo pi = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, d->family };
    if (!d->pool && d->CreateCommandPool(d->handle, &pi, NULL, &d->pool)) return 0;
    VkSamplerCreateInfo si = { VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO };
    si.magFilter = si.minFilter = VK_FILTER_NEAREST; si.addressModeU = si.addressModeV = si.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    if (!d->sampler && d->CreateSampler(d->handle, &si, NULL, &d->sampler)) return 0;
    VkDescriptorSetLayoutBinding b[3] = {
        { 0, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE, 1, VK_SHADER_STAGE_COMPUTE_BIT, NULL },
        { 1, VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE, 1, VK_SHADER_STAGE_COMPUTE_BIT, NULL },
        { 2, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1, VK_SHADER_STAGE_COMPUTE_BIT, NULL },
    };
    VkDescriptorSetLayoutCreateInfo li = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, NULL, 0, 3, b };
    if (!d->set_layout && d->CreateDescriptorSetLayout(d->handle, &li, NULL, &d->set_layout)) return 0;
    VkPipelineLayoutCreateInfo pli = { VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, NULL, 0, 1, &d->set_layout, 0, NULL };
    if (!d->pipeline_layout && d->CreatePipelineLayout(d->handle, &pli, NULL, &d->pipeline_layout)) return 0;
    VkDescriptorPoolSize sizes[2] = { { VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE, 16 }, { VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 8 } };
    VkDescriptorPoolCreateInfo dp = { VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, NULL, VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT, 8, 2, sizes };
    if (!d->descriptor_pool && d->CreateDescriptorPool(d->handle, &dp, NULL, &d->descriptor_pool)) return 0;
    VkShaderModuleCreateInfo sm = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, NULL, 0, sizeof(blend_spv), blend_spv };
    VkShaderModule module;
    if (d->CreateShaderModule(d->handle, &sm, NULL, &module)) return 0;
    VkComputePipelineCreateInfo ci = { VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO };
    ci.stage = (VkPipelineShaderStageCreateInfo){ VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, NULL, 0, VK_SHADER_STAGE_COMPUTE_BIT, module, "main", NULL };
    ci.layout = d->pipeline_layout;
    VkResult r = d->CreateComputePipelines(d->handle, VK_NULL_HANDLE, 1, &ci, NULL, &d->pipeline);
    d->DestroyShaderModule(d->handle, module, NULL);
    if (r) { d->pipeline = VK_NULL_HANDLE; return 0; }
    return 1;
}

static void destroy_swapchain_state(Device *d, Swapchain *s)
{
    for (int i = 0; i < RING; ++i) {
        if (s->fences[i]) d->DestroyFence(d->handle, s->fences[i], NULL);
        if (s->acquired[i]) d->DestroySemaphore(d->handle, s->acquired[i], NULL);
        if (s->generated_ready[i]) d->DestroySemaphore(d->handle, s->generated_ready[i], NULL);
        if (s->real_ready[i]) d->DestroySemaphore(d->handle, s->real_ready[i], NULL);
    }
    if (s->sets[0]) d->FreeDescriptorSets(d->handle, d->descriptor_pool, 2, s->sets);
    free_image(d, &s->history[0]); free_image(d, &s->history[1]); free_image(d, &s->generated);
    memset(s, 0, sizeof(*s));
}

static int setup_swapchain(Device *d, Swapchain *s)
{
    if (!ensure_pipeline(d)) { note("pipeline setup failed"); return 0; }
    if (!make_image(d, s->extent, &s->history[0]) || !make_image(d, s->extent, &s->history[1]) || !make_image(d, s->extent, &s->generated)) { note("image setup failed"); return 0; }
    VkDescriptorSetLayout layouts[2] = { d->set_layout, d->set_layout };
    VkDescriptorSetAllocateInfo ai = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, NULL, d->descriptor_pool, 2, layouts };
    if (d->AllocateDescriptorSets(d->handle, &ai, s->sets)) { s->sets[0] = s->sets[1] = VK_NULL_HANDLE; return 0; }
    for (int i = 0; i < 2; ++i) {
        /* Set i blends history[!i] (previous) with history[i] (current). */
        VkDescriptorImageInfo images[3] = {
            { d->sampler, s->history[!i].view, VK_IMAGE_LAYOUT_GENERAL },
            { d->sampler, s->history[i].view, VK_IMAGE_LAYOUT_GENERAL },
            { VK_NULL_HANDLE, s->generated.view, VK_IMAGE_LAYOUT_GENERAL },
        };
        VkWriteDescriptorSet w[3];
        for (int k = 0; k < 3; ++k) {
            w[k] = (VkWriteDescriptorSet){ VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET };
            w[k].dstSet = s->sets[i]; w[k].dstBinding = (uint32_t)k; w[k].descriptorCount = 1;
            w[k].descriptorType = k < 2 ? VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE : VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
            w[k].pImageInfo = &images[k];
        }
        d->UpdateDescriptorSets(d->handle, 3, w, 0, NULL);
    }
    VkCommandBufferAllocateInfo ci = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, d->pool, VK_COMMAND_BUFFER_LEVEL_PRIMARY, RING };
    if (d->AllocateCommandBuffers(d->handle, &ci, s->commands)) return 0;
    for (int i = 0; i < RING; ++i) {
        /* Command buffers made inside a layer need the loader's dispatch pointer. */
        if (d->set_loader_data) d->set_loader_data(d->handle, s->commands[i]);
        VkFenceCreateInfo fi = { VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, NULL, VK_FENCE_CREATE_SIGNALED_BIT };
        VkSemaphoreCreateInfo si = { VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO };
        if (d->CreateFence(d->handle, &fi, NULL, &s->fences[i]) || d->CreateSemaphore(d->handle, &si, NULL, &s->acquired[i]) ||
            d->CreateSemaphore(d->handle, &si, NULL, &s->generated_ready[i]) || d->CreateSemaphore(d->handle, &si, NULL, &s->real_ready[i])) return 0;
    }
    return 1;
}

/* ---- swapchain ---- */
static VkResult VKAPI_CALL CreateSwapchainKHR(VkDevice device, const VkSwapchainCreateInfoKHR *info, const VkAllocationCallbacks *alloc, VkSwapchainKHR *out)
{
    Device *d = find_device(device);
    VkSwapchainCreateInfoKHR mine = *info;
    /* One extra image for the generated frame; blits read and write swapchain images. */
    mine.minImageCount += 1;
    mine.imageUsage |= VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    VkResult r = d->CreateSwapchainKHR(device, &mine, alloc, out);
    int supported = r == VK_SUCCESS;
    if (!supported) { note("swapchain with extra image/usages refused (%d); forwarding unchanged", r); r = d->CreateSwapchainKHR(device, info, alloc, out); }
    if (r != VK_SUCCESS) return r;
    pthread_mutex_lock(&lock);
    for (int i = 0; i < 4; ++i) if (info->oldSwapchain && d->swapchains[i].handle == info->oldSwapchain) { d->DeviceWaitIdle(device); destroy_swapchain_state(d, &d->swapchains[i]); }
    Swapchain *s = NULL;
    for (int i = 0; i < 4; ++i) if (!d->swapchains[i].handle) { s = &d->swapchains[i]; break; }
    if (s) {
        s->handle = *out; s->format = info->imageFormat; s->extent = info->imageExtent;
        s->count = 16; d->GetSwapchainImagesKHR(device, *out, &s->count, s->images);
        s->supported = supported && setup_swapchain(d, s);
        note("swapchain %ux%u format %d images %u (asked %u) present mode %d: frame generation %s", s->extent.width, s->extent.height,
             s->format, s->count, mine.minImageCount, info->presentMode, s->supported ? "available" : "unavailable");
    }
    pthread_mutex_unlock(&lock);
    return r;
}

static void VKAPI_CALL DestroySwapchainKHR(VkDevice device, VkSwapchainKHR swapchain, const VkAllocationCallbacks *alloc)
{
    Device *d = find_device(device);
    pthread_mutex_lock(&lock);
    for (int i = 0; i < 4; ++i) if (swapchain && d->swapchains[i].handle == swapchain) { d->DeviceWaitIdle(device); destroy_swapchain_state(d, &d->swapchains[i]); }
    pthread_mutex_unlock(&lock);
    d->DestroySwapchainKHR(device, swapchain, alloc);
}

static void barrier(Device *d, VkCommandBuffer c, VkImage image, VkImageLayout from, VkImageLayout to, VkAccessFlags src, VkAccessFlags dst,
                    VkPipelineStageFlags src_stage, VkPipelineStageFlags dst_stage)
{
    VkImageMemoryBarrier b = { VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, NULL, src, dst, from, to, VK_QUEUE_FAMILY_IGNORED, VK_QUEUE_FAMILY_IGNORED, image,
                               { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 } };
    d->CmdPipelineBarrier(c, src_stage, dst_stage, 0, 0, NULL, 0, NULL, 1, &b);
}

static void blit(Device *d, VkCommandBuffer c, VkImage src, VkImage dst, VkExtent2D e)
{
    VkImageBlit region = { { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 }, { { 0, 0, 0 }, { (int32_t)e.width, (int32_t)e.height, 1 } },
                           { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 }, { { 0, 0, 0 }, { (int32_t)e.width, (int32_t)e.height, 1 } } };
    d->CmdBlitImage(c, src, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, dst, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &region, VK_FILTER_NEAREST);
}

static VkResult VKAPI_CALL QueuePresentKHR(VkQueue queue, const VkPresentInfoKHR *info)
{
    /* VkQueue shares its device's dispatch key. */
    Device *d = find_device(queue);
    if (!d) return VK_ERROR_DEVICE_LOST;
    read_config();
    Swapchain *s = NULL;
    if (info->swapchainCount == 1) for (int i = 0; i < 4; ++i) if (d->swapchains[i].handle == info->pSwapchains[0]) s = &d->swapchains[i];
    double t = now_ms();
    if (stats.last_real) { double dt = t - stats.last_real; stats.interval = stats.interval ? stats.interval * 0.9 + dt * 0.1 : dt; }
    stats.last_real = t; ++stats.real;
    if (t - stats.last_log > 10000) {
        if (stats.last_log) note("real=%u generated=%u skipped=%u real_interval_ms=%.2f enabled=%d pace=%.2f", stats.real, stats.generated, stats.skipped, stats.interval, cfg_enabled, cfg_pace);
        stats.last_log = t;
    }
    if (!s || !s->supported || !cfg_enabled) { if (s) s->have_previous = 0; return d->QueuePresentKHR(queue, info); }

    uint32_t r = s->ring++ % RING, real_index = info->pImageIndices[0], extra = 0;
    d->WaitForFences(d->handle, 1, &s->fences[r], VK_TRUE, UINT64_MAX);
    VkResult acquired = d->AcquireNextImageKHR(d->handle, s->handle, 20000000ull, s->acquired[r], VK_NULL_HANDLE, &extra);
    if (acquired != VK_SUCCESS && acquired != VK_SUBOPTIMAL_KHR) { ++stats.skipped; s->have_previous = 0; return d->QueuePresentKHR(queue, info); }
    d->ResetFences(d->handle, 1, &s->fences[r]);

    VkCommandBuffer c = s->commands[r];
    d->ResetCommandBuffer(c, 0);
    VkCommandBufferBeginInfo bi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, NULL, VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT, NULL };
    d->BeginCommandBuffer(c, &bi);
    VkImage real = s->images[real_index], target = s->images[extra];
    Image *current = &s->history[s->current];
    const VkPipelineStageFlags T = VK_PIPELINE_STAGE_TRANSFER_BIT, C = VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT;
    /* Capture the real frame. */
    barrier(d, c, real, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, 0, VK_ACCESS_TRANSFER_READ_BIT, T, T);
    barrier(d, c, current->image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0, VK_ACCESS_TRANSFER_WRITE_BIT, C, T);
    blit(d, c, real, current->image, s->extent);
    barrier(d, c, target, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0, VK_ACCESS_TRANSFER_WRITE_BIT, T, T);
    if (s->have_previous) {
        barrier(d, c, current->image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_GENERAL, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT, T, C);
        barrier(d, c, s->generated.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL, 0, VK_ACCESS_SHADER_WRITE_BIT, T, C);
        d->CmdBindPipeline(c, VK_PIPELINE_BIND_POINT_COMPUTE, d->pipeline);
        d->CmdBindDescriptorSets(c, VK_PIPELINE_BIND_POINT_COMPUTE, d->pipeline_layout, 0, 1, &s->sets[s->current], 0, NULL);
        d->CmdDispatch(c, (s->extent.width + 7) / 8, (s->extent.height + 7) / 8, 1);
        barrier(d, c, s->generated.image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_ACCESS_SHADER_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT, C, T);
        blit(d, c, s->generated.image, target, s->extent);
    } else {
        /* First frame after (re)enabling: repeat the real frame. */
        blit(d, c, real, target, s->extent);
        barrier(d, c, current->image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_GENERAL, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT, T, C);
    }
    barrier(d, c, real, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR, VK_ACCESS_TRANSFER_READ_BIT, 0, T, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT);
    barrier(d, c, target, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR, VK_ACCESS_TRANSFER_WRITE_BIT, 0, T, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT);
    d->EndCommandBuffer(c);

    /* Consume the application's wait semaphores plus our acquire. */
    VkSemaphore waits[17]; VkPipelineStageFlags stages[17]; uint32_t n = 0;
    for (uint32_t i = 0; i < info->waitSemaphoreCount && n < 16; ++i) { waits[n] = info->pWaitSemaphores[i]; stages[n++] = VK_PIPELINE_STAGE_ALL_COMMANDS_BIT; }
    waits[n] = s->acquired[r]; stages[n++] = T;
    VkSemaphore signals[2] = { s->generated_ready[r], s->real_ready[r] };
    VkSubmitInfo si = { VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, n, waits, stages, 1, &c, 2, signals };
    VkResult sr = d->QueueSubmit(queue, 1, &si, s->fences[r]);
    if (sr != VK_SUCCESS) { note("submit failed %d; disabling", sr); s->supported = 0; return sr; }

    VkPresentInfoKHR gen = { VK_STRUCTURE_TYPE_PRESENT_INFO_KHR, NULL, 1, &s->generated_ready[r], 1, &s->handle, &extra, NULL };
    VkResult gr = d->QueuePresentKHR(queue, &gen);
    if (gr == VK_SUCCESS || gr == VK_SUBOPTIMAL_KHR) ++stats.generated;
    if (cfg_pace > 0 && stats.interval > 0 && stats.interval < 100) {
        double until = now_ms() + stats.interval * cfg_pace;
        while (now_ms() < until) { struct timespec ts = { 0, 250000 }; nanosleep(&ts, NULL); }
    }
    VkPresentInfoKHR real_info = *info;
    real_info.waitSemaphoreCount = 1; real_info.pWaitSemaphores = &s->real_ready[r];
    s->current ^= 1; s->have_previous = 1;
    return d->QueuePresentKHR(queue, &real_info);
}

/* ---- dispatch ---- */
static PFN_vkVoidFunction VKAPI_CALL GetDeviceProcAddr(VkDevice device, const char *name);
static PFN_vkVoidFunction device_intercept(const char *name)
{
#define I(n) if (!strcmp(name, "vk" #n)) return (PFN_vkVoidFunction)n;
    I(GetDeviceProcAddr) I(DestroyDevice) I(CreateSwapchainKHR) I(DestroySwapchainKHR) I(QueuePresentKHR)
#undef I
    return NULL;
}

static PFN_vkVoidFunction VKAPI_CALL GetInstanceProcAddr(VkInstance instance, const char *name)
{
    if (!strcmp(name, "vkGetInstanceProcAddr")) return (PFN_vkVoidFunction)GetInstanceProcAddr;
    if (!strcmp(name, "vkCreateInstance")) return (PFN_vkVoidFunction)CreateInstance;
    if (!strcmp(name, "vkDestroyInstance")) return (PFN_vkVoidFunction)DestroyInstance;
    if (!strcmp(name, "vkCreateDevice")) return (PFN_vkVoidFunction)CreateDevice;
    PFN_vkVoidFunction f = device_intercept(name);
    if (f) return f;
    if (!instance) return NULL;
    Instance *in = find_instance(instance);
    return in ? in->gipa(instance, name) : NULL;
}

static PFN_vkVoidFunction VKAPI_CALL GetDeviceProcAddr(VkDevice device, const char *name)
{
    PFN_vkVoidFunction f = device_intercept(name);
    if (f) return f;
    Device *d = find_device(device);
    return d ? d->GetDeviceProcAddr(device, name) : NULL;
}

EXPORT VkResult VKAPI_CALL vkNegotiateLoaderLayerInterfaceVersion(VkNegotiateLayerInterface *v)
{
    if (v->loaderLayerInterfaceVersion > 2) v->loaderLayerInterfaceVersion = 2;
    v->pfnGetInstanceProcAddr = GetInstanceProcAddr;
    v->pfnGetDeviceProcAddr = GetDeviceProcAddr;
    v->pfnGetPhysicalDeviceProcAddr = NULL;
    return VK_SUCCESS;
}
