/* Validate the real system-heap -> Turnip -> CPU shared-buffer path.
 * This is a GPU memory test, not a claim of QNN/NPU interoperability. */
#include <errno.h>
#include <fcntl.h>
#include <linux/dma-buf.h>
#include <linux/dma-heap.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

#define VK_OK(call) do { VkResult r = (call); if (r != VK_SUCCESS) { \
    fprintf(stderr, "%s: VkResult %d\n", #call, r); goto out; } } while (0)

int main(void)
{
    int status = 1, heap = -1, fd = -1, import_fd = -1;
    void *cpu = MAP_FAILED;
    VkInstance instance = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    VkPhysicalDevice physical = VK_NULL_HANDLE;
    VkMemoryRequirements requirements = {0};
    const VkDeviceSize size = 1024 * 1024;
    const uint32_t pattern = 0x5a17c0de;
    const VkExternalMemoryHandleTypeFlagBits type = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
    VkApplicationInfo app = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "houji-dmabuf-probe", .apiVersion = VK_API_VERSION_1_1 };
    VkInstanceCreateInfo instance_info = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app };
    VK_OK(vkCreateInstance(&instance_info, NULL, &instance));
    uint32_t count = 0;
    VK_OK(vkEnumeratePhysicalDevices(instance, &count, NULL));
    VkPhysicalDevice *devices = calloc(count, sizeof(*devices));
    if (!devices) goto out;
    VkResult enumerated = vkEnumeratePhysicalDevices(instance, &count, devices);
    if (enumerated != VK_SUCCESS) { free(devices); goto out; }
    for (uint32_t i = 0; i < count; ++i) {
        VkPhysicalDeviceProperties p;
        vkGetPhysicalDeviceProperties(devices[i], &p);
        if (p.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU && p.vendorID == 0x5143) {
            physical = devices[i];
            printf("GPU: %s\n", p.deviceName);
            break;
        }
    }
    free(devices);
    if (!physical) { fprintf(stderr, "No Qualcomm integrated Vulkan GPU\n"); goto out; }
    uint32_t family_count = 0, family = UINT32_MAX;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &family_count, NULL);
    VkQueueFamilyProperties *families = calloc(family_count, sizeof(*families));
    if (!families) goto out;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &family_count, families);
    for (uint32_t i = 0; i < family_count; ++i)
        if (families[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { family = i; break; }
    free(families);
    if (family == UINT32_MAX) goto out;
    float priority = 0.5f;
    VkDeviceQueueCreateInfo queue_info = { .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority };
    const char *extensions[] = { VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME,
                                VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME };
    VkDeviceCreateInfo device_info = { .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info,
        .enabledExtensionCount = 2, .ppEnabledExtensionNames = extensions };
    VK_OK(vkCreateDevice(physical, &device_info, NULL, &device));
    VkQueue queue;
    vkGetDeviceQueue(device, family, 0, &queue);
    const VkBufferUsageFlags usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    VkPhysicalDeviceExternalBufferInfo external_info = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_BUFFER_INFO,
        .usage = usage, .handleType = type };
    VkExternalBufferProperties external_props = { .sType = VK_STRUCTURE_TYPE_EXTERNAL_BUFFER_PROPERTIES };
    vkGetPhysicalDeviceExternalBufferProperties(physical, &external_info, &external_props);
    if (!(external_props.externalMemoryProperties.externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT)) {
        fprintf(stderr, "DMA-BUF storage buffers are not importable\n"); goto out;
    }
    VkExternalMemoryBufferCreateInfo external_buffer = {
        .sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO, .handleTypes = type };
    VkBufferCreateInfo buffer_info = { .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .pNext = &external_buffer, .size = size, .usage = usage, .sharingMode = VK_SHARING_MODE_EXCLUSIVE };
    VK_OK(vkCreateBuffer(device, &buffer_info, NULL, &buffer));
    vkGetBufferMemoryRequirements(device, buffer, &requirements);
    heap = open("/dev/dma_heap/system", O_RDONLY | O_CLOEXEC);
    if (heap < 0) { perror("open system heap"); goto out; }
    struct dma_heap_allocation_data allocation = { .len = requirements.size, .fd_flags = O_RDWR | O_CLOEXEC };
    if (ioctl(heap, DMA_HEAP_IOCTL_ALLOC, &allocation) < 0) { perror("DMA_HEAP_IOCTL_ALLOC"); goto out; }
    fd = allocation.fd;
    cpu = mmap(NULL, requirements.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (cpu == MAP_FAILED) { perror("mmap"); goto out; }
    PFN_vkGetMemoryFdPropertiesKHR fd_props = (PFN_vkGetMemoryFdPropertiesKHR)vkGetDeviceProcAddr(device, "vkGetMemoryFdPropertiesKHR");
    if (!fd_props) goto out;
    VkMemoryFdPropertiesKHR props = { .sType = VK_STRUCTURE_TYPE_MEMORY_FD_PROPERTIES_KHR };
    VK_OK(fd_props(device, type, fd, &props));
    uint32_t types = props.memoryTypeBits & requirements.memoryTypeBits;
    if (!types) { fprintf(stderr, "No compatible DMA-BUF memory type\n"); goto out; }
    import_fd = fcntl(fd, F_DUPFD_CLOEXEC, 0);
    if (import_fd < 0) { perror("dup DMA-BUF"); goto out; }
    VkMemoryDedicatedAllocateInfo dedicated = { .sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO,
        .buffer = buffer };
    VkImportMemoryFdInfoKHR import = { .sType = VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR,
        .pNext = &dedicated, .handleType = type, .fd = import_fd };
    VkMemoryAllocateInfo memory_info = { .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .pNext = &import, .allocationSize = requirements.size, .memoryTypeIndex = __builtin_ctz(types) };
    VK_OK(vkAllocateMemory(device, &memory_info, NULL, &memory));
    import_fd = -1; /* Vulkan owns the imported duplicate after successful allocation. */
    VK_OK(vkBindBufferMemory(device, buffer, memory, 0));
    VkCommandPoolCreateInfo pool_info = { .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = family };
    VK_OK(vkCreateCommandPool(device, &pool_info, NULL, &pool));
    VkCommandBufferAllocateInfo command_info = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1 };
    VkCommandBuffer command;
    VK_OK(vkAllocateCommandBuffers(device, &command_info, &command));
    VkCommandBufferBeginInfo begin = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT };
    VK_OK(vkBeginCommandBuffer(command, &begin));
    vkCmdFillBuffer(command, buffer, 0, size, pattern);
    VkMemoryBarrier barrier = { .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT };
    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
        0, 1, &barrier, 0, NULL, 0, NULL);
    VK_OK(vkEndCommandBuffer(command));
    VkFenceCreateInfo fence_info = { .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    VK_OK(vkCreateFence(device, &fence_info, NULL, &fence));
    VkSubmitInfo submit = { .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1, .pCommandBuffers = &command };
    VK_OK(vkQueueSubmit(queue, 1, &submit, fence));
    VK_OK(vkWaitForFences(device, 1, &fence, VK_TRUE, 5000000000ULL));
    struct dma_buf_sync sync = { .flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ };
    if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync) < 0) { perror("DMA_BUF_SYNC_START"); goto out; }
    size_t mismatches = 0;
    for (size_t i = 0; i < size/4; ++i) mismatches += ((uint32_t *)cpu)[i] != pattern;
    sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
    if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync) < 0) { perror("DMA_BUF_SYNC_END"); goto out; }
    printf("Shared GPU fill: %llu bytes, %zu mismatches\n", (unsigned long long)size, mismatches);
    status = mismatches ? 1 : 0;
out:
    if (device) vkDeviceWaitIdle(device);
    if (fence) vkDestroyFence(device, fence, NULL);
    if (pool) vkDestroyCommandPool(device, pool, NULL);
    if (buffer) vkDestroyBuffer(device, buffer, NULL);
    if (memory) vkFreeMemory(device, memory, NULL);
    if (device) vkDestroyDevice(device, NULL);
    if (instance) vkDestroyInstance(instance, NULL);
    if (cpu != MAP_FAILED) munmap(cpu, requirements.size);
    if (import_fd >= 0) close(import_fd);
    if (fd >= 0) close(fd);
    if (heap >= 0) close(heap);
    return status;
}
