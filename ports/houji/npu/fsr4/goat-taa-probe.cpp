// Game-specific ReShade hook. Capture-only by default; FSR4_NPU_DIRECT=1 may
// replace the exact verified TAA pass, falling back if inputs are unavailable.
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <unordered_map>
#include <unordered_set>
#include <atomic>
#include <cmath>
#include "reshade.hpp"
#include "experimental-gate.h"

extern "C" __declspec(dllexport) const char *NAME="Houji TAA capture";
extern "C" __declspec(dllexport) const char *DESCRIPTION="Read-only Goat 3 temporal-input diagnostic";
static std::mutex lock;
static std::unordered_map<uint64_t,uint32_t> shaders;
static std::unordered_map<reshade::api::command_list*,uint32_t> active;
static std::atomic<unsigned> captured{0};
static thread_local bool inside=false;
// UE 4.27 compiles one TAA permutation per screen-percentage range, so 75%
// (2EFB4885) and 50% differ. Other permutations are found by their bindings.
static std::unordered_map<uint64_t,uint32_t> compute_hashes;
static std::unordered_map<reshade::api::command_list*,uint64_t> bound_compute;
static std::unordered_set<uint32_t> direct_hashes{0x2efb4885,0xfde55dee};
static std::atomic<bool> direct_found{false};
static std::atomic<unsigned> discovery_checks{0};
static const bool direct_enabled=[](){char value[8]={};GetEnvironmentVariableA("FSR4_NPU_DIRECT",value,sizeof(value));return !strcmp(value,"1");}();
struct ViewBuffer {
    const void *mapped=nullptr;
    bool valid=false;
    float view[133*4]{};
};
static std::mutex view_lock;
static std::unordered_map<uint64_t,ViewBuffer> view_buffers;
static std::atomic<unsigned> cached_view_hits{0},cached_view_misses{0};
static double milliseconds(){LARGE_INTEGER t,f;QueryPerformanceCounter(&t);QueryPerformanceFrequency(&f);return 1000.*t.QuadPart/f.QuadPart;}
static bool valid_view(const float *v){
    float w=v[130*4],h=v[130*4+1];
    if(!std::isfinite(w)||!std::isfinite(h)||w<32||h<32||w>2048||h>2048)return false;
    if(std::fabs(w*v[130*4+2]-1.f)>1e-4f||std::fabs(h*v[130*4+3]-1.f)>1e-4f)return false;
    for(unsigned i=0;i<16;++i)if(!std::isfinite(v[122*4+i]))return false;
    return std::isfinite(v[28*4+8])&&std::isfinite(v[28*4+9])&&
        std::fabs(v[28*4+8])<.1f&&std::fabs(v[28*4+9])<.1f;
}
static void init_resource(reshade::api::device*d,const reshade::api::resource_desc&desc,const reshade::api::subresource_data*initial,reshade::api::resource_usage,reshade::api::resource r){
    if(d->get_api()!=reshade::api::device_api::d3d11||desc.type!=reshade::api::resource_type::buffer||desc.buffer.size!=8192)return;
    if((desc.usage&reshade::api::resource_usage::constant_buffer)==0)return;
    std::lock_guard<std::mutex>guard(view_lock);auto &v=view_buffers[r.handle];
    if(initial&&initial->data){memcpy(v.view,initial->data,sizeof(v.view));v.valid=valid_view(v.view);}
}
static void destroy_resource(reshade::api::device*,reshade::api::resource r){std::lock_guard<std::mutex>guard(view_lock);view_buffers.erase(r.handle);}
static void map_view(reshade::api::device*,reshade::api::resource r,uint64_t offset,uint64_t size,reshade::api::map_access access,void**data){
    if(inside||access==reshade::api::map_access::read_only)return;
    std::lock_guard<std::mutex>guard(view_lock);auto found=view_buffers.find(r.handle);if(found==view_buffers.end())return;
    auto &v=found->second;v.valid=false;v.mapped=nullptr;
    if(offset==0&&size>=sizeof(v.view)&&data)v.mapped=*data;
}
static void unmap_view(reshade::api::device*,reshade::api::resource r){
    if(inside)return;
    std::lock_guard<std::mutex>guard(view_lock);auto found=view_buffers.find(r.handle);if(found==view_buffers.end())return;
    auto &v=found->second;if(v.mapped){memcpy(v.view,v.mapped,sizeof(v.view));v.valid=valid_view(v.view);v.mapped=nullptr;}
}
static bool update_view(reshade::api::device*,const void*data,reshade::api::resource r,uint64_t offset,uint64_t size){
    if(inside)return false;
    std::lock_guard<std::mutex>guard(view_lock);auto found=view_buffers.find(r.handle);if(found==view_buffers.end())return false;
    auto &v=found->second;v.valid=false;
    if(offset==0&&size>=sizeof(v.view)&&data){memcpy(v.view,data,sizeof(v.view));v.valid=valid_view(v.view);}
    return false;
}
static bool update_view_command(reshade::api::command_list*c,const void*data,reshade::api::resource r,uint64_t offset,uint64_t size){return update_view(c->get_device(),data,r,offset,size);}
static uint32_t crc(const void *data,size_t size){uint32_t c=~0u;auto b=static_cast<const uint8_t*>(data);while(size--){c^=*b++;for(int i=0;i<8;++i)c=(c>>1)^(0xedb88320u&-(c&1));}return ~c;}
static bool contains(const void *data,size_t size,const char *text){size_t n=strlen(text);auto b=static_cast<const char*>(data);for(size_t i=0;i+n<=size;++i)if(!memcmp(b+i,text,n))return true;return false;}
static void init_pipeline(reshade::api::device*d,reshade::api::pipeline_layout,uint32_t count,const reshade::api::pipeline_subobject *objects,reshade::api::pipeline p){
    if(d->get_api()!=reshade::api::device_api::d3d11)return;
    for(uint32_t i=0;i<count;++i){
        if(objects[i].type!=reshade::api::pipeline_subobject_type::pixel_shader&&objects[i].type!=reshade::api::pipeline_subobject_type::compute_shader)continue;
        auto s=static_cast<const reshade::api::shader_desc*>(objects[i].data);if(!s||!s->code)continue;
        uint32_t hash=crc(s->code,s->code_size);
        if(objects[i].type==reshade::api::pipeline_subobject_type::compute_shader){std::lock_guard<std::mutex>guard(lock);compute_hashes[p.handle]=hash;}
        if(hash!=0x37a2d4ca&&hash!=0x8e2dc705&&hash!=0x2efb4885&&hash!=0xfde55dee&&!contains(s->code,s->code_size,"TemporalAA"))continue;
        std::lock_guard<std::mutex>guard(lock);shaders[p.handle]=hash;
        CreateDirectoryA("C:\\fsr4-goat-taa",nullptr);FILE*f=fopen("C:\\fsr4-goat-taa\\probe.log","a");
        if(f){fprintf(f,"candidate CRC %08x bytes=%zu type=%u\n",hash,s->code_size,(unsigned)objects[i].type);fclose(f);}
        char path[200];snprintf(path,sizeof(path),"C:\\fsr4-goat-taa\\shader-%08x.dxbc",hash);f=fopen(path,"wb");if(f){fwrite(s->code,1,s->code_size,f);fclose(f);}
    }
}
static void bind(reshade::api::command_list*c,reshade::api::pipeline_stage stage,reshade::api::pipeline p){
    if((stage&reshade::api::pipeline_stage::pixel_shader)==0&&(stage&reshade::api::pipeline_stage::compute_shader)==0)return;
    std::lock_guard<std::mutex>guard(lock);auto found=shaders.find(p.handle);active[c]=found==shaders.end()?0:found->second;
    if((stage&reshade::api::pipeline_stage::compute_shader)!=0)bound_compute[c]=p.handle;
}
static bool texture_desc(ID3D11View*view,D3D11_TEXTURE2D_DESC*desc){
    if(!view)return false;ID3D11Resource*r=nullptr;view->GetResource(&r);if(!r)return false;
    ID3D11Texture2D*t=nullptr;bool ok=SUCCEEDED(r->QueryInterface(__uuidof(ID3D11Texture2D),(void**)&t));
    if(ok){t->GetDesc(desc);t->Release();}r->Release();return ok;
}
// Signature of Goat's TAA pass at any screen percentage: an RGBA16F output UAV
// u0 (720p or 1080p tested); R11G11B10 color, R32G8X24 depth and RGBA16-UNORM/
// RG32F motion at t1-t3, all one smaller render size; 8 KiB view constants at b1.
static bool supported_output(const D3D11_TEXTURE2D_DESC&d){
    return d.Format==DXGI_FORMAT_R16G16B16A16_FLOAT&&d.Width>=640&&d.Height>=360&&d.Width<=2048&&d.Height<=2048&&d.Width%8==0&&d.Height%8==0;
}
static uint32_t discover(reshade::api::command_list*c){
    if(direct_found||discovery_checks.fetch_add(1)>200000)return 0;
    auto ctx=reinterpret_cast<ID3D11DeviceContext*>(c->get_native());
    ID3D11UnorderedAccessView*uav=nullptr;ctx->CSGetUnorderedAccessViews(0,1,&uav);
    D3D11_TEXTURE2D_DESC out{},in[3]{};bool ok=texture_desc(uav,&out);if(uav)uav->Release();
    if(!ok||!supported_output(out))return 0;
    ID3D11ShaderResourceView*views[3]={};ctx->CSGetShaderResources(1,3,views);
    for(unsigned i=0;i<3;++i){if(!texture_desc(views[i],&in[i]))ok=false;if(views[i])views[i]->Release();}
    if(!ok||in[0].Format!=DXGI_FORMAT_R11G11B10_FLOAT||in[1].Format!=DXGI_FORMAT_R32G8X24_TYPELESS||
       (in[2].Format!=DXGI_FORMAT_R16G16B16A16_UNORM&&in[2].Format!=DXGI_FORMAT_R32G32_FLOAT))return 0;
    for(unsigned i=1;i<3;++i)if(in[i].Width!=in[0].Width||in[i].Height!=in[0].Height)return 0;
    if(in[0].Width>=out.Width||in[0].Height>=out.Height)return 0;
    ID3D11Buffer*cb=nullptr;ctx->CSGetConstantBuffers(1,1,&cb);if(!cb)return 0;
    D3D11_BUFFER_DESC bd;cb->GetDesc(&bd);cb->Release();if(bd.ByteWidth!=8192)return 0;
    uint32_t hash=0;
    {
        std::lock_guard<std::mutex>guard(lock);auto bound=bound_compute.find(c);if(bound==bound_compute.end())return 0;
        auto known=compute_hashes.find(bound->second);if(known==compute_hashes.end())return 0;
        hash=known->second;direct_hashes.insert(hash);shaders[bound->second]=hash;active[c]=hash;
    }
    direct_found=true;
    FILE*f=fopen("C:\\fsr4-goat-taa\\probe.log","a");
    if(f){fprintf(f,"discovered TAA CRC %08x by bindings: render %ux%u -> %ux%u motion format %u\n",hash,in[0].Width,in[0].Height,out.Width,out.Height,in[2].Format);fclose(f);}
    return hash;
}
static bool live_dispatch(reshade::api::command_list*c){
    typedef unsigned (*Evaluate)(ID3D11DeviceContext*,ID3D11Resource*,ID3D11Resource*,ID3D11Resource*,ID3D11Resource*,const float*,unsigned);
    static Evaluate evaluate=nullptr;
    static bool enabled=[](){char value[8]={};GetEnvironmentVariableA("FSR4_NPU_DIRECT",value,sizeof(value));return !strcmp(value,"1");}();
    static bool previous_ok=false;
    if(!enabled)return false;
    double started=milliseconds();
    if(!evaluate){HMODULE ngx=GetModuleHandleW(L"_nvngx.dll");if(!ngx)ngx=LoadLibraryW(L"_nvngx.dll");if(ngx)evaluate=reinterpret_cast<Evaluate>(GetProcAddress(ngx,"Houji_EvaluateTAA"));if(!evaluate)return false;}
    auto ctx=reinterpret_cast<ID3D11DeviceContext*>(c->get_native());
    ID3D11Buffer*cb=nullptr;ctx->CSGetConstantBuffers(1,1,&cb);if(!cb)return false;
    float view[133*4];bool have_view=false;
    {
        std::lock_guard<std::mutex>guard(view_lock);auto found=view_buffers.find(reinterpret_cast<uint64_t>(cb));
        if(found!=view_buffers.end()&&found->second.valid){memcpy(view,found->second.view,sizeof(view));have_view=true;}
    }
    cb->Release();
    if(!have_view){++cached_view_misses;previous_ok=false;return false;}
    unsigned hit=++cached_view_hits;double got_view=milliseconds();
    unsigned rw=(unsigned)view[130*4],rh=(unsigned)view[130*4+1];
    ID3D11ShaderResourceView*all_views[8]={};ctx->CSGetShaderResources(0,8,all_views);
    ID3D11Resource*resources[4]={};
    for(unsigned i=0;i<8;++i){
        if(!all_views[i])continue;
        ID3D11Resource*r=nullptr;all_views[i]->GetResource(&r);all_views[i]->Release();
        if(!r)continue;
        ID3D11Texture2D*t=nullptr;
        if(SUCCEEDED(r->QueryInterface(__uuidof(ID3D11Texture2D),(void**)&t))){
            D3D11_TEXTURE2D_DESC td;t->GetDesc(&td);t->Release();
            if(td.Width==rw&&td.Height==rh){
                if(!resources[0]&&(td.Format==DXGI_FORMAT_R11G11B10_FLOAT||td.Format==DXGI_FORMAT_R16G16B16A16_FLOAT))
                    resources[0]=r;
                else if(!resources[1]&&td.Format==DXGI_FORMAT_R32G8X24_TYPELESS)
                    resources[1]=r;
                else if(!resources[2]&&(td.Format==DXGI_FORMAT_R16G16B16A16_UNORM||td.Format==DXGI_FORMAT_R32G32_FLOAT))
                    resources[2]=r;
                else r->Release();
            }else r->Release();
        }else r->Release();
    }
    ID3D11UnorderedAccessView*uav=nullptr;ctx->CSGetUnorderedAccessViews(0,1,&uav);
    if(uav){uav->GetResource(&resources[3]);uav->Release();}
    bool ok=false,continuous=false;
    if(resources[0]&&resources[1]&&resources[2]&&resources[3]){
        // DXVK's GetVulkanImageInfo is not called here: ReShade's hooked
        // ID3D11Resource::GetDevice returns its proxy device, which DXVK then
        // casts to its own device class and crashes.
        unsigned result=evaluate(ctx,resources[0],resources[1],resources[2],resources[3],view,!previous_ok);
        ok=result==1;
        // 0xbad0000a: pipelined input queued; TAA draws this frame, history continues.
        continuous=ok||result==0xbad0000au;
    }
    for(auto r:resources)if(r)r->Release();previous_ok=continuous;
    if(hit<=3||hit%120==0){FILE*f=fopen("C:\\fsr4-goat-taa\\probe.log","a");if(f){fprintf(f,"hook frame=%u camera_ms=%.3f total_ms=%.3f cached_hits=%u misses=%u npu=%d\n",hit,got_view-started,milliseconds()-started,hit,cached_view_misses.load(),ok);fclose(f);}}
    return ok;
}
static void buffer_dump(ID3D11DeviceContext*ctx,ID3D11Buffer*b,const char*path){
    D3D11_BUFFER_DESC d;b->GetDesc(&d);d.BindFlags=0;d.MiscFlags=0;d.Usage=D3D11_USAGE_STAGING;d.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
    ID3D11Device*dev=nullptr;ctx->GetDevice(&dev);ID3D11Buffer*stage=nullptr;
    if(SUCCEEDED(dev->CreateBuffer(&d,nullptr,&stage))){ctx->CopyResource(stage,b);D3D11_MAPPED_SUBRESOURCE m;if(SUCCEEDED(ctx->Map(stage,0,D3D11_MAP_READ,0,&m))){FILE*f=fopen(path,"wb");if(f){fwrite(m.pData,1,d.ByteWidth,f);fclose(f);}ctx->Unmap(stage,0);}stage->Release();}dev->Release();
}
static void texture_dump(ID3D11DeviceContext*ctx,ID3D11Texture2D*t,const char*path){
    D3D11_TEXTURE2D_DESC d;t->GetDesc(&d);unsigned bytes=0;
    switch(d.Format){case DXGI_FORMAT_R16G16B16A16_FLOAT:case DXGI_FORMAT_R32G8X24_TYPELESS:case DXGI_FORMAT_R32G32_FLOAT:bytes=8;break;
        case DXGI_FORMAT_R16G16_FLOAT:case DXGI_FORMAT_R16G16_UNORM:case DXGI_FORMAT_R32_TYPELESS:case DXGI_FORMAT_R32_FLOAT:case DXGI_FORMAT_R11G11B10_FLOAT:case DXGI_FORMAT_R8G8B8A8_UNORM:case DXGI_FORMAT_R24G8_TYPELESS:bytes=4;break;default:return;}
    if(d.SampleDesc.Count!=1||d.Width>2048||d.Height>2048)return;
    d.BindFlags=0;d.MiscFlags=0;d.Usage=D3D11_USAGE_STAGING;d.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
    ID3D11Device*dev=nullptr;ctx->GetDevice(&dev);ID3D11Texture2D*stage=nullptr;
    if(SUCCEEDED(dev->CreateTexture2D(&d,nullptr,&stage))){ctx->CopyResource(stage,t);D3D11_MAPPED_SUBRESOURCE m;if(SUCCEEDED(ctx->Map(stage,0,D3D11_MAP_READ,0,&m))){FILE*f=fopen(path,"wb");if(f){for(unsigned y=0;y<d.Height;++y)fwrite((char*)m.pData+y*m.RowPitch,bytes,d.Width,f);fclose(f);}ctx->Unmap(stage,0);}stage->Release();}dev->Release();
}
static bool capture(reshade::api::command_list*c,bool compute){
    if(inside)return false;
    uint32_t hash=0;bool direct=false;
    {std::lock_guard<std::mutex>guard(lock);hash=active[c];direct=hash&&direct_hashes.count(hash);}
    if(!direct&&compute&&direct_enabled){inside=true;uint32_t d=discover(c);inside=false;if(d){hash=d;direct=true;}}
    if(!hash)return false;
    if(compute&&direct){
        direct_found=true;
        inside=true;bool ok=live_dispatch(c);inside=false;if(ok)return true;
        if(direct_enabled)return false; // keep earlier capture dumps intact
    }
    if(captured.load()>=3)return false;
    unsigned n=captured.fetch_add(1);if(n>=3)return false;inside=true;
    char dir[150],path[200];snprintf(dir,sizeof(dir),"C:\\fsr4-goat-taa\\f%02u",n);CreateDirectoryA(dir,nullptr);snprintf(path,sizeof(path),"%s\\bindings.txt",dir);FILE*f=fopen(path,"w");
    auto ctx=reinterpret_cast<ID3D11DeviceContext*>(c->get_native());ID3D11ShaderResourceView*views[16]={};ID3D11Buffer*cb[14]={};
    if(compute){ctx->CSGetShaderResources(0,16,views);ctx->CSGetConstantBuffers(0,14,cb);}else{ctx->PSGetShaderResources(0,16,views);ctx->PSGetConstantBuffers(0,14,cb);}
    for(unsigned i=0;i<16;++i)if(views[i]){ID3D11Resource*r=nullptr;views[i]->GetResource(&r);ID3D11Texture2D*t=nullptr;if(SUCCEEDED(r->QueryInterface(__uuidof(ID3D11Texture2D),(void**)&t))){D3D11_TEXTURE2D_DESC d;t->GetDesc(&d);D3D11_SHADER_RESOURCE_VIEW_DESC v;views[i]->GetDesc(&v);if(f)fprintf(f,"t%u %ux%u resource_format=%u view_format=%u\n",i,d.Width,d.Height,d.Format,v.Format);snprintf(path,sizeof(path),"%s\\t%u.raw",dir,i);texture_dump(ctx,t,path);t->Release();}r->Release();views[i]->Release();}
    for(unsigned i=0;i<14;++i)if(cb[i]){D3D11_BUFFER_DESC d;cb[i]->GetDesc(&d);if(f)fprintf(f,"b%u bytes=%u\n",i,d.ByteWidth);snprintf(path,sizeof(path),"%s\\b%u.raw",dir,i);buffer_dump(ctx,cb[i],path);cb[i]->Release();}
    if(f)fclose(f);inside=false;return false;
}
static bool draw(reshade::api::command_list*c,uint32_t,uint32_t,uint32_t,uint32_t){return capture(c,false);}
static bool dispatch(reshade::api::command_list*c,uint32_t,uint32_t,uint32_t){return capture(c,true);}
BOOL WINAPI DllMain(HMODULE module,DWORD reason,LPVOID){
    if(!houji_fsr4_experimental_enabled())return TRUE;
    if(reason==DLL_PROCESS_ATTACH){if(!reshade::register_addon(module))return FALSE;reshade::register_event<reshade::addon_event::init_pipeline>(init_pipeline);reshade::register_event<reshade::addon_event::bind_pipeline>(bind);reshade::register_event<reshade::addon_event::draw>(draw);reshade::register_event<reshade::addon_event::dispatch>(dispatch);
        reshade::register_event<reshade::addon_event::init_resource>(init_resource);reshade::register_event<reshade::addon_event::destroy_resource>(destroy_resource);
        reshade::register_event<reshade::addon_event::map_buffer_region>(map_view);reshade::register_event<reshade::addon_event::unmap_buffer_region>(unmap_view);
        reshade::register_event<reshade::addon_event::update_buffer_region>(update_view);reshade::register_event<reshade::addon_event::update_buffer_region_command>(update_view_command);}
    if(reason==DLL_PROCESS_DETACH)reshade::unregister_addon(module);
    return TRUE;
}
