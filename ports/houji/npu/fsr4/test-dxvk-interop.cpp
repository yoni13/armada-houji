// Minimal reproducer for the ARM64 Proton custom-COM interop failure. It uses
// a freshly created private texture; no game process/resource/hook is involved.
#include <windows.h>
#include <d3d11.h>
#include <cstdio>
#include <cstdlib>
#include "dxvk-interop-probe.h"

int main(int argc,char **argv){
    bool query_info=argc>1&&!strcmp(argv[1],"--image-info");
    FILE *f=fopen("Z:\\var\\home\\armada\\.local\\opt\\fsr4-htp\\results\\interop-standalone.log","w");
    if(!f)return 2;
    setvbuf(f,nullptr,_IONBF,0);
    ID3D11Device *device=nullptr;ID3D11DeviceContext *context=nullptr;
    D3D_FEATURE_LEVEL level{};
    HRESULT hr=D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_HARDWARE,nullptr,0,nullptr,0,D3D11_SDK_VERSION,&device,&level,&context);
    fprintf(f,"D3D11CreateDevice: %08lx level=%x\n",(unsigned long)hr,level);
    if(FAILED(hr)){fclose(f);return 1;}
    D3D11_TEXTURE2D_DESC desc{};desc.Width=32;desc.Height=32;desc.MipLevels=desc.ArraySize=1;desc.Format=DXGI_FORMAT_R16G16B16A16_FLOAT;desc.SampleDesc.Count=1;desc.Usage=D3D11_USAGE_DEFAULT;desc.BindFlags=D3D11_BIND_SHADER_RESOURCE|D3D11_BIND_UNORDERED_ACCESS;
    ID3D11Texture2D *texture=nullptr;hr=device->CreateTexture2D(&desc,nullptr,&texture);
    fprintf(f,"CreateTexture2D: %08lx\n",(unsigned long)hr);
    if(FAILED(hr)){context->Release();device->Release();fclose(f);return 1;}
    HoujiVkDevice *interop=nullptr;hr=device->QueryInterface(houji_device_iid,reinterpret_cast<void**>(&interop));
    fprintf(f,"Device interop: %08lx\n",(unsigned long)hr);
    if(interop){
        VkInstance instance{};VkPhysicalDevice physical{};VkDevice dev{};VkQueue queue{};uint32_t family=UINT32_MAX;
        interop->GetVulkanHandles(&instance,&physical,&dev);interop->GetSubmissionQueue(&queue,&family);
        fprintf(f,"Handles present %d/%d/%d/%d family=%u\n",!!instance,!!physical,!!dev,!!queue,family);
    }
    HoujiVkSurface *surface=nullptr;hr=texture->QueryInterface(houji_surface_iid,reinterpret_cast<void**>(&surface));
    fprintf(f,"Surface interop: %08lx\n",(unsigned long)hr);
    if(surface&&query_info){
        VkImage image{};VkImageLayout layout{};VkImageCreateInfo info{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
        fprintf(f,"Calling GetVulkanImageInfo on private texture\n");
        hr=surface->GetVulkanImageInfo(&image,&layout,&info);
        fprintf(f,"Returned: %08lx image=%d format=%u size=%ux%u layout=%u\n",(unsigned long)hr,!!image,info.format,info.extent.width,info.extent.height,layout);
    }
    if(surface)surface->Release();if(interop)interop->Release();texture->Release();context->Release();device->Release();
    fprintf(f,"Completed cleanly\n");fclose(f);return 0;
}
