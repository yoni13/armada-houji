// SPDX-License-Identifier: GPL-2.0-only
#include "board-thermal.hpp"
#include <iostream>

using namespace houji;
struct Table {std::vector<int> trigger,cap;};
static const std::map<int,Table> tables={
 {0,{{25000,39000,40000,45000,48000},{2035200,1689600,1459200,1344000,1017600}}},
 {2,{{25000,34000,37000,39000,41000,42000,43000,44000,45000,46000,47000,48000,49000,50000},
     {2630400,2323200,2188800,2035200,1824000,1612800,1612800,1286400,1286400,1286400,1190400,1190400,1075200,960000}}},
 {5,{{25000,34000,37000,39000,41000,41500,42000,43000,45000,46000,47000,48000,49000,50000},
     {2630400,2323200,2188800,1824000,1497600,1401600,1286400,1286400,1286400,1286400,1190400,1190400,1075200,960000}}},
 {7,{{25000,32000,34000,37000,39000,40000,41000,41500,42000,43000,44000,45000,46000,47000,48000,50000},
     {2630400,2169600,1939200,1824000,1708800,1363200,1248000,1248000,1248000,1248000,1248000,1132800,1132800,1017600,1017600,1017600}}}
};
static constexpr int gpu_mhz[]={903,834,770,720,680,629,578,500,422,366,310,231};
struct Limits {std::map<int,int> cpu;int gpu=0;std::set<int> paused;};
class Policy {
    bool game; Hysteresis gpu,cores{{50000},{48000}},battery{{1,3},{2,4},true};
public:
    explicit Policy(bool g):game(g),gpu(g?Hysteresis({46000,48000},{44000,45000}):Hysteresis({15000},{13000})) {}
    Limits update(int skin,int percent) {
        if(skin < -20000 || skin > 100000 || percent<0 || percent>100) throw std::runtime_error("Invalid thermal input");
        Limits out;
        for(const auto &[cpu,t]:tables) {out.cpu[cpu]=0;for(size_t i=0;i<t.trigger.size();++i) if(skin>=t.trigger[i]) out.cpu[cpu]=t.cap[i];}
        int level=gpu.update(skin);out.gpu=level<0?0:game?level+2:2;
        if(cores.update(skin)>=0) out.paused={3,4,7};
        level=battery.update(percent);
        if(level>=0) {
            std::map<int,int> caps;
            if(game && level==0) {caps={{0,1017600},{2,1497600},{5,1497600}};out.gpu=std::max(out.gpu,6);out.paused.insert({2,3,4,7});}
            else if(!game && level==0) {caps={{0,787200},{2,844800},{5,1286400}};out.paused.insert({3,4,7});}
            else {caps={{0,2265600},{2,1286400},{5,1286400}};if(game)out.gpu=std::max(out.gpu,3);out.paused.insert({4,7});}
            for(const auto &[cpu,freq]:caps) out.cpu[cpu]=out.cpu[cpu]?std::min(out.cpu[cpu],freq):freq;
        }
        return out;
    }
};
static std::vector<long long> frequencies(const fs::path &path) {
    std::istringstream input(read(path));std::set<long long,std::greater<long long>> values;long long value;
    while(input>>value) values.insert(value);
    if(values.empty() || !input.eof()) throw std::runtime_error("Invalid frequency table");
    return {values.begin(),values.end()};
}
static int cap_state(const std::vector<long long> &freqs,long long cap) {
    if(!cap)return 0;
    return std::min(static_cast<int>(std::count_if(freqs.begin(),freqs.end(),[&](auto f){return f>cap;})),static_cast<int>(freqs.size())-1);
}
struct CpuCap {
    fs::path device;std::vector<long long> freqs;int extra,state;
    CpuCap(const fs::path &d,const fs::path &cpu,int n):device(d),freqs(frequencies(cpu/"cpufreq"/("policy"+std::to_string(n))/"scaling_available_frequencies")),
        extra(integer(d/"max_state")+1-static_cast<long long>(freqs.size())),state(integer(d/"cur_state")) {
        if(extra<0) throw std::runtime_error("CPU cooling table mismatch");
    }
    void apply(int cap,bool jump) {
        int target=cap?cap_state(freqs,cap)+extra:0;
        state=jump?target:state+(target>state?1:target<state?-1:0);
        if(integer(device/"cur_state")!=state) syswrite(device/"cur_state",state);
    }
};
static bool game_mode(const fs::path &proc) {
    for(const auto &p:fs::directory_iterator(proc)) if(digits(p.path().filename().string())) {
        try {auto cmd=read(p.path()/"cmdline");if(cmd.substr(0,cmd.find('\0'))=="/usr/bin/gamescope")return true;}catch(...){}
    }
    return false;
}
int main(int argc,char **argv) {
    try {
        if(argc==3 && std::string(argv[1])=="--policy") {
            Policy policy(std::string(argv[2])=="game");int skin,percent;
            while(std::cin>>skin>>percent) {auto l=policy.update(skin,percent);for(auto [cpu,cap]:l.cpu){(void)cpu;std::cout<<cap<<' ';}std::cout<<l.gpu;for(int c:l.paused)std::cout<<' '<<c;std::cout<<'\n';}
            return 0;
        }
        Options o(argc,argv,{"--thermal","--cpus","--devfreq","--battery","--iio","--proc","--interval-ms"});
        fs::path root=o.get("--thermal","/sys/class/thermal"),cpu=o.get("--cpus","/sys/devices/system/cpu"),devfreq=o.get("--devfreq","/sys/class/devfreq"),
            bat=o.get("--battery","/sys/class/power_supply/battery"),iio=o.get("--iio","/sys/bus/iio/devices"),proc=o.get("--proc","/proc");
        std::map<int,CpuCap> caps;fs::path gpu;std::vector<long long> gf;
        for(const auto &d:fs::directory_iterator(root)) {
            if(d.path().filename().string().rfind("cooling_device",0)!=0)continue;
            auto kind=trim(read(d.path()/"type"));
            if(kind.rfind("cpufreq-cpu",0)==0){int n=std::stoi(kind.substr(11));caps.emplace(n,CpuCap(d.path(),cpu,n));}
            else if(kind.rfind("devfreq-",0)==0 && kind.size()>4 && kind.substr(kind.size()-4)==".gpu"){gpu=d.path();gf=frequencies(devfreq/kind.substr(8)/"available_frequencies");}
        }
        if(caps.size()!=4 || !caps.count(0)||!caps.count(2)||!caps.count(5)||!caps.count(7)||gpu.empty())throw std::runtime_error("Unexpected cooling devices");
        signals();std::set<int> offline;int written=0;bool game=game_mode(proc),jump=true;Policy policy(game);
        auto mode_check=Clock::now(),logged=Clock::time_point{};std::string last_error,last_major;
        auto online=[&](int n,int value){try{syswrite(cpu/("cpu"+std::to_string(n))/"online",value);return true;}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return false;}};
        auto cleanup=[&](){for(int n:offline)online(n,1);try{if(integer(gpu/"cur_state")==written)syswrite(gpu/"cur_state",0);}catch(...){}for(auto &[n,c]:caps){(void)n;try{syswrite(c.device/"cur_state",0);}catch(...){}}};
        try {
            do {
                auto now=Clock::now();if(now-mode_check>=std::chrono::seconds(5)){mode_check=now;bool g=game_mode(proc);if(g!=game){game=g;policy=Policy(game);jump=true;}}
                Limits l;long long skin;int percent;
                try {skin=skin_temperature(board_temperatures(bat,iio));percent=integer(bat/"capacity");l=policy.update(skin,percent);last_error.clear();}
                catch(const std::exception&e){if(last_error!=e.what())std::cerr<<"Thermal telemetry unavailable; holding limits: "<<e.what()<<'\n';last_error=e.what();if(o.once)throw;pause(o.interval(1000));continue;}
                for(auto &[n,c]:caps)c.apply(l.cpu.at(n),jump);
                int target=l.gpu?cap_state(gf,static_cast<long long>(gpu_mhz[std::min(l.gpu,11)])*1000000):0;
                int current=integer(gpu/"cur_state");
                if(current<=written && current!=target){syswrite(gpu/"cur_state",target);written=target;}
                for(int n:l.paused)if(n && !offline.count(n) && online(n,0))offline.insert(n);
                auto old=offline;for(int n:old)if(!l.paused.count(n)&&online(n,1))offline.erase(n);
                jump=false;
                std::string major=std::to_string(game)+":"+std::to_string(l.gpu);for(int n:l.paused)major+=":"+std::to_string(n);
                if(major!=last_major || now-logged>=std::chrono::seconds(30)){std::cout<<"skin "<<skin<<" mC battery "<<percent<<" mode "<<(game?"game":"normal")<<" gpu "<<l.gpu<<std::endl;last_major=major;logged=now;}
                if(o.once)break;
                pause(o.interval(1000));
            }while(!stopped);
        }catch(...){cleanup();throw;}
        cleanup();return 0;
    }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}
}
