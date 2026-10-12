/* Do not invoke this under ReShade. ReShade hooks ID3D11Resource::GetDevice to
 * return its proxy device; DXVK's GetVulkanImageInfo static_casts that result
 * to its own D3D11Device and calls LockImage, which faulted (reading 0x6140).
 * test-dxvk-interop.cpp succeeds on the same Proton build without ReShade.
 * Read-only interface capability probe. GUIDs/method layout from DXVK's
 * public dxgi_interfaces.h (zlib license, Philip Rebohle and contributors). */
#include <vulkan/vulkan.h>
struct HoujiVkSurface;
struct HoujiVkDevice : IUnknown {
    virtual void STDMETHODCALLTYPE GetVulkanHandles(VkInstance*,VkPhysicalDevice*,VkDevice*)=0;
    virtual void STDMETHODCALLTYPE GetSubmissionQueue(VkQueue*,uint32_t*)=0;
    virtual void STDMETHODCALLTYPE TransitionSurfaceLayout(HoujiVkSurface*,const VkImageSubresourceRange*,VkImageLayout,VkImageLayout)=0;
    virtual void STDMETHODCALLTYPE FlushRenderingCommands()=0;
    virtual void STDMETHODCALLTYPE LockSubmissionQueue()=0;
    virtual void STDMETHODCALLTYPE ReleaseSubmissionQueue()=0;
};
struct HoujiVkSurface : IUnknown {
    virtual HRESULT STDMETHODCALLTYPE GetDevice(HoujiVkDevice**)=0;
    virtual HRESULT STDMETHODCALLTYPE GetVulkanImageInfo(VkImage*,VkImageLayout*,VkImageCreateInfo*)=0;
};
static const GUID houji_device_iid={0xe2ef5fa5,0xdc21,0x4af7,{0x90,0xc4,0xf6,0x7e,0xf6,0xa0,0x93,0x23}};
static const GUID houji_surface_iid={0x5546cf8c,0x77e7,0x4341,{0xb0,0x5d,0x8d,0x4d,0x50,0x00,0xe7,0x7d}};

static void probe_interop(ID3D11DeviceContext*ctx,ID3D11Resource**resources){
    static bool tested=false;if(tested)return;tested=true;
    FILE*f=fopen("C:\\fsr4-goat-taa\\interop.log","w");if(!f)return;
    setvbuf(f,NULL,_IONBF,0);
    ID3D11Device*d=nullptr;ctx->GetDevice(&d);HoujiVkDevice*interop=nullptr;
    HRESULT hr=d->QueryInterface(houji_device_iid,reinterpret_cast<void**>(&interop));d->Release();
    fprintf(f,"DXVK device interop HRESULT=%08lx\n",(unsigned long)hr);
    if(interop){
        VkInstance instance{};VkPhysicalDevice physical{};VkDevice device{};VkQueue queue{};uint32_t family=0;
        interop->GetVulkanHandles(&instance,&physical,&device);interop->GetSubmissionQueue(&queue,&family);
        fprintf(f,"Device/physical/instance/queue present=%d/%d/%d/%d family=%u\n",!!device,!!physical,!!instance,!!queue,family);
        HMODULE loader=LoadLibraryW(L"vulkan-1.dll");
        auto gipa=loader?reinterpret_cast<PFN_vkGetInstanceProcAddr>(GetProcAddress(loader,"vkGetInstanceProcAddr")):nullptr;
        auto features=gipa?reinterpret_cast<PFN_vkGetPhysicalDeviceFeatures2>(gipa(instance,"vkGetPhysicalDeviceFeatures2")):nullptr;
        if(features){
            VkPhysicalDeviceVulkan12Features f12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES};VkPhysicalDeviceFeatures2 ft{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};ft.pNext=&f12;features(physical,&ft);
            fprintf(f,"supported float16=%u int8=%u storageImageWriteWithoutFormat=%u\n",f12.shaderFloat16,f12.shaderInt8,ft.features.shaderStorageImageWriteWithoutFormat);
        }
        for(unsigned i=0;i<4;++i){
            HoujiVkSurface*s=nullptr;hr=resources[i]->QueryInterface(houji_surface_iid,reinterpret_cast<void**>(&s));fprintf(f,"surface %u HRESULT=%08lx",i,(unsigned long)hr);
            if(s){VkImage image{};VkImageLayout layout{};VkImageCreateInfo info{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};hr=s->GetVulkanImageInfo(&image,&layout,&info);fprintf(f," image=%d result=%08lx dimensions=%ux%u format=%u layout=%u usage=%x",!!image,(unsigned long)hr,info.extent.width,info.extent.height,info.format,layout,info.usage);s->Release();}
            fputc('\n',f);
        }
        interop->Release();
    }
    fclose(f);
}
