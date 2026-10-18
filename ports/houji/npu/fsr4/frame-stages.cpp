// Standalone Vulkan pre/post validation using externally compiled AMD shaders.
// Files are raw little-endian data; no game injection is performed here.
#include <vulkan/vulkan.h>
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
#include <fcntl.h>
#include <unistd.h>

static void check(VkResult r,const char *what) { if(r!=VK_SUCCESS) throw std::runtime_error(std::string(what)+": "+std::to_string(r)); }
#define VK(call) check(call,#call)
static std::vector<uint8_t> read(const std::string &path,size_t expected=0) {
    std::ifstream f(path,std::ios::binary|std::ios::ate);
    if(!f)throw std::runtime_error("Cannot read "+path);
    size_t size=static_cast<size_t>(f.tellg());
    if(expected&&size!=expected)throw std::runtime_error("Wrong size: "+path);
    std::vector<uint8_t>b(size);f.seekg(0);f.read(reinterpret_cast<char*>(b.data()),size);
    if(!f)throw std::runtime_error("Short read: "+path);
    return b;
}
static void write(const std::string &path,const void *data,size_t size) {
    std::ofstream f(path,std::ios::binary);f.write(static_cast<const char*>(data),size);
    if(!f)throw std::runtime_error("Cannot write "+path);
}

struct Constants {
    float inv_size[2],scale[2],inv_scale[2],jitter[2],mv_scale[2];
    float tex_size[2],max_render_size[2],mv_jitter[2];
    uint32_t width,height,reset,width_lr,height_lr;
    float pre_exposure,previous_pre_exposure;
    uint32_t rcas_enabled;
    float rcas_sharpness,pad;
};
static_assert(sizeof(Constants)==104);
struct Buffer { VkBuffer handle{};VkDeviceMemory memory{};void *mapped{};size_t size{};bool map_owned{true}; };
struct Image { VkImage handle{};VkDeviceMemory memory{};VkImageView view{};uint32_t width{},height{};size_t pixel{};bool initialized{}; };

class GPU {
public:
    VkInstance instance{};VkPhysicalDevice physical{};VkDevice device{};VkQueue queue{};
    uint32_t family{};VkCommandPool pool{};VkCommandBuffer command{};
    std::vector<Buffer> buffers;std::vector<Image> images;
    std::map<size_t,size_t> uploads,downloads;
    VkSampler sampler{};VkDescriptorSetLayout layout{};VkDescriptorPool descriptors{};
    VkPipelineLayout pipeline_layout{};VkPipeline pipeline{};VkShaderModule shader{};
    struct Pipeline { VkDescriptorSetLayout layout;VkDescriptorPool descriptors;VkPipelineLayout pipeline_layout;VkPipeline pipeline;VkShaderModule shader;VkDescriptorSet set; };
    std::map<std::string,Pipeline> pipelines;
    // While batching, helpers record into one command buffer; end_batch submits
    // once. Each pipeline's descriptor set may be dispatched once per batch.
    bool batching=false,recording=false;
    // Optional GPU timestamps: stamp() records one, read_stamps() returns the
    // intervals between consecutive stamps in milliseconds and starts over.
    VkQueryPool timestamps{};float timestamp_period=0;uint32_t stamp_count=0;
    static constexpr uint32_t max_stamps=16;
    void enable_timestamps(){
        VkPhysicalDeviceProperties p;vkGetPhysicalDeviceProperties(physical,&p);
        uint32_t n=0;vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,nullptr);std::vector<VkQueueFamilyProperties> qs(n);vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,qs.data());
        if(!qs[family].timestampValidBits||p.limits.timestampPeriod<=0){std::cout<<"GPU timestamps unsupported on this queue\n";return;}
        timestamp_period=p.limits.timestampPeriod;
        VkQueryPoolCreateInfo qi{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};qi.queryType=VK_QUERY_TYPE_TIMESTAMP;qi.queryCount=max_stamps;VK(vkCreateQueryPool(device,&qi,nullptr,&timestamps));
    }
    void stamp(){
        if(!timestamps||stamp_count>=max_stamps)return;
        begin();if(!stamp_count)vkCmdResetQueryPool(command,timestamps,0,max_stamps);
        vkCmdWriteTimestamp(command,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,timestamps,stamp_count++);
    }
    std::vector<double> read_stamps(){
        std::vector<double> out;if(!timestamps||stamp_count<2){stamp_count=0;return out;}
        std::vector<uint64_t> t(stamp_count);
        if(vkGetQueryPoolResults(device,timestamps,0,stamp_count,t.size()*8,t.data(),8,VK_QUERY_RESULT_64_BIT)==VK_SUCCESS)
            for(uint32_t i=1;i<stamp_count;++i)out.push_back((double)(t[i]-t[i-1])*timestamp_period/1e6);
        stamp_count=0;return out;
    }
    ~GPU(){
        if(device)vkDeviceWaitIdle(device);
        if(timestamps)vkDestroyQueryPool(device,timestamps,nullptr);
        for(auto &[name,p]:pipelines){(void)name;vkDestroyPipeline(device,p.pipeline,nullptr);vkDestroyShaderModule(device,p.shader,nullptr);vkDestroyPipelineLayout(device,p.pipeline_layout,nullptr);vkDestroyDescriptorPool(device,p.descriptors,nullptr);vkDestroyDescriptorSetLayout(device,p.layout,nullptr);}
        if(sampler)vkDestroySampler(device,sampler,nullptr);
        for(auto &i:images){vkDestroyImageView(device,i.view,nullptr);vkDestroyImage(device,i.handle,nullptr);vkFreeMemory(device,i.memory,nullptr);}
        for(auto &b:buffers){if(b.mapped&&b.map_owned)vkUnmapMemory(device,b.memory);vkDestroyBuffer(device,b.handle,nullptr);vkFreeMemory(device,b.memory,nullptr);}
        if(pool)vkDestroyCommandPool(device,pool,nullptr);
        if(device)vkDestroyDevice(device,nullptr);
        if(instance)vkDestroyInstance(instance,nullptr);
    }
    void init(){
        VkApplicationInfo a{VK_STRUCTURE_TYPE_APPLICATION_INFO};a.pApplicationName="FSR4 HTP validation";a.apiVersion=VK_API_VERSION_1_3;
        VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};ci.pApplicationInfo=&a;VK(vkCreateInstance(&ci,nullptr,&instance));
        uint32_t n=0;VK(vkEnumeratePhysicalDevices(instance,&n,nullptr));std::vector<VkPhysicalDevice> ds(n);VK(vkEnumeratePhysicalDevices(instance,&n,ds.data()));
        for(auto d:ds){VkPhysicalDeviceProperties p;vkGetPhysicalDeviceProperties(d,&p);if(p.vendorID==0x5143&&p.deviceType==VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU){physical=d;std::cout<<p.deviceName<<'\n';break;}}
        if(!physical)throw std::runtime_error("Qualcomm integrated GPU missing");
        vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,nullptr);std::vector<VkQueueFamilyProperties> qs(n);vkGetPhysicalDeviceQueueFamilyProperties(physical,&n,qs.data());
        family=UINT32_MAX;for(uint32_t i=0;i<n;++i)if(qs[i].queueFlags&VK_QUEUE_COMPUTE_BIT){family=i;break;}
        if(family==UINT32_MAX)throw std::runtime_error("Compute queue missing");
        VkPhysicalDeviceComputeShaderDerivativesFeaturesKHR derivatives{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_COMPUTE_SHADER_DERIVATIVES_FEATURES_KHR};
        derivatives.computeDerivativeGroupLinear=VK_TRUE;
        VkPhysicalDeviceVulkan13Features f13{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES};f13.pNext=&derivatives;f13.robustImageAccess=VK_TRUE;
        VkPhysicalDeviceVulkan12Features f12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES};f12.pNext=&f13;f12.shaderFloat16=VK_TRUE;f12.shaderInt8=VK_TRUE;
        VkPhysicalDeviceFeatures features{};features.robustBufferAccess=VK_TRUE;features.shaderStorageImageWriteWithoutFormat=VK_TRUE;features.shaderStorageImageExtendedFormats=VK_TRUE;
        float priority=.5f;VkDeviceQueueCreateInfo qi{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};qi.queueFamilyIndex=family;qi.queueCount=1;qi.pQueuePriorities=&priority;
        std::vector<const char*> ext{VK_KHR_COMPUTE_SHADER_DERIVATIVES_EXTENSION_NAME,VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME,VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME};
        uint32_t count=0;VK(vkEnumerateDeviceExtensionProperties(physical,nullptr,&count,nullptr));std::vector<VkExtensionProperties> available(count);VK(vkEnumerateDeviceExtensionProperties(physical,nullptr,&count,available.data()));
        auto supported=[&](const char *name){for(auto &e:available)if(!std::strcmp(e.extensionName,name))return true;return false;};
        VkDeviceCreateInfo di{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};di.pNext=&f12;di.pEnabledFeatures=&features;di.queueCreateInfoCount=1;di.pQueueCreateInfos=&qi;di.enabledExtensionCount=ext.size();di.ppEnabledExtensionNames=ext.data();
        // Opt-in: a higher msm ring lets these short passes preempt game work.
        const char *requested=getenv("FSR4_GPU_PRIORITY");
        VkDeviceQueueGlobalPriorityCreateInfoKHR global{VK_STRUCTURE_TYPE_DEVICE_QUEUE_GLOBAL_PRIORITY_CREATE_INFO_KHR};global.globalPriority=VK_QUEUE_GLOBAL_PRIORITY_HIGH_KHR;
        VkResult created=VK_ERROR_INITIALIZATION_FAILED;
        if(requested&&!std::strcmp(requested,"high")&&supported(VK_KHR_GLOBAL_PRIORITY_EXTENSION_NAME)){
            ext.push_back(VK_KHR_GLOBAL_PRIORITY_EXTENSION_NAME);di.enabledExtensionCount=ext.size();di.ppEnabledExtensionNames=ext.data();
            qi.pNext=&global;created=vkCreateDevice(physical,&di,nullptr,&device);qi.pNext=nullptr;
            ext.pop_back();di.enabledExtensionCount=ext.size();di.ppEnabledExtensionNames=ext.data();
        }
        if(requested&&!std::strcmp(requested,"high"))std::cout<<"High GPU queue priority "<<(created==VK_SUCCESS?"enabled":"refused; using default")<<'\n';
        if(created!=VK_SUCCESS)VK(vkCreateDevice(physical,&di,nullptr,&device));
        vkGetDeviceQueue(device,family,0,&queue);
        VkCommandPoolCreateInfo pi{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};pi.queueFamilyIndex=family;pi.flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;VK(vkCreateCommandPool(device,&pi,nullptr,&pool));
        VkCommandBufferAllocateInfo ai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};ai.commandPool=pool;ai.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY;ai.commandBufferCount=1;VK(vkAllocateCommandBuffers(device,&ai,&command));
    }
    uint32_t memory_type(uint32_t bits,VkMemoryPropertyFlags flags){
        VkPhysicalDeviceMemoryProperties p;vkGetPhysicalDeviceMemoryProperties(physical,&p);
        for(uint32_t i=0;i<p.memoryTypeCount;++i)if((bits&(1u<<i))&&(p.memoryTypes[i].propertyFlags&flags)==flags)return i;
        throw std::runtime_error("Memory type missing");
    }
    // Readback buffers ask for HOST_CACHED: Turnip's first coherent type is
    // uncached, where a 7 MB CPU read took about 4 ms.
    size_t buffer(size_t size,VkBufferUsageFlags usage,VkMemoryPropertyFlags extra=0){
        Buffer b{};b.size=size;VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};bi.size=size;bi.usage=usage;bi.sharingMode=VK_SHARING_MODE_EXCLUSIVE;
        VK(vkCreateBuffer(device,&bi,nullptr,&b.handle));VkMemoryRequirements r;vkGetBufferMemoryRequirements(device,b.handle,&r);
        const VkMemoryPropertyFlags base=VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
        uint32_t type;try{type=memory_type(r.memoryTypeBits,base|extra);}catch(const std::runtime_error&){type=memory_type(r.memoryTypeBits,base);}
        VkMemoryAllocateInfo mi{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};mi.allocationSize=r.size;mi.memoryTypeIndex=type;
        VK(vkAllocateMemory(device,&mi,nullptr,&b.memory));VK(vkBindBufferMemory(device,b.handle,b.memory,0));VK(vkMapMemory(device,b.memory,0,size,0,&b.mapped));std::memset(b.mapped,0,size);
        buffers.push_back(b);return buffers.size()-1;
    }
    size_t image(uint32_t w,uint32_t h,VkFormat format,size_t bytes){
        Image i{};i.width=w;i.height=h;i.pixel=bytes;
        VkImageCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};ci.imageType=VK_IMAGE_TYPE_2D;ci.format=format;ci.extent={w,h,1};ci.mipLevels=1;ci.arrayLayers=1;ci.samples=VK_SAMPLE_COUNT_1_BIT;ci.tiling=VK_IMAGE_TILING_OPTIMAL;ci.usage=VK_IMAGE_USAGE_STORAGE_BIT|VK_IMAGE_USAGE_SAMPLED_BIT|VK_IMAGE_USAGE_TRANSFER_SRC_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT;ci.sharingMode=VK_SHARING_MODE_EXCLUSIVE;
        VK(vkCreateImage(device,&ci,nullptr,&i.handle));VkMemoryRequirements r;vkGetImageMemoryRequirements(device,i.handle,&r);
        VkMemoryAllocateInfo mi{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};mi.allocationSize=r.size;mi.memoryTypeIndex=memory_type(r.memoryTypeBits,0);VK(vkAllocateMemory(device,&mi,nullptr,&i.memory));VK(vkBindImageMemory(device,i.handle,i.memory,0));
        VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};vi.image=i.handle;vi.viewType=VK_IMAGE_VIEW_TYPE_2D;vi.format=format;vi.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};VK(vkCreateImageView(device,&vi,nullptr,&i.view));images.push_back(i);return images.size()-1;
    }
    size_t import_buffer(size_t size,int fd,void *cpu){
        Buffer b{};b.size=size;b.mapped=cpu;b.map_owned=false;
        VkExternalMemoryBufferCreateInfo external{VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO};external.handleTypes=VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
        VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};bi.pNext=&external;bi.size=size;bi.usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;bi.sharingMode=VK_SHARING_MODE_EXCLUSIVE;
        VK(vkCreateBuffer(device,&bi,nullptr,&b.handle));VkMemoryRequirements r;vkGetBufferMemoryRequirements(device,b.handle,&r);
        VkMemoryFdPropertiesKHR props{VK_STRUCTURE_TYPE_MEMORY_FD_PROPERTIES_KHR};auto get=(PFN_vkGetMemoryFdPropertiesKHR)vkGetDeviceProcAddr(device,"vkGetMemoryFdPropertiesKHR");
        VK(get(device,VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,fd,&props));uint32_t bits=props.memoryTypeBits&r.memoryTypeBits;
        if(!bits)throw std::runtime_error("No DMA-BUF memory type");
        int copy=fcntl(fd,F_DUPFD_CLOEXEC,0);if(copy<0)throw std::runtime_error("DMA-BUF dup failed");
        VkMemoryDedicatedAllocateInfo dedicated{VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO};dedicated.buffer=b.handle;
        VkImportMemoryFdInfoKHR im{VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR};im.pNext=&dedicated;im.handleType=VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;im.fd=copy;
        VkMemoryAllocateInfo mi{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};mi.pNext=&im;mi.allocationSize=r.size;mi.memoryTypeIndex=__builtin_ctz(bits);
        VkResult result=vkAllocateMemory(device,&mi,nullptr,&b.memory);if(result!=VK_SUCCESS)close(copy);check(result,"vkAllocateMemory DMA-BUF");
        VK(vkBindBufferMemory(device,b.handle,b.memory,0));buffers.push_back(b);return buffers.size()-1;
    }
    void external_buffer_barrier(size_t index,bool acquire){
        begin();VkBufferMemoryBarrier barrier{VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER};barrier.buffer=buffers[index].handle;barrier.size=VK_WHOLE_SIZE;
        barrier.srcQueueFamilyIndex=acquire?VK_QUEUE_FAMILY_EXTERNAL:family;barrier.dstQueueFamilyIndex=acquire?family:VK_QUEUE_FAMILY_EXTERNAL;
        // The NPU reads/writes these DMA-BUFs outside Vulkan. State its accesses as
        // memory accesses so the GPU flushes its writes before the NPU runs and
        // invalidates stale lines afterwards (0 here let 1080p frames diverge).
        barrier.srcAccessMask=acquire?VK_ACCESS_MEMORY_WRITE_BIT:VK_ACCESS_SHADER_WRITE_BIT|VK_ACCESS_SHADER_READ_BIT;
        barrier.dstAccessMask=acquire?VK_ACCESS_SHADER_READ_BIT|VK_ACCESS_SHADER_WRITE_BIT:VK_ACCESS_MEMORY_READ_BIT|VK_ACCESS_MEMORY_WRITE_BIT;
        vkCmdPipelineBarrier(command,acquire?VK_PIPELINE_STAGE_ALL_COMMANDS_BIT:VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
            acquire?VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT:VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,0,0,nullptr,1,&barrier,0,nullptr);submit();
    }
    void begin(){if(recording)return;VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};bi.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;VK(vkBeginCommandBuffer(command,&bi));recording=true;}
    void submit(){if(batching)return;flush();}
    void flush(){if(!recording)return;recording=false;VK(vkEndCommandBuffer(command));VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};si.commandBufferCount=1;si.pCommandBuffers=&command;VK(vkQueueSubmit(queue,1,&si,VK_NULL_HANDLE));VK(vkQueueWaitIdle(queue));VK(vkResetCommandBuffer(command,0));}
    void begin_batch(){flush();batching=true;}
    void end_batch(){batching=false;flush();}
    void upload_data(size_t index,const void *data,size_t bytes){
        auto &i=images[index];size_t size=static_cast<size_t>(i.width)*i.height*i.pixel;
        if(data&&bytes!=size)throw std::runtime_error("Image upload size mismatch");
        if(!uploads.count(index))uploads[index]=buffer(size,VK_BUFFER_USAGE_TRANSFER_SRC_BIT);
        size_t b=uploads[index];if(data)std::memcpy(buffers[b].mapped,data,size);else std::memset(buffers[b].mapped,0,size);
        copy_buffer_to_image(index,b,0);
    }
    void copy_buffer_to_image(size_t index,size_t buffer,VkDeviceSize offset){
        auto &i=images[index];
        begin();VkImageMemoryBarrier barrier{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};barrier.oldLayout=i.initialized?VK_IMAGE_LAYOUT_GENERAL:VK_IMAGE_LAYOUT_UNDEFINED;barrier.newLayout=VK_IMAGE_LAYOUT_GENERAL;barrier.srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED;barrier.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED;barrier.image=i.handle;barrier.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};barrier.srcAccessMask=i.initialized?(VK_ACCESS_MEMORY_READ_BIT|VK_ACCESS_MEMORY_WRITE_BIT):0;barrier.dstAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;
        vkCmdPipelineBarrier(command,i.initialized?VK_PIPELINE_STAGE_ALL_COMMANDS_BIT:VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,0,nullptr,0,nullptr,1,&barrier);
        VkBufferImageCopy copy{};copy.bufferOffset=offset;copy.imageSubresource={VK_IMAGE_ASPECT_COLOR_BIT,0,0,1};copy.imageExtent={i.width,i.height,1};vkCmdCopyBufferToImage(command,buffers[buffer].handle,i.handle,VK_IMAGE_LAYOUT_GENERAL,1,&copy);
        barrier.oldLayout=VK_IMAGE_LAYOUT_GENERAL;barrier.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;barrier.dstAccessMask=VK_ACCESS_SHADER_READ_BIT|VK_ACCESS_SHADER_WRITE_BIT;
        vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,0,0,nullptr,0,nullptr,1,&barrier);submit();i.initialized=true;
    }

    void upload(size_t index,const std::vector<uint8_t>&data){upload_data(index,data.empty()?nullptr:data.data(),data.size());}
    const Buffer &download_buffer(size_t index){
        auto &i=images[index];size_t size=static_cast<size_t>(i.width)*i.height*i.pixel;
        if(!downloads.count(index))downloads[index]=buffer(size,VK_BUFFER_USAGE_TRANSFER_DST_BIT,VK_MEMORY_PROPERTY_HOST_CACHED_BIT);
        size_t b=downloads[index];
        begin();VkMemoryBarrier barrier{VK_STRUCTURE_TYPE_MEMORY_BARRIER};barrier.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT;barrier.dstAccessMask=VK_ACCESS_TRANSFER_READ_BIT;
        vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,1,&barrier,0,nullptr,0,nullptr);
        VkBufferImageCopy copy{};copy.imageSubresource={VK_IMAGE_ASPECT_COLOR_BIT,0,0,1};copy.imageExtent={i.width,i.height,1};vkCmdCopyImageToBuffer(command,i.handle,VK_IMAGE_LAYOUT_GENERAL,buffers[b].handle,1,&copy);
        barrier.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT;barrier.dstAccessMask=VK_ACCESS_HOST_READ_BIT;vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&barrier,0,nullptr,0,nullptr);submit();return buffers[b];
    }
    void download(size_t index,const std::string &path){const auto &b=download_buffer(index);write(path,b.mapped,b.size);}
    void dispatch(const std::string &spv,const std::map<uint32_t,size_t>&uniforms,const std::map<uint32_t,size_t>&storage,const std::map<uint32_t,size_t>&sampled,const std::map<uint32_t,size_t>&writable,uint32_t gx,uint32_t gy,bool needs_sampler){
        VkDescriptorSet set{};
        bool cached=pipelines.count(spv);
        if(cached){auto &p=pipelines.at(spv);layout=p.layout;descriptors=p.descriptors;pipeline_layout=p.pipeline_layout;pipeline=p.pipeline;shader=p.shader;set=p.set;}
        if(!cached){
        std::vector<VkDescriptorSetLayoutBinding> bindings;
        auto add=[&](const auto&m,VkDescriptorType t){for(auto &[n,i]:m){(void)i;bindings.push_back({n,t,1,VK_SHADER_STAGE_COMPUTE_BIT,nullptr});}};
        add(uniforms,VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER);add(storage,VK_DESCRIPTOR_TYPE_STORAGE_BUFFER);add(sampled,VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE);add(writable,VK_DESCRIPTOR_TYPE_STORAGE_IMAGE);
        if(needs_sampler)bindings.push_back({16,VK_DESCRIPTOR_TYPE_SAMPLER,1,VK_SHADER_STAGE_COMPUTE_BIT,nullptr});
        VkDescriptorSetLayoutCreateInfo li{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};li.bindingCount=bindings.size();li.pBindings=bindings.data();VK(vkCreateDescriptorSetLayout(device,&li,nullptr,&layout));
        VkDescriptorPoolSize sizes[]={{VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,8},{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,8},{VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,16},{VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,16},{VK_DESCRIPTOR_TYPE_SAMPLER,1}};
        VkDescriptorPoolCreateInfo pi{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};pi.maxSets=1;pi.poolSizeCount=5;pi.pPoolSizes=sizes;VK(vkCreateDescriptorPool(device,&pi,nullptr,&descriptors));
        VkDescriptorSetAllocateInfo ai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};ai.descriptorPool=descriptors;ai.descriptorSetCount=1;ai.pSetLayouts=&layout;VK(vkAllocateDescriptorSets(device,&ai,&set));
        }
        auto bindbuffer=[&](const auto &m,VkDescriptorType type){for(auto &[n,index]:m){auto &b=buffers[index];VkDescriptorBufferInfo info{b.handle,0,b.size};VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};w.dstSet=set;w.dstBinding=n;w.descriptorCount=1;w.descriptorType=type;w.pBufferInfo=&info;vkUpdateDescriptorSets(device,1,&w,0,nullptr);}};
        auto bindimage=[&](const auto &m,VkDescriptorType type){for(auto &[n,index]:m){VkDescriptorImageInfo info{VK_NULL_HANDLE,images[index].view,VK_IMAGE_LAYOUT_GENERAL};VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};w.dstSet=set;w.dstBinding=n;w.descriptorCount=1;w.descriptorType=type;w.pImageInfo=&info;vkUpdateDescriptorSets(device,1,&w,0,nullptr);}};
        bindbuffer(uniforms,VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER);bindbuffer(storage,VK_DESCRIPTOR_TYPE_STORAGE_BUFFER);bindimage(sampled,VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE);bindimage(writable,VK_DESCRIPTOR_TYPE_STORAGE_IMAGE);
        if(needs_sampler){if(!sampler){VkSamplerCreateInfo si{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};si.magFilter=si.minFilter=VK_FILTER_LINEAR;si.mipmapMode=VK_SAMPLER_MIPMAP_MODE_NEAREST;si.addressModeU=si.addressModeV=si.addressModeW=VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;si.maxLod=0;VK(vkCreateSampler(device,&si,nullptr,&sampler));}VkDescriptorImageInfo info{sampler,VK_NULL_HANDLE,VK_IMAGE_LAYOUT_UNDEFINED};VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};w.dstSet=set;w.dstBinding=16;w.descriptorCount=1;w.descriptorType=VK_DESCRIPTOR_TYPE_SAMPLER;w.pImageInfo=&info;vkUpdateDescriptorSets(device,1,&w,0,nullptr);}
        if(!cached){
        VkPipelineLayoutCreateInfo pli{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};pli.setLayoutCount=1;pli.pSetLayouts=&layout;VK(vkCreatePipelineLayout(device,&pli,nullptr,&pipeline_layout));
        auto bytes=read(spv);VkShaderModuleCreateInfo sm{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};sm.codeSize=bytes.size();sm.pCode=reinterpret_cast<const uint32_t*>(bytes.data());VK(vkCreateShaderModule(device,&sm,nullptr,&shader));
        VkComputePipelineCreateInfo pci{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};pci.layout=pipeline_layout;pci.stage={VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,nullptr,0,VK_SHADER_STAGE_COMPUTE_BIT,shader,"main",nullptr};VK(vkCreateComputePipelines(device,VK_NULL_HANDLE,1,&pci,nullptr,&pipeline));
        pipelines.emplace(spv,Pipeline{layout,descriptors,pipeline_layout,pipeline,shader,set});
        }
        begin();
        VkMemoryBarrier before{VK_STRUCTURE_TYPE_MEMORY_BARRIER};before.srcAccessMask=VK_ACCESS_MEMORY_WRITE_BIT;before.dstAccessMask=VK_ACCESS_SHADER_READ_BIT|VK_ACCESS_SHADER_WRITE_BIT;
        vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,0,1,&before,0,nullptr,0,nullptr);
        vkCmdBindPipeline(command,VK_PIPELINE_BIND_POINT_COMPUTE,pipeline);vkCmdBindDescriptorSets(command,VK_PIPELINE_BIND_POINT_COMPUTE,pipeline_layout,0,1,&set,0,nullptr);vkCmdDispatch(command,gx,gy,1);
        VkMemoryBarrier barrier{VK_STRUCTURE_TYPE_MEMORY_BARRIER};barrier.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT;barrier.dstAccessMask=VK_ACCESS_HOST_READ_BIT;vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&barrier,0,nullptr,0,nullptr);submit();
    }
};

#ifndef FSR4_NO_MAIN
int main(int argc,char **argv){
    const char *enabled=getenv("HOUJI_FSR4_EXPERIMENTAL");
    if(!enabled||strcmp(enabled,"1")){fprintf(stderr,"Houji FSR4 experiment is disabled\n");return 1;}
    try{
        if(argc!=5)throw std::runtime_error("Usage: frame-stages pre|post SHADER_DIR FRAME_DIR STATE_DIR");
        std::string mode=argv[1],shaders=argv[2],frame=argv[3],state=argv[4];
        if(mode!="pre"&&mode!="post")throw std::runtime_error("Unknown stage");
        auto data=read(frame+"/constants.bin",sizeof(Constants));Constants c;std::memcpy(&c,data.data(),sizeof(c));
        if(!c.width||!c.height||c.width%16||c.height%16||!c.width_lr||!c.height_lr||c.width>4096||c.height>4096)throw std::runtime_error("Invalid frame dimensions");
        std::filesystem::create_directories(state);GPU g;g.init();
        size_t constants=g.buffer(sizeof(c),VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT);std::memcpy(g.buffers[constants].mapped,&c,sizeof(c));
        size_t color=g.image(c.width_lr,c.height_lr,VK_FORMAT_R16G16B16A16_SFLOAT,8);g.upload(color,read(frame+"/color.bin",c.width_lr*c.height_lr*8));
        float exposure=1;size_t exp=g.image(1,1,VK_FORMAT_R32_SFLOAT,4);std::vector<uint8_t> eb(4);std::memcpy(eb.data(),&exposure,4);g.upload(exp,eb);
        auto stateimage=[&](const char *name,VkFormat format,size_t bytes){size_t id=g.image(c.width,c.height,format,bytes);auto p=state+"/"+name;g.upload(id,std::filesystem::exists(p)?read(p,c.width*c.height*bytes):std::vector<uint8_t>{});return id;};
        size_t history=stateimage("history.bin",VK_FORMAT_R16G16B16A16_SFLOAT,8);
        size_t recurrent=stateimage("recurrent.bin",VK_FORMAT_R8G8B8A8_UNORM,4);
        size_t reprojected=stateimage("reprojected.bin",VK_FORMAT_R16G16B16A16_SFLOAT,8);
        if(mode=="pre"){
            size_t depth=g.image(c.width_lr,c.height_lr,VK_FORMAT_R32_SFLOAT,4);g.upload(depth,read(frame+"/depth.bin",c.width_lr*c.height_lr*4));
            size_t mv=g.image(c.width_lr,c.height_lr,VK_FORMAT_R16G16_SFLOAT,4);g.upload(mv,read(frame+"/mv.bin",c.width_lr*c.height_lr*4));
            size_t weights=g.buffer(1024,VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT);auto wb=read(shaders+"/pass0-weights.bin",1024);std::memcpy(g.buffers[weights].mapped,wb.data(),1024);
            size_t scratch=g.buffer(static_cast<size_t>(c.width)*c.height*4,VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
            g.dispatch(shaders+"/pre.spv",{{0,constants},{1,weights}},{{75,scratch}},{{32,history},{33,mv},{34,depth},{35,color},{36,recurrent},{38,exp}},{{67,reprojected}},c.width/16,c.height/16,true);
            write(state+"/features-u8.bin",g.buffers[scratch].mapped,g.buffers[scratch].size);
            auto *b=static_cast<uint8_t*>(g.buffers[scratch].mapped);for(size_t i=0;i<g.buffers[scratch].size;++i)b[i]^=128;
            write(state+"/features-s8.bin",b,g.buffers[scratch].size);g.download(reprojected,state+"/reprojected.bin");
        }else{
            size_t params=g.buffer(static_cast<size_t>(c.width)*c.height*16,VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);auto b=read(state+"/neural.bin",g.buffers[params].size);std::memcpy(g.buffers[params].mapped,b.data(),b.size());
            size_t output=g.image(c.width,c.height,VK_FORMAT_R16G16B16A16_SFLOAT,8);g.upload(output,{});
            g.dispatch(shaders+"/post.spv",{{0,constants}},{{53,params}},{{35,color},{38,exp},{41,reprojected}},{{65,history},{66,output},{70,recurrent}},c.width/8,c.height/8,false);
            g.download(output,state+"/output.bin");g.download(history,state+"/history.bin");g.download(recurrent,state+"/recurrent.bin");
        }
        std::cout<<mode<<" complete "<<c.width<<"x"<<c.height<<'\n';return 0;
    }catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 1;}
}
#endif
