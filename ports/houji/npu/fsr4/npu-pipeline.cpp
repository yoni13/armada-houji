// Standalone temporal pipeline. QNN tensor DMA-BUFs are imported directly into
// Vulkan; file I/O and CPU visibility operations are only for input/output dumps.
#define FSR4_NO_MAIN
#include "frame-stages.cpp"
#include <chrono>
#include <memory>
#include <linux/dma-buf.h>
#include <sys/ioctl.h>

extern "C" {
struct FsrQnn;
FsrQnn *fsr_qnn_create(void);
void fsr_qnn_free(FsrQnn *);
int fsr_qnn_fd(FsrQnn *,unsigned);
void *fsr_qnn_pointer(FsrQnn *,unsigned);
size_t fsr_qnn_size(FsrQnn *,unsigned);
int fsr_qnn_execute(FsrQnn *);
}
using Clock=std::chrono::steady_clock;
static double ms(Clock::time_point a,Clock::time_point b){return std::chrono::duration<double,std::milli>(b-a).count();}

#ifndef FSR4_NO_PIPELINE_MAIN
int main(int argc,char **argv){
    const char *enabled=getenv("HOUJI_FSR4_EXPERIMENTAL");
    if(!enabled||strcmp(enabled,"1")){fprintf(stderr,"Houji FSR4 experiment is disabled\n");return 1;}
    try{
        if(argc!=5)throw std::runtime_error("Usage: npu-pipeline SHADER_DIR FRAME_ROOT OUTPUT_DIR FRAME_COUNT");
        std::string shaders=argv[1],frames=argv[2],out=argv[3];int count=std::stoi(argv[4]);
        if(count<1||count>100)throw std::runtime_error("Frame count out of range");
        std::filesystem::create_directories(out);
        std::unique_ptr<FsrQnn,decltype(&fsr_qnn_free)> q(fsr_qnn_create(),fsr_qnn_free);
        if(!q)throw std::runtime_error("HTP graph initialization failed");
        auto data=read(frames+"/f00/constants.bin",sizeof(Constants));Constants first;std::memcpy(&first,data.data(),sizeof(first));
        if(first.width%16||first.height%16||!first.width||!first.height)throw std::runtime_error("Unaligned dimensions");
        if(fsr_qnn_size(q.get(),0)!=(size_t)first.width*first.height*4||fsr_qnn_size(q.get(),1)!=(size_t)first.width*first.height*16)throw std::runtime_error("Graph and frame dimensions differ");
        GPU g;g.init();
        size_t constants=g.buffer(sizeof(Constants),VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT);
        size_t weights=g.buffer(1024,VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT);auto wb=read(shaders+"/pass0-weights.bin",1024);std::memcpy(g.buffers[weights].mapped,wb.data(),1024);
        size_t features=g.import_buffer(fsr_qnn_size(q.get(),0),fsr_qnn_fd(q.get(),0),fsr_qnn_pointer(q.get(),0));
        size_t params=g.import_buffer(fsr_qnn_size(q.get(),1),fsr_qnn_fd(q.get(),1),fsr_qnn_pointer(q.get(),1));
        uint32_t w=first.width,h=first.height,rw=first.width_lr,rh=first.height_lr;
        size_t color=g.image(rw,rh,VK_FORMAT_R16G16B16A16_SFLOAT,8),depth=g.image(rw,rh,VK_FORMAT_R32_SFLOAT,4),mv=g.image(rw,rh,VK_FORMAT_R16G16_SFLOAT,4);
        size_t history=g.image(w,h,VK_FORMAT_R16G16B16A16_SFLOAT,8),recurrent=g.image(w,h,VK_FORMAT_R8G8B8A8_UNORM,4),reprojected=g.image(w,h,VK_FORMAT_R16G16B16A16_SFLOAT,8),output=g.image(w,h,VK_FORMAT_R16G16B16A16_SFLOAT,8);
        g.upload(history,{});g.upload(recurrent,{});g.upload(reprojected,{});g.upload(output,{});
        size_t exposure=g.image(1,1,VK_FORMAT_R32_SFLOAT,4);float one=1;std::vector<uint8_t> e(4);std::memcpy(e.data(),&one,4);g.upload(exposure,e);
        for(int frame=0;frame<count;++frame){
            char label[32];std::snprintf(label,sizeof(label),"f%02d",frame);std::string dir=frames+"/"+label;
            auto cb=read(dir+"/constants.bin",sizeof(Constants));Constants c;std::memcpy(&c,cb.data(),sizeof(c));
            if(c.width!=w||c.height!=h||c.width_lr!=rw||c.height_lr!=rh)throw std::runtime_error("Dynamic size is not implemented");
            std::memcpy(g.buffers[constants].mapped,cb.data(),sizeof(c));
            g.upload(color,read(dir+"/color.bin",rw*rh*8));g.upload(depth,read(dir+"/depth.bin",rw*rh*4));g.upload(mv,read(dir+"/mv.bin",rw*rh*4));
            auto t0=Clock::now();
            g.external_buffer_barrier(features,true);
            g.dispatch(shaders+"/pre.spv",{{0,constants},{1,weights}},{{75,features}},{{32,history},{33,mv},{34,depth},{35,color},{36,recurrent},{38,exposure}},{{67,reprojected}},w/16,h/16,true);
            g.external_buffer_barrier(features,false);
            auto t1=Clock::now();
            if(fsr_qnn_execute(q.get()))throw std::runtime_error("HTP execution failed");
            auto t2=Clock::now();
            g.external_buffer_barrier(params,true);
            g.dispatch(shaders+"/post.spv",{{0,constants}},{{53,params}},{{35,color},{38,exposure},{41,reprojected}},{{65,history},{66,output},{70,recurrent}},w/8,h/8,false);
            g.external_buffer_barrier(params,false);
            auto t3=Clock::now();
            std::cout<<label<<" pre_ms="<<ms(t0,t1)<<" npu_ms="<<ms(t1,t2)<<" post_ms="<<ms(t2,t3)<<" total_ms="<<ms(t0,t3)<<" (includes host waits; frame 0 includes pipeline creation)\n"<<std::flush;
            g.download(output,out+"/"+label+"-output.bin");g.download(reprojected,out+"/"+label+"-reprojected.bin");
            g.download(history,out+"/"+label+"-history.bin");g.download(recurrent,out+"/"+label+"-recurrent.bin");
            for(unsigned i=0;i<2;++i){
                int fd=fsr_qnn_fd(q.get(),i);struct dma_buf_sync sync{DMA_BUF_SYNC_START|DMA_BUF_SYNC_READ};
                if(ioctl(fd,DMA_BUF_IOCTL_SYNC,&sync)<0)throw std::runtime_error("DMA-BUF CPU read start failed");
                write(out+"/"+label+(i?"-neural.bin":"-features-u8.bin"),fsr_qnn_pointer(q.get(),i),fsr_qnn_size(q.get(),i));
                sync.flags=DMA_BUF_SYNC_END|DMA_BUF_SYNC_READ;
                if(ioctl(fd,DMA_BUF_IOCTL_SYNC,&sync)<0)throw std::runtime_error("DMA-BUF CPU read end failed");
            }
        }
        return 0;
    }catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 1;}
}
#endif
