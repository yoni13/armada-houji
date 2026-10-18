// Local D3D11 prototype bridge. GPU/NPU tensors share DMA-BUFs, while Wine game
// texture transfer is staged over loopback and is NOT an end-to-end zero-copy path.
#define FSR4_NO_MAIN
#include "experimental-gate.h"
#define FSR4_NO_PIPELINE_MAIN
#include "npu-pipeline.cpp"
#include "frame-wire.h"
#include <arpa/inet.h>
#include <sys/socket.h>
#include <cerrno>
#include <cmath>
#include <csignal>
#include <netinet/tcp.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <atomic>

struct Bytes { const uint8_t *ptr;size_t length;const uint8_t *data()const{return ptr;}size_t size()const{return length;}const uint8_t &operator[](size_t i)const{return ptr[i];} };
class SharedFrames {
    int fd=-1;
public:
    uint8_t *data=nullptr;
    explicit SharedFrames(const char *path){
        if(!path)return;
        fd=open(path,O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW,0600);
        struct stat st{};
        if(fd<0||fstat(fd,&st)||!S_ISREG(st.st_mode)||st.st_uid!=getuid()||ftruncate(fd,FSR4_SHARED_SIZE))throw std::runtime_error("Cannot create shared frame buffer");
        void *p=mmap(nullptr,FSR4_SHARED_SIZE,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
        if(p==MAP_FAILED)throw std::runtime_error("Cannot map shared frame buffer");
        data=static_cast<uint8_t*>(p);
    }
    ~SharedFrames(){if(data)munmap(data,FSR4_SHARED_SIZE);if(fd>=0)close(fd);}
};

static bool transfer(int fd,void *data,size_t size,bool sending){
    auto p=static_cast<char*>(data);
    while(size){ssize_t n=sending?send(fd,p,size,MSG_NOSIGNAL):recv(fd,p,size,0);if(n<0&&errno==EINTR)continue;if(n<=0)return false;p+=n;size-=n;}
    return true;
}
class Pipeline {
    std::unique_ptr<FsrQnn,decltype(&fsr_qnn_free)> q{fsr_qnn_create(),fsr_qnn_free};
    GPU g;
    std::string shaders;
    uint32_t w,h,rw,rh;
    size_t constants,weights,features,params,color,depth,mv,history,recurrent,reprojected,output,exposure;
    size_t packed_mv,camera,linear_output=0;bool use_linear=false;
    std::vector<uint8_t> depth_unpacked;
    bool first=true;
    double conversion_ms=0,upload_ms=0,compute_ms=0,download_ms=0;
    double stamp_sum[6]={};unsigned stamp_frames=0;
public:
    bool input_images_ready=false;
    uint32_t color_format=0;
    uint32_t motion_format=0;
    uint32_t depth_format=0;
    double gpu_pre_ms=0,npu_ms=0,gpu_post_ms=0;
    uint32_t get_rw()const{return rw;}
    uint32_t get_rh()const{return rh;}
private:
public:
    Pipeline(std::string s,uint32_t width,uint32_t height):shaders(std::move(s)),w(width),h(height),rw(width),rh(height){
        if(!q)throw std::runtime_error("HTP graph initialization failed");
        if(fsr_qnn_size(q.get(),0)!=(size_t)w*h*4)throw std::runtime_error("Wrong graph dimensions");
        g.init();if(getenv("FSR4_GPU_TIMESTAMPS"))g.enable_timestamps();
        if(const char *ablate=getenv("FSR4_ABLATE")){
            std::string a=ablate;skip_npu=a=="npu"||a=="all";skip_gpu=a=="gpu"||a=="all";
            std::cout<<"DIAGNOSTIC ablation: skip_npu="<<skip_npu<<" skip_gpu="<<skip_gpu<<" (output is not valid)\n"<<std::flush;
        }
        constants=g.buffer(sizeof(Constants),VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT);
        weights=g.buffer(1024,VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT);auto wb=read(shaders+"/pass0-weights.bin",1024);std::memcpy(g.buffers[weights].mapped,wb.data(),1024);
        features=g.import_buffer(fsr_qnn_size(q.get(),0),fsr_qnn_fd(q.get(),0),fsr_qnn_pointer(q.get(),0));
        params=g.import_buffer(fsr_qnn_size(q.get(),1),fsr_qnn_fd(q.get(),1),fsr_qnn_pointer(q.get(),1));
        history=g.image(w,h,VK_FORMAT_R16G16B16A16_SFLOAT,8);recurrent=g.image(w,h,VK_FORMAT_R8G8B8A8_UNORM,4);reprojected=g.image(w,h,VK_FORMAT_R16G16B16A16_SFLOAT,8);output=g.image(w,h,VK_FORMAT_R16G16B16A16_SFLOAT,8);
        g.upload(history,{});g.upload(recurrent,{});g.upload(reprojected,{});g.upload(output,{});
        // FSR4_LINEAR_OUTPUT: post-linear.spv writes the result into a mapped
        // buffer, removing the image-to-buffer copy pass.
        if(getenv("FSR4_LINEAR_OUTPUT")){linear_output=g.buffer((size_t)w*h*8,VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,VK_MEMORY_PROPERTY_HOST_CACHED_BIT);use_linear=true;std::cout<<"Linear output buffer enabled\n";}
        exposure=g.image(1,1,VK_FORMAT_R32_SFLOAT,4);float one=1;std::vector<uint8_t> e(4);std::memcpy(e.data(),&one,4);g.upload(exposure,e);
    }
    bool accepts(const Fsr4FrameHeader &f)const{
        if(f.magic!=FSR4_WIRE_MAGIC||f.version!=4||!f.width||!f.height||f.width>w||f.height>h||f.out_width!=w||f.out_height!=h)return false;
        if(!f.render_width||!f.render_height||f.render_width>f.width||f.render_height>f.height)return false;
        if(input_images_ready&&(f.width!=rw||f.height!=rh||f.color_format!=color_format||f.mv_format!=motion_format||f.depth_format!=depth_format))return false;
        if(!std::isfinite(f.jitter_x)||!std::isfinite(f.jitter_y)||!std::isfinite(f.mv_scale_x)||!std::isfinite(f.mv_scale_y))return false;
        size_t pixels=(size_t)f.width*f.height;
        bool color_ok=(f.color_format==26&&f.color_bytes==pixels*4)||(f.color_format==10&&f.color_bytes==pixels*8);
        return color_ok&&
               ((f.depth_format==19&&f.depth_bytes==pixels*8)||((f.depth_format==39||f.depth_format==41)&&f.depth_bytes==pixels*4))&&
               ((f.mv_format==16||f.mv_format==11)&&f.mv_bytes==pixels*8);
    }
    void reset(){first=true;}
    // Diagnostic only (FSR4_ABLATE): "npu" skips HTP, "gpu" skips all Vulkan
    // work, "all" skips both. Output is wrong; used to attribute frame cost.
    bool skip_npu=false,skip_gpu=false;
    Bytes execute(const Fsr4FrameHeader &f,Bytes rawcolor,Bytes rawdepth,Bytes rawmv){
        if(skip_gpu){
            if(!skip_npu&&fsr_qnn_execute(q.get()))throw std::runtime_error("HTP execution failed");
            const auto &previous=g.downloads.count(output)?g.buffers[g.downloads[output]]:g.download_buffer(output);
            return Bytes{static_cast<const uint8_t*>(previous.mapped),previous.size};
        }
        auto started=Clock::now();
        if(!input_images_ready){
            rw=f.width;rh=f.height;
            color_format=f.color_format;motion_format=f.mv_format;depth_format=f.depth_format;
            VkFormat vk_color=(color_format==10)?VK_FORMAT_R16G16B16A16_SFLOAT:VK_FORMAT_B10G11R11_UFLOAT_PACK32;
            size_t color_bpp=(color_format==10)?8:4;
            color=g.image(rw,rh,vk_color,color_bpp);depth=g.image(rw,rh,VK_FORMAT_R32_SFLOAT,4);mv=g.image(rw,rh,VK_FORMAT_R32G32_SFLOAT,8);
            if(motion_format==11){packed_mv=g.image(rw,rh,VK_FORMAT_R16G16B16A16_UNORM,8);camera=g.buffer(80,VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT);g.upload(mv,{});}
            depth_unpacked.resize((size_t)rw*rh*4);input_images_ready=true;
            std::cout<<"Input render="<<f.render_width<<"x"<<f.render_height<<" texture="<<rw<<"x"<<rh<<" color_fmt="<<color_format<<" output="<<w<<"x"<<h<<'\n'<<std::flush;
        }
        size_t pixels=(size_t)rw*rh;
        if(f.depth_format==19){for(size_t i=0;i<pixels;++i)std::memcpy(&depth_unpacked[i*4],&rawdepth[i*8],4);}
        Constants c{};c.inv_size[0]=1.f/w;c.inv_size[1]=1.f/h;c.scale[0]=float(w)/f.render_width;c.scale[1]=float(h)/f.render_height;c.inv_scale[0]=float(f.render_width)/w;c.inv_scale[1]=float(f.render_height)/h;
        c.jitter[0]=f.jitter_x;c.jitter[1]=f.jitter_y;c.mv_scale[0]=f.mv_scale_x/f.render_width;c.mv_scale[1]=f.mv_scale_y/f.render_height;c.tex_size[0]=w;c.tex_size[1]=h;c.max_render_size[0]=rw;c.max_render_size[1]=rh;
        c.width=w;c.height=h;c.width_lr=f.render_width;c.height_lr=f.render_height;c.reset=first||f.reset;c.pre_exposure=c.previous_pre_exposure=1.f;first=false;
        auto converted=Clock::now();
        // Two queue submissions per frame (pre-NPU and post-NPU). Each wait can
        // sit behind game GPU work, so separate per-pass submissions multiply it.
        g.begin_batch();g.stamp();
        std::memcpy(g.buffers[constants].mapped,&c,sizeof(c));g.upload_data(color,rawcolor.data(),rawcolor.size());
        if(f.depth_format==19)g.upload(depth,depth_unpacked);else g.upload_data(depth,rawdepth.data(),rawdepth.size());
        if(motion_format==11){
            float motion_cb[20];std::memcpy(motion_cb,f.clip_to_previous,64);motion_cb[16]=f.render_width;motion_cb[17]=f.render_height;motion_cb[18]=f.jitter_x;motion_cb[19]=f.jitter_y;
            std::memcpy(g.buffers[camera].mapped,motion_cb,sizeof(motion_cb));g.upload_data(packed_mv,rawmv.data(),rawmv.size());g.stamp();
            g.dispatch(shaders+"/decode-motion.spv",{{0,camera}},{},{{32,depth},{33,packed_mv}},{{64,mv}},(rw+7)/8,(rh+7)/8,false);
        }else{g.upload_data(mv,rawmv.data(),rawmv.size());g.stamp();}
        g.stamp();g.external_buffer_barrier(features,true);
        g.dispatch(shaders+"/pre.spv",{{0,constants},{1,weights}},{{75,features}},{{32,history},{33,mv},{34,depth},{35,color},{36,recurrent},{38,exposure}},{{67,reprojected}},(w+15)/16,(h+15)/16,true);
        g.external_buffer_barrier(features,false);g.stamp();
        auto a=Clock::now();g.end_batch();auto b=Clock::now();
        auto hash=[&](size_t index){const uint8_t*p=static_cast<const uint8_t*>(g.buffers[index].mapped);uint64_t v=1469598103934665603ull;for(size_t i=0;i<g.buffers[index].size;i+=8){uint64_t x;std::memcpy(&x,p+i,8);v=(v^x)*1099511628211ull;}return v;};
        // FSR4_DEBUG_HASH: per-frame hashes of the NPU input and output, to locate nondeterminism.
        static const bool debug_hash=getenv("FSR4_DEBUG_HASH")!=nullptr;
        if(debug_hash)std::cout<<"hash frame="<<f.sequence<<" features="<<std::hex<<hash(features)<<std::dec<<'\n';
        if(!skip_npu&&fsr_qnn_execute(q.get()))throw std::runtime_error("HTP execution failed");
        if(debug_hash)std::cout<<"hash frame="<<f.sequence<<" params="<<std::hex<<hash(params)<<std::dec<<'\n'<<std::flush;
        auto d=Clock::now();
        g.begin_batch();g.stamp();
        g.external_buffer_barrier(params,true);
        if(use_linear)g.dispatch(shaders+"/post-linear.spv",{{0,constants}},{{53,params},{86,linear_output}},{{35,color},{38,exposure},{41,reprojected}},{{65,history},{70,recurrent}},w/8,h/8,false);
        else g.dispatch(shaders+"/post.spv",{{0,constants}},{{53,params}},{{35,color},{38,exposure},{41,reprojected}},{{65,history},{66,output},{70,recurrent}},w/8,h/8,false);
        g.external_buffer_barrier(params,false);
        g.stamp();const auto &download=use_linear?g.buffers[linear_output]:g.download_buffer(output);g.stamp();
        Bytes result{static_cast<const uint8_t*>(download.mapped),download.size};
        g.end_batch();auto e=Clock::now();gpu_pre_ms=ms(a,b);npu_ms=ms(b,d);gpu_post_ms=ms(d,e);
        // Intervals: upload, decode, pre, (NPU and CPU between batches), post, download.
        auto stamps=g.read_stamps();
        if(stamps.size()==6){for(size_t i=0;i<6;++i)stamp_sum[i]+=stamps[i];++stamp_frames;}
        if(f.sequence%120==0&&stamp_frames){
            std::cout<<"gpu_timestamps frames="<<stamp_frames<<" upload_ms="<<stamp_sum[0]/stamp_frames<<" decode_ms="<<stamp_sum[1]/stamp_frames<<" pre_ms="<<stamp_sum[2]/stamp_frames
                     <<" post_ms="<<stamp_sum[4]/stamp_frames<<" download_ms="<<stamp_sum[5]/stamp_frames<<'\n'<<std::flush;
            std::fill(std::begin(stamp_sum),std::end(stamp_sum),0.);stamp_frames=0;
        }
        if(f.sequence<=3||f.sequence%120==0)std::cout<<"frame="<<f.sequence<<" reset="<<c.reset<<" gpu_pre_ms="<<ms(a,b)<<" npu_ms="<<ms(b,d)<<" gpu_post_download_ms="<<ms(d,e)<<'\n'<<std::flush;
        auto finished=Clock::now();conversion_ms=ms(started,converted);upload_ms=ms(converted,a);compute_ms=ms(a,e);download_ms=ms(e,finished);
        if(f.sequence<=3||f.sequence%120==0)std::cout<<"server_stages frame="<<f.sequence<<" convert_ms="<<conversion_ms<<" record_ms="<<upload_ms<<" submit_npu_ms="<<compute_ms<<'\n'<<std::flush;
        if(f.sequence==1){
            const char *capture=getenv("FSR4_SERVER_CAPTURE");
            if(capture){
                std::filesystem::create_directories(capture);std::string p=capture;
                write(p+"/frame-header.bin",&f,sizeof(f));write(p+"/color.raw",rawcolor.data(),rawcolor.size());
                write(p+"/depth.raw",rawdepth.data(),rawdepth.size());write(p+"/mv.raw",rawmv.data(),rawmv.size());
                write(p+"/output.bin",result.data(),result.size());
                write(p+"/constants.bin",&c,sizeof(c));
            }
        }
        return result;
    }
};

int main(int argc,char**argv){
    if(!houji_fsr4_experimental_enabled()){
        fprintf(stderr,"Houji FSR4 is disabled; explicit HOUJI_FSR4_EXPERIMENTAL=1 opt-in required.\n");
        return 1;
    }
    int listener=-1,client=-1;
    try{
        if(argc!=4)throw std::runtime_error("Usage: frame-server SHADERS OUTPUT_WIDTH OUTPUT_HEIGHT");
        uint32_t w=std::stoul(argv[2]),h=std::stoul(argv[3]);if(!w||!h||w>FSR4_MAX_OUTPUT||h>FSR4_MAX_OUTPUT||w%FSR4_OUTPUT_ALIGN||h%FSR4_OUTPUT_ALIGN||(uint64_t)w*h*8>FSR4_SHARED_SIZE-FSR4_SHARED_OUTPUT_OFFSET)throw std::runtime_error("Unsupported dimensions");
        Pipeline pipeline(argv[1],w,h);
        SharedFrames shared(getenv("FSR4_SHARED_FILE"));
        listener=socket(AF_INET,SOCK_STREAM|SOCK_CLOEXEC,0);if(listener<0)throw std::runtime_error("socket failed");
        // FSR4_TEST_PORT keeps test instances away from a game's bridge port.
        const char *test_port=getenv("FSR4_TEST_PORT");uint16_t port=test_port?(uint16_t)std::stoul(test_port):FSR4_WIRE_PORT;
        int reuse=1;setsockopt(listener,SOL_SOCKET,SO_REUSEADDR,&reuse,sizeof(reuse));sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);address.sin_port=htons(port);
        if(bind(listener,(sockaddr*)&address,sizeof(address))||listen(listener,1))throw std::runtime_error("bind/listen failed");
        std::cout<<"Listening on loopback port "<<port<<"; prototype staged D3D11 bridge\n"<<std::flush;
        while((client=accept4(listener,nullptr,nullptr,SOCK_CLOEXEC))>=0){
            int nodelay=1;setsockopt(client,IPPROTO_TCP,TCP_NODELAY,&nodelay,sizeof(nodelay));
            // No receive timeout: loading screens can pause the hook for many
            // seconds, and an idle game must keep its connection. EOF still ends it.
            timeval timeout{5,0};setsockopt(client,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));pipeline.reset();
            Fsr4FrameHeader f{};
            Clock::time_point previous_arrival{};bool have_previous=false;
            while(transfer(client,&f,sizeof(f),false)){
                auto started=Clock::now();
                if(!pipeline.accepts(f)){
                    std::cerr<<"Rejected frame: size="<<f.width<<"x"<<f.height<<" ("<<f.render_width<<"x"<<f.render_height<<") -> "<<f.out_width<<"x"<<f.out_height
                             <<" fmt="<<f.color_format<<","<<f.depth_format<<","<<f.mv_format
                             <<" bytes="<<f.color_bytes<<","<<f.depth_bytes<<","<<f.mv_bytes
                             <<" jitter="<<f.jitter_x<<","<<f.jitter_y<<" scale="<<f.mv_scale_x<<","<<f.mv_scale_y
                             <<" pipeline_state: ready="<<pipeline.input_images_ready<<" rw="<<pipeline.get_rw()<<" rh="<<pipeline.get_rh()
                             <<" mv_fmt="<<pipeline.motion_format<<" depth_fmt="<<pipeline.depth_format<<'\n';break;}
                std::vector<uint8_t>c,d,v;
                Bytes cb{},db{},vb{};
                if(f.version==4){
                    if(!shared.data||(uint64_t)f.color_bytes+f.depth_bytes+f.mv_bytes>FSR4_SHARED_OUTPUT_OFFSET)break;
                    std::atomic_thread_fence(std::memory_order_acquire);
                    cb={shared.data,f.color_bytes};db={shared.data+f.color_bytes,f.depth_bytes};vb={shared.data+f.color_bytes+f.depth_bytes,f.mv_bytes};
                }else{
                    c.resize(f.color_bytes);d.resize(f.depth_bytes);v.resize(f.mv_bytes);
                    if(!transfer(client,c.data(),c.size(),false)||!transfer(client,d.data(),d.size(),false)||!transfer(client,v.data(),v.size(),false))break;
                    cb={c.data(),c.size()};db={d.data(),d.size()};vb={v.data(),v.size()};
                }
                auto received=Clock::now();
                const Bytes b=pipeline.execute(f,cb,db,vb);Fsr4Reply reply{FSR4_WIRE_MAGIC,f.sequence,0,(uint32_t)b.size()};
                auto executed=Clock::now();
                if(f.version==4){
                    if(b.size()>FSR4_SHARED_SIZE-FSR4_SHARED_OUTPUT_OFFSET)throw std::runtime_error("Shared output too large");
                    uint8_t *output=shared.data+FSR4_SHARED_OUTPUT_OFFSET;
                    if(b.data()!=output)std::memcpy(output,b.data(),b.size());
                    std::atomic_thread_fence(std::memory_order_release);
                    if(!transfer(client,&reply,sizeof(reply),true))break;
                }else if(!transfer(client,&reply,sizeof(reply),true)||!transfer(client,const_cast<uint8_t*>(b.data()),b.size(),true))break;
                if(f.sequence<=3||f.sequence%120==0)std::cout<<"server_io frame="<<f.sequence<<" receive_ms="<<ms(started,received)<<" process_ms="<<ms(received,executed)<<" send_ms="<<ms(executed,Clock::now())<<'\n'<<std::flush;
                // Slow-frame diagnostics: a large gap means the game side stalled
                // or was paused; a long process time means this server was slow.
                double gap=have_previous?ms(previous_arrival,started):0,process=ms(received,executed);
                if(have_previous&&(gap>60||process>20))std::cout<<"slow frame="<<f.sequence<<" gap_ms="<<gap<<" process_ms="<<process<<" gpu_pre_ms="<<pipeline.gpu_pre_ms<<" npu_ms="<<pipeline.npu_ms<<" gpu_post_ms="<<pipeline.gpu_post_ms<<" send_ms="<<ms(executed,Clock::now())<<'\n'<<std::flush;
                previous_arrival=started;have_previous=true;
            }
            close(client);client=-1;pipeline.reset();
        }
        close(listener);return 0;
    }catch(const std::exception&e){std::cerr<<e.what()<<'\n';if(client>=0)close(client);if(listener>=0)close(listener);return 1;}
}
