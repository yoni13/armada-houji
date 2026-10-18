/* Game-local D3D11 NGX interoperability probe. Without FSR4_NPU_LIVE it captures
 * inputs then returns failure so the original TAA draw runs. FSR4_NPU_LIVE uses
 * the native frame server; FSR4_NPU_PIPELINE additionally overlaps readback, NPU
 * work and display at the cost of two frames of scene latency.
 * MSVC x64 overload slot order is used explicitly rather than a MinGW C++ ABI. */
#define COBJMACROS
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <xmmintrin.h>
#include "frame-wire.h"
#include "experimental-gate.h"

#define OK 1u
#define MISSING 0xbad00005u
#define UNSUPPORTED 0xbad00001u
/* Pipelined input accepted, but no completed output is available yet. */
#define QUEUED 0xbad0000au
#define EXP __declspec(dllexport) unsigned
typedef struct { char key[128];double number;uint64_t integer;void *pointer;int kind; } Entry;
typedef struct { void **vt;unsigned count;Entry entries[128]; } Params;
typedef struct { unsigned id;unsigned width,height,out_width,out_height,flags; } Feature;
static char folder[MAX_PATH];static FILE *logfile;static LONG sequence;
static SOCKET connection=INVALID_SOCKET;
static int live_enabled,pipeline_enabled;
static int live_fails;
static HANDLE shared_file=INVALID_HANDLE_VALUE,shared_mapping;
static unsigned char *shared_frames;
static double milliseconds(void){LARGE_INTEGER t,f;QueryPerformanceCounter(&t);QueryPerformanceFrequency(&f);return 1000.*(double)t.QuadPart/(double)f.QuadPart;}

static void init(void){
    if(!houji_fsr4_experimental_enabled())return;
    if(logfile)return;
    GetEnvironmentVariableA("FSR4_CAPTURE_DIR",folder,sizeof(folder));
    if(!folder[0])strcpy(folder,"C:\\fsr4-goat-capture");
    CreateDirectoryA(folder,NULL);char path[MAX_PATH];snprintf(path,sizeof(path),"%s\\capture.log",folder);logfile=fopen(path,"a");
    char mode[8]={0};GetEnvironmentVariableA("FSR4_NPU_LIVE",mode,sizeof(mode));live_enabled=!strcmp(mode,"1");
    memset(mode,0,sizeof(mode));GetEnvironmentVariableA("FSR4_NPU_PIPELINE",mode,sizeof(mode));pipeline_enabled=live_enabled&&!strcmp(mode,"1");
}
static void note(const char *text){init();if(logfile){fprintf(logfile,"%s\n",text);fflush(logfile);}}
static const char *key(const char *s){
    if(s&&s[0]=='#')switch((unsigned char)s[1]){
        case 1:return "SuperSampling.Available";
        case 0x10:return "Width";case 0x11:return "Height";case 0x12:return "OutWidth";case 0x13:return "OutHeight";
        case 0x1e:return "Color";case 0x25:return "Reset";case 0x27:return "MotionVectors";case 0x3d:return "Depth";
    }
    return s?s:"";
}
static Entry *entry(Params *p,const char *name,int create){
    name=key(name);for(unsigned i=0;i<p->count;++i)if(!strcmp(p->entries[i].key,name))return &p->entries[i];
    if(!create||p->count==128)return NULL;Entry *e=&p->entries[p->count++];memset(e,0,sizeof(*e));snprintf(e->key,sizeof(e->key),"%s",name);return e;
}
static void set_ull(Params*p,const char*n,uint64_t v){Entry*e=entry(p,n,1);if(e){e->integer=v;e->number=(double)v;e->kind=0;}}
static void set_i(Params*p,const char*n,int v){Entry*e=entry(p,n,1);if(e){e->integer=(uint64_t)(int64_t)v;e->number=v;e->kind=0;}}
static void set_ui(Params*p,const char*n,unsigned v){set_ull(p,n,v);}
static void set_ptr(Params*p,const char*n,void*v){Entry*e=entry(p,n,1);if(e){e->pointer=v;e->kind=1;}}
static void set_f(Params*p,const char*n,float v){Entry*e=entry(p,n,1);if(e){e->number=v;e->kind=2;}}
static void set_d(Params*p,const char*n,double v){Entry*e=entry(p,n,1);if(e){e->number=v;e->kind=2;}}
static unsigned get_ull(Params*p,const char*n,uint64_t*v){Entry*e=entry(p,n,0);if(!e)return MISSING;*v=e->kind==1?(uintptr_t)e->pointer:e->kind==2?(uint64_t)e->number:e->integer;return OK;}
static unsigned get_ui(Params*p,const char*n,unsigned*v){uint64_t t=0;unsigned r=get_ull(p,n,&t);*v=(unsigned)t;return r;}
static unsigned get_i(Params*p,const char*n,int*v){unsigned t=0;unsigned r=get_ui(p,n,&t);*v=(int)t;if(!strcmp(key(n),"SuperSampling.Available")){char line[128];snprintf(line,sizeof(line),"availability read: %d result=%x",*v,r);note(line);}return r;}
static unsigned get_ptr(Params*p,const char*n,void**v){Entry*e=entry(p,n,0);if(!e)return MISSING;*v=e->pointer;return OK;}
static unsigned get_f(Params*p,const char*n,float*v){Entry*e=entry(p,n,0);if(!e)return MISSING;*v=(float)e->number;return OK;}
static unsigned get_d(Params*p,const char*n,double*v){Entry*e=entry(p,n,0);if(!e)return MISSING;*v=e->number;return OK;}
static void reset(Params*p){p->count=0;}
static void *vtable[]={set_ull,set_ptr,set_ptr,set_i,set_ui,set_d,set_f,set_ull,
                       get_ull,get_ptr,get_ptr,get_i,get_ui,get_d,get_f,get_ull,reset};
/* MSVC emits overloads in reverse declaration order within each method name. */
static void correct_vtable(void){
    void *v[]={set_ptr,set_ptr,set_ptr,set_i,set_ui,set_d,set_f,set_ull,
               get_ptr,get_ptr,get_ptr,get_i,get_ui,get_d,get_f,get_ull,reset};
    memcpy(vtable,v,sizeof(v));
}
static Params *make_params(void){Params*p=calloc(1,sizeof(*p));if(p){correct_vtable();p->vt=vtable;}return p;}
static unsigned optimal(Params*p){unsigned w=0,h=0;get_ui(p,"Width",&w);get_ui(p,"Height",&h);set_ui(p,"OutWidth",w);set_ui(p,"OutHeight",h);set_f(p,"Sharpness",0);set_ui(p,"DLSS.Get.Dynamic.Min.Render.Width",32);set_ui(p,"DLSS.Get.Dynamic.Min.Render.Height",32);set_ui(p,"DLSS.Get.Dynamic.Max.Render.Width",w);set_ui(p,"DLSS.Get.Dynamic.Max.Render.Height",h);return OK;}
static unsigned stats(Params*p){set_ull(p,"SizeInBytes",0);return OK;}
static void capabilities(Params*p){set_i(p,"SuperSampling.Available",houji_fsr4_experimental_enabled());set_i(p,"SuperSampling.NeedsUpdatedDriver",0);set_i(p,"SuperSampling.FeatureInitResult",houji_fsr4_experimental_enabled()?OK:UNSUPPORTED);set_ptr(p,"DLSSOptimalSettingsCallback",(void*)optimal);set_ptr(p,"DLSSGetStatsCallback",(void*)stats);}

typedef struct{ID3D11Texture2D*stage;D3D11_TEXTURE2D_DESC desc;unsigned bytes;unsigned char*data;} Transfer;
static Transfer transfers[3];
static double readback_wait_ms,readback_copy_ms;
static void unmap_frames(void){
    if(shared_frames){UnmapViewOfFile(shared_frames);shared_frames=NULL;}
    if(shared_mapping){CloseHandle(shared_mapping);shared_mapping=NULL;}
    if(shared_file!=INVALID_HANDLE_VALUE){CloseHandle(shared_file);shared_file=INVALID_HANDLE_VALUE;}
}
static int map_frames(void){
    if(shared_frames)return 1;
    char path[MAX_PATH]={0};GetEnvironmentVariableA("FSR4_FRAME_FILE",path,sizeof(path));
    if(!path[0])snprintf(path,sizeof(path),"%s\\frame-share.bin",folder);
    shared_file=CreateFileA(path,GENERIC_READ|GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,NULL,OPEN_EXISTING,0,NULL);
    if(shared_file==INVALID_HANDLE_VALUE)return 0;
    LARGE_INTEGER length;if(!GetFileSizeEx(shared_file,&length)||length.QuadPart<FSR4_SHARED_SIZE){CloseHandle(shared_file);shared_file=INVALID_HANDLE_VALUE;return 0;}
    shared_mapping=CreateFileMappingA(shared_file,NULL,PAGE_READWRITE,0,0,NULL);
    if(shared_mapping)shared_frames=MapViewOfFile(shared_mapping,FILE_MAP_ALL_ACCESS,0,0,FSR4_SHARED_SIZE);
    if(!shared_frames){if(shared_mapping)CloseHandle(shared_mapping);CloseHandle(shared_file);shared_mapping=NULL;shared_file=INVALID_HANDLE_VALUE;return 0;}
    note("Using shared memory frame transport (socket carries control only)");return 1;
}
static int texture_bytes(DXGI_FORMAT f){switch(f){case DXGI_FORMAT_R16G16B16A16_FLOAT:case DXGI_FORMAT_R16G16B16A16_UNORM:case DXGI_FORMAT_R32G8X24_TYPELESS:case DXGI_FORMAT_R32G32_FLOAT:return 8;case DXGI_FORMAT_R11G11B10_FLOAT:case DXGI_FORMAT_R32_FLOAT:case DXGI_FORMAT_R32_TYPELESS:case DXGI_FORMAT_R16G16_FLOAT:return 4;default:return 0;}}
static int prepare_texture(ID3D11DeviceContext*ctx,ID3D11Resource*r,Transfer*t){
    if(!r)return 0;ID3D11Texture2D*texture=NULL;ID3D11Device*device=NULL;
    if(FAILED(IUnknown_QueryInterface((IUnknown*)r,&IID_ID3D11Texture2D,(void**)&texture)))return 0;
    D3D11_TEXTURE2D_DESC d;ID3D11Texture2D_GetDesc(texture,&d);unsigned pixel=texture_bytes(d.Format);
    if(!pixel||d.Width>2048||d.Height>2048||d.SampleDesc.Count!=1){ID3D11Texture2D_Release(texture);return 0;}
    if(!t->stage||t->desc.Width!=d.Width||t->desc.Height!=d.Height||t->desc.Format!=d.Format){
        if(t->stage)ID3D11Texture2D_Release(t->stage);t->stage=NULL;free(t->data);t->data=NULL;
        t->desc=d;t->bytes=d.Width*d.Height*pixel;t->data=malloc(t->bytes);if(!t->data){ID3D11Texture2D_Release(texture);return 0;}
        D3D11_TEXTURE2D_DESC sd=d;sd.BindFlags=sd.MiscFlags=0;sd.Usage=D3D11_USAGE_STAGING;sd.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
        ID3D11DeviceContext_GetDevice(ctx,&device);HRESULT hr=ID3D11Device_CreateTexture2D(device,&sd,NULL,&t->stage);ID3D11Device_Release(device);if(FAILED(hr)){ID3D11Texture2D_Release(texture);return 0;}
    }
    ID3D11DeviceContext_CopyResource(ctx,(ID3D11Resource*)t->stage,r);ID3D11Texture2D_Release(texture);
    return 1;
}
static int acquire_texture(ID3D11DeviceContext*ctx,Transfer*t,unsigned char *destination){
    D3D11_TEXTURE2D_DESC d=t->desc;unsigned pixel=texture_bytes(d.Format);
    double start=milliseconds();
    D3D11_MAPPED_SUBRESOURCE mapped;HRESULT hr=ID3D11DeviceContext_Map(ctx,(ID3D11Resource*)t->stage,0,D3D11_MAP_READ,0,&mapped);if(FAILED(hr))return 0;
    double ready=milliseconds();
    if(mapped.RowPitch==d.Width*pixel)memcpy(destination,mapped.pData,t->bytes);
    else for(unsigned y=0;y<d.Height;++y)memcpy(destination+y*d.Width*pixel,(char*)mapped.pData+y*mapped.RowPitch,d.Width*pixel);
    readback_wait_ms+=ready-start;readback_copy_ms+=milliseconds()-ready;
    ID3D11DeviceContext_Unmap(ctx,(ID3D11Resource*)t->stage,0);return 1;
}
static int wire(void*data,unsigned bytes,int sending){char*p=data;while(bytes){int n=sending?send(connection,p,(int)bytes,0):recv(connection,p,(int)bytes,0);if(n<=0)return 0;p+=n;bytes-=n;}return 1;}
static int connect_server(void){
    static int started;
    if(connection!=INVALID_SOCKET)return 1;
    WSADATA w;if(!started){if(WSAStartup(MAKEWORD(2,2),&w))return 0;started=1;}
    connection=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);if(connection==INVALID_SOCKET)return 0;
    DWORD timeout=2500;setsockopt(connection,SOL_SOCKET,SO_RCVTIMEO,(char*)&timeout,sizeof(timeout));setsockopt(connection,SOL_SOCKET,SO_SNDTIMEO,(char*)&timeout,sizeof(timeout));
    int one=1;setsockopt(connection,IPPROTO_TCP,TCP_NODELAY,(char*)&one,sizeof(one));
    struct sockaddr_in address={0};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);address.sin_port=htons(FSR4_WIRE_PORT);
    if(connect(connection,(struct sockaddr*)&address,sizeof(address))){closesocket(connection);connection=INVALID_SOCKET;return 0;}
    unmap_frames();
    if(!map_frames()){closesocket(connection);connection=INVALID_SOCKET;return 0;}
    note("Connected to native HTP frame server");return 1;
}
static int build_header(Params*p,const Transfer*t,ID3D11Resource*output,unsigned seq,Fsr4FrameHeader*h){
    ID3D11Texture2D*out=NULL;if(FAILED(IUnknown_QueryInterface((IUnknown*)output,&IID_ID3D11Texture2D,(void**)&out)))return 0;
    D3D11_TEXTURE2D_DESC od;ID3D11Texture2D_GetDesc(out,&od);ID3D11Texture2D_Release(out);
    if(od.Format!=DXGI_FORMAT_R16G16B16A16_FLOAT||od.Width>FSR4_MAX_OUTPUT||od.Height>FSR4_MAX_OUTPUT||
       od.Width%FSR4_OUTPUT_ALIGN||od.Height%FSR4_OUTPUT_ALIGN||
       (uint64_t)od.Width*od.Height*8>FSR4_SHARED_SIZE-FSR4_SHARED_OUTPUT_OFFSET)return 0;
    memset(h,0,sizeof(*h));h->magic=FSR4_WIRE_MAGIC;h->version=4;h->sequence=seq;h->width=t[0].desc.Width;h->height=t[0].desc.Height;h->out_width=od.Width;h->out_height=od.Height;
    h->color_format=t[0].desc.Format;h->depth_format=t[1].desc.Format;h->mv_format=t[2].desc.Format;h->color_bytes=t[0].bytes;h->depth_bytes=t[1].bytes;h->mv_bytes=t[2].bytes;
    get_ui(p,"Reset",&h->reset);get_f(p,"Jitter.Offset.X",&h->jitter_x);get_f(p,"Jitter.Offset.Y",&h->jitter_y);h->mv_scale_x=h->mv_scale_y=h->pre_exposure=1;
    get_f(p,"MV.Scale.X",&h->mv_scale_x);get_f(p,"MV.Scale.Y",&h->mv_scale_y);get_f(p,"DLSS.Pre.Exposure",&h->pre_exposure);
    h->render_width=h->width;h->render_height=h->height;get_ui(p,"DLSS.Render.Subrect.Dimensions.Width",&h->render_width);get_ui(p,"DLSS.Render.Subrect.Dimensions.Height",&h->render_height);
    void *matrix=NULL;if(get_ptr(p,"Houji.ClipToPrevious",&matrix)==OK&&matrix)memcpy(h->clip_to_previous,matrix,sizeof(h->clip_to_previous));
    return h->width<=od.Width&&h->height<=od.Height&&t[1].desc.Width==h->width&&t[2].desc.Width==h->width&&t[1].desc.Height==h->height&&t[2].desc.Height==h->height;
}
static unsigned live_frame(ID3D11DeviceContext*ctx,Feature*f,Params*p,unsigned seq){
    if(live_fails>=2)return UNSUPPORTED;
    double start=milliseconds();
    readback_wait_ms=readback_copy_ms=0;
    ID3D11Resource*resources[4]={0};const char*names[]={"Color","Depth","MotionVectors","Output"};
    for(unsigned i=0;i<4;++i)if(get_ptr(p,names[i],(void**)&resources[i])!=OK||!resources[i])return UNSUPPORTED;
    for(unsigned i=0;i<3;++i)if(!prepare_texture(ctx,resources[i],&transfers[i]))return UNSUPPORTED;
    int shared=map_frames();unsigned offset=0;
    if((uint64_t)transfers[0].bytes+transfers[1].bytes+transfers[2].bytes>FSR4_SHARED_OUTPUT_OFFSET)return UNSUPPORTED;
    for(unsigned i=0;i<3;++i){if(!acquire_texture(ctx,&transfers[i],shared?shared_frames+offset:transfers[i].data))return UNSUPPORTED;offset+=transfers[i].bytes;}
    double staged=milliseconds();
    Fsr4FrameHeader h;if(!build_header(p,transfers,resources[3],seq,&h))return UNSUPPORTED;
    D3D11_TEXTURE2D_DESC od={0};od.Width=h.out_width;od.Height=h.out_height;
    if(!shared)return UNSUPPORTED;
    if(!connect_server())goto failed;
    MemoryBarrier();if(!wire(&h,sizeof(h),1))goto failed;
    if(!shared)for(unsigned i=0;i<3;++i)if(!wire(transfers[i].data,transfers[i].bytes,1))goto failed;
    double sent=milliseconds();
    Fsr4Reply reply;if(!wire(&reply,sizeof(reply),0)||reply.magic!=FSR4_WIRE_MAGIC||reply.sequence!=seq||reply.status||reply.bytes!=od.Width*od.Height*8)goto failed;
    static unsigned char*result=NULL;static unsigned result_size;
    if(!shared){if(result_size!=reply.bytes){free(result);result=malloc(reply.bytes);result_size=reply.bytes;}
    if(!result||!wire(result,reply.bytes,0))goto failed;}
    MemoryBarrier();
    double received=milliseconds();
    ID3D11DeviceContext_UpdateSubresource(ctx,resources[3],0,NULL,shared?shared_frames+FSR4_SHARED_OUTPUT_OFFSET:result,od.Width*8,0);
    if(seq<=3||seq%120==0){char line[160];snprintf(line,sizeof(line),"NPU frame %u completed %ux%u; GPU-NPU shared buffers, game-side staged transport",seq,od.Width,od.Height);note(line);}
    if(seq<=3||seq%120==0){char line[320];snprintf(line,sizeof(line),"timing frame=%u stage_ms=%.3f map_wait_ms=%.3f copy_ms=%.3f send_ms=%.3f wait_receive_ms=%.3f upload_ms=%.3f total_ms=%.3f",seq,staged-start,readback_wait_ms,readback_copy_ms,sent-staged,received-sent,milliseconds()-received,milliseconds()-start);note(line);}
    (void)f;return OK;
failed:
    if(connection!=INVALID_SOCKET)closesocket(connection);connection=INVALID_SOCKET;++live_fails;note("Native frame server failed; using normal TAA fallback");return UNSUPPORTED;
}

/* Frame N: present the result for N-2, copy N to staging, map N-1 and hand it
 * to a worker thread. The worker copies the mapped inputs to shared memory,
 * waits for the server and writes the reply into a mapped STAGING texture, so
 * the render thread only issues Map/Unmap/CopyResource. The displayed scene is
 * two frames older than the game's later passes and HUD. */
typedef struct{Transfer t[3];Fsr4FrameHeader header;} Slot;
typedef struct{
    const unsigned char *src[3];unsigned src_pitch[3],row_bytes[3],rows[3];
    Fsr4FrameHeader header;unsigned char *dst;unsigned dst_pitch;
    int ok,pack_depth;double copy_in_ms,server_ms,copy_out_ms;
} Job;
static Slot slots[2];
static ID3D11Texture2D *out_stage[2];
static Job job;
static HANDLE worker_thread,job_ready,job_done;
static int queued_slot=-1,mapped_slot=-1,mapped_out=-1,job_pending,next_out;
static double last_call,next_connect;
static void copy_rows(unsigned char *dst,unsigned dst_pitch,const unsigned char *src,unsigned src_pitch,unsigned row_bytes,unsigned rows){
    if(dst_pitch==row_bytes&&src_pitch==row_bytes){memcpy(dst,src,(size_t)row_bytes*rows);return;}
    for(unsigned y=0;y<rows;++y)memcpy(dst+(size_t)y*dst_pitch,src+(size_t)y*src_pitch,row_bytes);
}
/* D32_FLOAT_S8X24 rows -> D32_FLOAT: keep the first dword of each 8-byte texel. */
static void pack_depth_rows(unsigned char *dst,const unsigned char *src,unsigned src_pitch,unsigned width,unsigned rows){
    for(unsigned y=0;y<rows;++y){
        const float *s=(const float*)(src+(size_t)y*src_pitch);float *d=(float*)dst+(size_t)y*width;unsigned x=0;
        for(;x+4<=width;x+=4){__m128 a=_mm_loadu_ps(s+2*x),b=_mm_loadu_ps(s+2*x+4);_mm_storeu_ps(d+x,_mm_shuffle_ps(a,b,_MM_SHUFFLE(2,0,2,0)));}
        for(;x<width;++x)d[x]=s[2*x];
    }
}
static DWORD WINAPI transfer_worker(void *unused){
    (void)unused;
    for(;;){
        WaitForSingleObject(job_ready,INFINITE);
        double t0=milliseconds();unsigned offset=0;
        for(unsigned i=0;i<3;++i){
            if(i==1&&job.pack_depth){unsigned width=job.row_bytes[1]/8;pack_depth_rows(shared_frames+offset,job.src[1],job.src_pitch[1],width,job.rows[1]);offset+=width*4*job.rows[1];continue;}
            copy_rows(shared_frames+offset,job.row_bytes[i],job.src[i],job.src_pitch[i],job.row_bytes[i],job.rows[i]);offset+=job.row_bytes[i]*job.rows[i];
        }
        double t1=milliseconds();MemoryBarrier();
        Fsr4Reply reply;
        int ok=wire(&job.header,sizeof(job.header),1)&&wire(&reply,sizeof(reply),0)&&reply.magic==FSR4_WIRE_MAGIC&&
            reply.sequence==job.header.sequence&&!reply.status&&reply.bytes==job.header.out_width*job.header.out_height*8u;
        double t2=milliseconds();
        if(ok){MemoryBarrier();copy_rows(job.dst,job.dst_pitch,shared_frames+FSR4_SHARED_OUTPUT_OFFSET,job.header.out_width*8,job.header.out_width*8,job.header.out_height);}
        job.copy_in_ms=t1-t0;job.server_ms=t2-t1;job.copy_out_ms=milliseconds()-t2;job.ok=ok;
        SetEvent(job_done);
    }
    return 0;
}
static int ensure_output_stages(ID3D11DeviceContext*ctx,ID3D11Resource*output){
    if(out_stage[0]&&out_stage[1])return 1;
    ID3D11Texture2D*t=NULL;if(FAILED(IUnknown_QueryInterface((IUnknown*)output,&IID_ID3D11Texture2D,(void**)&t)))return 0;
    D3D11_TEXTURE2D_DESC d;ID3D11Texture2D_GetDesc(t,&d);ID3D11Texture2D_Release(t);
    d.BindFlags=d.MiscFlags=0;d.Usage=D3D11_USAGE_STAGING;d.CPUAccessFlags=D3D11_CPU_ACCESS_WRITE;
    ID3D11Device*device=NULL;ID3D11DeviceContext_GetDevice(ctx,&device);
    for(unsigned i=0;i<2;++i)if(!out_stage[i]&&FAILED(ID3D11Device_CreateTexture2D(device,&d,NULL,&out_stage[i])))out_stage[i]=NULL;
    ID3D11Device_Release(device);
    return out_stage[0]&&out_stage[1];
}
/* Returns 0 only if the worker is still running and may touch mapped memory. */
static int finish_job(void){
    if(WaitForSingleObject(job_done,2500)==WAIT_OBJECT_0)return 1;
    if(connection!=INVALID_SOCKET)shutdown(connection,SD_BOTH);
    return WaitForSingleObject(job_done,1000)==WAIT_OBJECT_0;
}
static unsigned pipelined_frame(ID3D11DeviceContext*ctx,Params*p,unsigned seq){
    /* Consecutive protocol failures; reset by each completed frame. An absent
     * or restarting server is retried once per second while TAA draws. */
    static double worker_in_ms,worker_server_ms,worker_out_ms;
    double start=milliseconds(),waited=start,presented=start,copied,submitted;
    ID3D11Resource*resources[4]={0};const char*names[]={"Color","Depth","MotionVectors","Output"};
    for(unsigned i=0;i<4;++i)if(get_ptr(p,names[i],(void**)&resources[i])!=OK||!resources[i])return UNSUPPORTED;
    if(!map_frames())return UNSUPPORTED;
    if(!worker_thread){
        job_ready=CreateEventA(NULL,FALSE,FALSE,NULL);job_done=CreateEventA(NULL,FALSE,FALSE,NULL);
        if(job_ready&&job_done)worker_thread=CreateThread(NULL,0,transfer_worker,NULL,0,NULL);
        if(!worker_thread){live_fails=3;note("Cannot start transfer worker");return UNSUPPORTED;}
    }
    if(connection==INVALID_SOCKET){
        if(start<next_connect)return UNSUPPORTED;
        if(!connect_server()){next_connect=start+1000.;return UNSUPPORTED;}
        live_fails=0;
    }
    if(live_fails>=3)return UNSUPPORTED;
    unsigned reset=0,result=QUEUED,shown=0;get_ui(p,"Reset",&reset);
    if(last_call>0&&start-last_call>250.)reset=1;
    last_call=start;
    if(job_pending){
        if(!finish_job()){live_fails=3;note("Transfer worker did not finish; bridge disabled, staging left mapped");return UNSUPPORTED;}
        job_pending=0;waited=milliseconds();
        worker_in_ms=job.copy_in_ms;worker_server_ms=job.server_ms;worker_out_ms=job.copy_out_ms;
        for(unsigned i=0;i<3;++i)ID3D11DeviceContext_Unmap(ctx,(ID3D11Resource*)slots[mapped_slot].t[i].stage,0);
        ID3D11DeviceContext_Unmap(ctx,(ID3D11Resource*)out_stage[mapped_out],0);
        if(!job.ok){mapped_slot=mapped_out=-1;goto failed;}
        live_fails=0;
        if(!reset){ID3D11DeviceContext_CopyResource(ctx,resources[3],(ID3D11Resource*)out_stage[mapped_out]);result=OK;shown=job.header.sequence;}
        mapped_slot=mapped_out=-1;presented=milliseconds();
    }
    unsigned keep=result==OK?OK:UNSUPPORTED;
    if(reset)queued_slot=-1;
    int next=queued_slot==0?1:0;Slot*s=&slots[next];
    for(unsigned i=0;i<3;++i)if(!prepare_texture(ctx,resources[i],&s->t[i])){queued_slot=-1;return keep;}
    if((uint64_t)s->t[0].bytes+s->t[1].bytes+s->t[2].bytes>FSR4_SHARED_OUTPUT_OFFSET||!build_header(p,s->t,resources[3],seq,&s->header)||!ensure_output_stages(ctx,resources[3])){queued_slot=-1;return keep;}
    s->header.reset=reset;
    copied=milliseconds();
    if(queued_slot>=0){
        Slot*q=&slots[queued_slot];D3D11_MAPPED_SUBRESOURCE m;unsigned mapped=0;
        for(;mapped<3;++mapped){
            Transfer*t=&q->t[mapped];
            if(FAILED(ID3D11DeviceContext_Map(ctx,(ID3D11Resource*)t->stage,0,D3D11_MAP_READ,0,&m)))break;
            job.src[mapped]=m.pData;job.src_pitch[mapped]=m.RowPitch;job.row_bytes[mapped]=t->desc.Width*texture_bytes(t->desc.Format);job.rows[mapped]=t->desc.Height;
        }
        if(mapped==3&&SUCCEEDED(ID3D11DeviceContext_Map(ctx,(ID3D11Resource*)out_stage[next_out],0,D3D11_MAP_WRITE,0,&m))){
            job.dst=m.pData;job.dst_pitch=m.RowPitch;job.header=q->header;
            job.pack_depth=q->t[1].desc.Format==DXGI_FORMAT_R32G8X24_TYPELESS;
            if(job.pack_depth){job.header.depth_format=DXGI_FORMAT_R32_FLOAT;job.header.depth_bytes/=2;}
            mapped_slot=queued_slot;mapped_out=next_out;next_out=!next_out;job_pending=1;SetEvent(job_ready);
        }else{
            /* Frame N-1 is dropped; restart history so N is not reprojected from N-2. */
            for(unsigned i=0;i<mapped;++i)ID3D11DeviceContext_Unmap(ctx,(ID3D11Resource*)q->t[i].stage,0);
            s->header.reset=1;
        }
    }
    submitted=milliseconds();queued_slot=next;
    if(seq<=4||seq%120==0){char line[400];snprintf(line,sizeof(line),"pipeline frame=%u shown=%u latency=%u wait_ms=%.3f present_ms=%.3f copy_issue_ms=%.3f map_submit_ms=%.3f total_ms=%.3f worker_in_ms=%.3f worker_server_ms=%.3f worker_out_ms=%.3f",
        seq,shown,shown?seq-shown:0,waited-start,presented-waited,copied-presented,submitted-copied,submitted-start,worker_in_ms,worker_server_ms,worker_out_ms);note(line);}
    return result;
failed:
    if(connection!=INVALID_SOCKET)closesocket(connection);connection=INVALID_SOCKET;queued_slot=-1;++live_fails;note("Pipelined frame server failed; using normal TAA fallback");return UNSUPPORTED;
}

static int dump_texture(ID3D11DeviceContext *ctx,ID3D11Resource *resource,const char *dir,const char *name,FILE *meta){
    if(!resource){fprintf(meta,"%s absent\n",name);return 0;}
    ID3D11Texture2D *texture=NULL,*stage=NULL;ID3D11Device *device=NULL;
    HRESULT hr=IUnknown_QueryInterface((IUnknown*)resource,&IID_ID3D11Texture2D,(void**)&texture);
    if(FAILED(hr))return 0;
    D3D11_TEXTURE2D_DESC d;ID3D11Texture2D_GetDesc(texture,&d);ID3D11DeviceContext_GetDevice(ctx,&device);
    unsigned pixel=0;
    switch(d.Format){case DXGI_FORMAT_R16G16B16A16_FLOAT:pixel=8;break;case DXGI_FORMAT_R32G32B32A32_FLOAT:pixel=16;break;
        case DXGI_FORMAT_R16G16_FLOAT:case DXGI_FORMAT_R32_FLOAT:case DXGI_FORMAT_R32_TYPELESS:case DXGI_FORMAT_R24G8_TYPELESS:case DXGI_FORMAT_R11G11B10_FLOAT:case DXGI_FORMAT_R8G8B8A8_UNORM:pixel=4;break;
        case DXGI_FORMAT_R32G32_FLOAT:case DXGI_FORMAT_R32G8X24_TYPELESS:pixel=8;break;default:break;}
    fprintf(meta,"%s %u %u format=%u bytes_per_pixel=%u\n",name,d.Width,d.Height,d.Format,pixel);
    if(!pixel||d.SampleDesc.Count!=1||d.Width>4096||d.Height>4096)goto done;
    d.BindFlags=0;d.MiscFlags=0;d.Usage=D3D11_USAGE_STAGING;d.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
    hr=ID3D11Device_CreateTexture2D(device,&d,NULL,&stage);if(FAILED(hr))goto done;
    ID3D11DeviceContext_CopyResource(ctx,(ID3D11Resource*)stage,resource);
    D3D11_MAPPED_SUBRESOURCE map;hr=ID3D11DeviceContext_Map(ctx,(ID3D11Resource*)stage,0,D3D11_MAP_READ,0,&map);
    if(SUCCEEDED(hr)){
        char path[MAX_PATH];snprintf(path,sizeof(path),"%s\\%s.raw",dir,name);FILE*f=fopen(path,"wb");
        if(f){for(unsigned y=0;y<d.Height;++y)fwrite((char*)map.pData+y*map.RowPitch,pixel,d.Width,f);fclose(f);}
        ID3D11DeviceContext_Unmap(ctx,(ID3D11Resource*)stage,0);
    }
done:
    if(stage)ID3D11Texture2D_Release(stage);if(device)ID3D11Device_Release(device);ID3D11Texture2D_Release(texture);return SUCCEEDED(hr);
}

EXP NVSDK_NGX_D3D11_Init_ProjectID(void){if(!houji_fsr4_experimental_enabled())return UNSUPPORTED;init();note(live_enabled?"Goat native HTP bridge initialized":"Goat capture backend initialized; returns fallback on evaluate");return OK;}
EXP NVSDK_NGX_D3D11_Init_with_ProjectID(void){return NVSDK_NGX_D3D11_Init_ProjectID();}
EXP NVSDK_NGX_D3D11_Init(void){return NVSDK_NGX_D3D11_Init_ProjectID();}
EXP NVSDK_NGX_D3D11_Init_Ext(void){return NVSDK_NGX_D3D11_Init_ProjectID();}
EXP NVSDK_NGX_D3D11_AllocateParameters(Params **p){*p=make_params();return *p?OK:UNSUPPORTED;}
EXP NVSDK_NGX_D3D11_GetParameters(Params **p){return NVSDK_NGX_D3D11_AllocateParameters(p);}
EXP NVSDK_NGX_D3D11_GetCapabilityParameters(Params **p){unsigned r=NVSDK_NGX_D3D11_AllocateParameters(p);if(r==OK)capabilities(*p);note("NGX capability query");return r;}
EXP NVSDK_NGX_D3D11_DestroyParameters(Params*p){free(p);return OK;}
EXP NVSDK_NGX_D3D11_Shutdown(void){return OK;}
EXP NVSDK_NGX_D3D11_Shutdown1(void*p){(void)p;return OK;}
EXP NVSDK_NGX_D3D11_GetFeatureRequirements(void*a,void*b,void*out){(void)a;(void)b;if(out)*(unsigned*)out=0;return OK;}
EXP NVSDK_NGX_D3D11_GetScratchBufferSize(int feature,Params*p,size_t*s){(void)feature;(void)p;if(s)*s=0;return OK;}
EXP NVSDK_NGX_D3D11_CreateFeature(ID3D11DeviceContext*ctx,unsigned type,Params*p,Feature**out){
    if(!houji_fsr4_experimental_enabled()){if(out)*out=NULL;return UNSUPPORTED;}
    (void)ctx;if(type!=1)return UNSUPPORTED;Feature*f=calloc(1,sizeof(*f));if(!f)return UNSUPPORTED;
    f->id=1;get_ui(p,"Width",&f->width);get_ui(p,"Height",&f->height);get_ui(p,"OutWidth",&f->out_width);get_ui(p,"OutHeight",&f->out_height);get_ui(p,"DLSS.Feature.Create.Flags",&f->flags);*out=f;
    char b[180];snprintf(b,sizeof(b),"create %ux%u -> %ux%u flags=%u",f->width,f->height,f->out_width,f->out_height,f->flags);note(b);return OK;
}
EXP NVSDK_NGX_D3D11_ReleaseFeature(Feature*f){free(f);return OK;}
EXP NVSDK_NGX_D3D11_EvaluateFeature(ID3D11DeviceContext*ctx,Feature*f,Params*p,void*callback){
    if(!houji_fsr4_experimental_enabled())return UNSUPPORTED;
    (void)callback;LONG seq=InterlockedIncrement(&sequence);if(live_enabled)return live_frame(ctx,f,p,(unsigned)seq);if(seq>3)return UNSUPPORTED;
    char dir[MAX_PATH],path[MAX_PATH];snprintf(dir,sizeof(dir),"%s\\f%02ld",folder,seq-1);CreateDirectoryA(dir,NULL);snprintf(path,sizeof(path),"%s\\frame.txt",dir);FILE*meta=fopen(path,"w");if(!meta)return UNSUPPORTED;
    fprintf(meta,"feature %u %u %u %u flags=%u\n",f->width,f->height,f->out_width,f->out_height,f->flags);
    const char *numeric[]={"Jitter.Offset.X","Jitter.Offset.Y","MV.Scale.X","MV.Scale.Y","DLSS.Pre.Exposure","Reset","DLSS.Render.Subrect.Dimensions.Width","DLSS.Render.Subrect.Dimensions.Height"};
    for(unsigned i=0;i<sizeof(numeric)/sizeof(numeric[0]);++i){Entry*e=entry(p,numeric[i],0);if(e)fprintf(meta,"%s %.9g\n",numeric[i],e->number);}
    for(unsigned i=0;i<p->count;++i){Entry*e=&p->entries[i];if(e->kind==1)continue;/* numeric keys only, no account data */}
    const char*keys[]={"Color","Depth","MotionVectors","ExposureTexture","Output"};
    for(unsigned i=0;i<5;++i){ID3D11Resource*r=NULL;get_ptr(p,keys[i],(void**)&r);if(i==4){if(r){ID3D11Texture2D*t=NULL;if(SUCCEEDED(IUnknown_QueryInterface((IUnknown*)r,&IID_ID3D11Texture2D,(void**)&t))){D3D11_TEXTURE2D_DESC d;ID3D11Texture2D_GetDesc(t,&d);fprintf(meta,"Output %u %u format=%u\n",d.Width,d.Height,d.Format);ID3D11Texture2D_Release(t);}}}else dump_texture(ctx,r,dir,keys[i],meta);}
    fclose(meta);note("Captured temporal inputs; returning failure for original TAA fallback");return UNSUPPORTED;
}
EXP NVSDK_NGX_UpdateFeature(void){return OK;}
EXP NVSDK_NGX_GetSnippetVersion(void){return 0x15;}
EXP Houji_EvaluateTAA(ID3D11DeviceContext*ctx,ID3D11Resource*color,ID3D11Resource*depth,ID3D11Resource*mv,ID3D11Resource*out,const float*view,unsigned is_reset){
    if(!houji_fsr4_experimental_enabled())return UNSUPPORTED;
    init();if(!live_enabled)return UNSUPPORTED;
    Params*p=make_params();if(!p)return UNSUPPORTED;
    set_ptr(p,"Color",color);set_ptr(p,"Depth",depth);set_ptr(p,"MotionVectors",mv);set_ptr(p,"Output",out);
    unsigned rw=(unsigned)view[130*4],rh=(unsigned)view[130*4+1];
    set_ui(p,"DLSS.Render.Subrect.Dimensions.Width",rw);set_ui(p,"DLSS.Render.Subrect.Dimensions.Height",rh);
    set_f(p,"Jitter.Offset.X",view[28*4+8]*rw*.5f);set_f(p,"Jitter.Offset.Y",view[28*4+9]*rh*-.5f);
    set_ptr(p,"Houji.ClipToPrevious",(void*)(view+122*4));set_ui(p,"Reset",is_reset);
    Feature f={1,rw,rh,1280,720,75};unsigned seq=(unsigned)InterlockedIncrement(&sequence);
    unsigned result=pipeline_enabled?pipelined_frame(ctx,p,seq):live_frame(ctx,&f,p,seq);free(p);return result;
}
BOOL WINAPI DllMain(HINSTANCE h,DWORD reason,LPVOID reserved){(void)reserved;if(reason==DLL_PROCESS_ATTACH)DisableThreadLibraryCalls(h);return TRUE;}
