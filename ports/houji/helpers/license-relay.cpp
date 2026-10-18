// SPDX-License-Identifier: BSD-3-Clause
// QMI framing only; license decisions remain in stock QTEE UID 119.
#include "common.hpp"
#include <array>
#include <cstdint>
#include <dlfcn.h>
#include <functional>
#include <iostream>
#include <linux/qrtr.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>

using namespace houji;
using Bytes=std::vector<uint8_t>;
static uint16_t u16(const uint8_t *p){return p[0]|(p[1]<<8);}
static void put16(Bytes &b,uint16_t v){b.push_back(v&255);b.push_back(v>>8);}
static void put32(Bytes &b,uint32_t v){put16(b,v&65535);put16(b,v>>16);}
static void tlv(Bytes &b,uint8_t id,const Bytes &value){b.push_back(id);put16(b,value.size());b.insert(b.end(),value.begin(),value.end());}
static Bytes packet(uint8_t kind,uint16_t txn,uint16_t msg,const Bytes &body){Bytes b{kind};put16(b,txn);put16(b,msg);put16(b,body.size());b.insert(b.end(),body.begin(),body.end());return b;}
struct Message {uint8_t kind;uint16_t txn,msg;std::map<uint8_t,Bytes> fields;};
static Message decode(const Bytes &data){
    if(data.size()<7 || u16(data.data()+5)!=data.size()-7)throw std::runtime_error("Invalid QMI length");
    Message m{data[0],u16(data.data()+1),u16(data.data()+3),{}};
    for(size_t p=7;p<data.size();){
        if(data.size()-p<3)throw std::runtime_error("Truncated TLV");
        auto id=data[p];size_t size=u16(data.data()+p+1);p+=3;
        if(size>data.size()-p || m.fields.count(id))throw std::runtime_error("Truncated or duplicate TLV");
        m.fields[id]=Bytes(data.begin()+p,data.begin()+p+size);p+=size;
    }
    return m;
}
class Qtee {
    using Init=int(*)();using Check=int(*)(unsigned,void*,size_t,void*,size_t*);
    void *library; Init ready; Check check_fn;
public:
    explicit Qtee(const std::string &path){
        library=dlopen(path.c_str(),RTLD_NOW|RTLD_LOCAL);if(!library)throw std::runtime_error("QTEE library unavailable");
        auto init=reinterpret_cast<Init>(dlsym(library,"houji_qtee_init"));
        ready=reinterpret_cast<Init>(dlsym(library,"houji_qtee_ready"));check_fn=reinterpret_cast<Check>(dlsym(library,"houji_qtee_check"));
        if(!init||!ready||!check_fn||init())throw std::runtime_error("QTEE manager open failed");
        auto end=Clock::now()+std::chrono::seconds(30);
        while(ready()){if(stopped||Clock::now()>end)throw std::runtime_error("QTEE license storage unavailable");pause(500);}
    }
    std::pair<int,Bytes> check(unsigned secure,Bytes &input){
        int rc=0;std::array<uint8_t,1024> output{};size_t size=output.size();
        for(int attempt=0;attempt<5;++attempt){
            output.fill(0);size=output.size();rc=check_fn(secure,input.data(),input.size(),output.data(),&size);
            if(rc!=-99)break;
            pause(200*(attempt+1));
        }
        if(size>output.size())throw std::runtime_error("Invalid QTEE response length");
        Bytes result;if(!rc)result.assign(output.begin(),output.begin()+size);
        explicit_bzero(output.data(),output.size());return {rc,result};
    }
};
// Parse before ACK; malformed data must never reach the secure service.
static void process(const Bytes &data,const std::function<void(const Bytes&)> &send,Qtee &qtee){
    Message m;
    try{m=decode(data);}catch(const std::exception&){return;}
    if(m.kind!=0)return;
    if(m.msg!=0x20){Bytes body;tlv(body,2,{1,0,0x47,0});send(packet(2,m.txn,m.msg,body));return;}
    auto &f=m.fields;
    if(f.size()!=4 || !f.count(1)||!f.count(2)||!f.count(3)||!f.count(4) ||
       f[1].size()!=4||f[2].size()!=2||f[3].size()!=1||f[3][0]>1||f[4].size()<2||f[4].size()>1026||
       u16(f[4].data())!=f[4].size()-2)return;
    Bytes ack;tlv(ack,2,{0,0,0,0});tlv(ack,0x10,{0,0,0,0});send(packet(2,m.txn,m.msg,ack));
    Bytes input(f[4].begin()+2,f[4].end());auto [rc,output]=qtee.check(f[3][0],input);
    explicit_bzero(input.data(),input.size());
    Bytes body,status;put32(status,rc?6:0);tlv(body,1,status);tlv(body,2,f[2]);tlv(body,3,f[3]);
    Bytes array;put16(array,output.size());array.insert(array.end(),output.begin(),output.end());tlv(body,4,array);
    send(packet(4,0,0x20,body));
    explicit_bzero(output.data(),output.size());explicit_bzero(array.data(),array.size());
    std::cout<<"License check: secure="<<static_cast<unsigned>(f[3][0])<<" result="<<rc<<std::endl;
}
static void notify(){
    const char *path=getenv("NOTIFY_SOCKET");if(!path||!*path)return;
    sockaddr_un address{};address.sun_family=AF_UNIX;size_t len=strlen(path);
    if(len>=sizeof(address.sun_path))throw std::runtime_error("Invalid notify socket");
    memcpy(address.sun_path,path,len+1);if(path[0]=='@')address.sun_path[0]=0;
    int fd=socket(AF_UNIX,SOCK_DGRAM|SOCK_CLOEXEC,0);if(fd<0)throw std::runtime_error("Notify socket failed");
    ssize_t n=sendto(fd,"READY=1",7,MSG_NOSIGNAL,reinterpret_cast<sockaddr*>(&address),offsetof(sockaddr_un,sun_path)+len+(path[0]=='@'?0:1));close(fd);
    if(n!=7)throw std::runtime_error("Readiness notification failed");
}
int main(int argc,char **argv){
    int fd=-1;
    try{
        if(argc==2&&std::string(argv[1])=="--check-ready"){
            signals();Qtee qtee("/usr/libexec/houji-cellular/libhouji-qtee.so");
            std::cout<<"QTEE license storage ready"<<std::endl;return 0;
        }
        Options o(argc,argv,{"--qtee-library","--test-fd"});signals();
        Qtee qtee(o.get("--qtee-library","/usr/libexec/houji-cellular/libhouji-qtee.so"));
        bool test=o.values.count("--test-fd");
        if(test)fd=std::stoi(o.get("--test-fd",""));
        else{
            fd=socket(AF_QIPCRTR,SOCK_DGRAM|SOCK_CLOEXEC,0);if(fd<0)throw std::runtime_error("QRTR unavailable");
            sockaddr_qrtr local{};socklen_t len=sizeof(local);
            if(getsockname(fd,reinterpret_cast<sockaddr*>(&local),&len))throw std::runtime_error("QRTR address unavailable");
            Bytes registration;for(uint32_t value:{4u,0x423u,1u,local.sq_node,local.sq_port})put32(registration,value);
            sockaddr_qrtr control{AF_QIPCRTR,local.sq_node,QRTR_PORT_CTRL};
            if(sendto(fd,registration.data(),registration.size(),0,reinterpret_cast<sockaddr*>(&control),sizeof(control))!=static_cast<ssize_t>(registration.size()))throw std::runtime_error("QTEELS registration failed");
        }
        notify();std::cout<<"QTEE license storage ready; QTEELS relay published"<<std::endl;
        while(!stopped){
            pollfd poller{fd,POLLIN,0};int rc=poll(&poller,1,1000);if(rc<0&&errno==EINTR)continue;
            if(rc<0)throw std::runtime_error("QRTR poll failed");
            if(!rc)continue;
            uint8_t buffer[4096];sockaddr_qrtr peer{};socklen_t len=sizeof(peer);
            ssize_t n=recvfrom(fd,buffer,sizeof(buffer),MSG_TRUNC,reinterpret_cast<sockaddr*>(&peer),&len);
            if(n<0){if(errno==EINTR)continue;throw std::runtime_error("QRTR receive failed");}
            if(n>static_cast<ssize_t>(sizeof(buffer)) || (!test&&(peer.sq_node!=0||peer.sq_port==QRTR_PORT_CTRL)))continue;
            process(Bytes(buffer,buffer+n),[&](const Bytes&packet){
                ssize_t sent=test?send(fd,packet.data(),packet.size(),MSG_NOSIGNAL):sendto(fd,packet.data(),packet.size(),0,reinterpret_cast<sockaddr*>(&peer),sizeof(peer));
                if(sent!=static_cast<ssize_t>(packet.size()))throw std::runtime_error("QRTR send failed");
            },qtee);
        }
        close(fd);return 0;
    }catch(const std::exception&e){if(fd>=0)close(fd);std::cerr<<e.what()<<'\n';return 1;}
}
