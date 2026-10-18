// SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include "common.hpp"
#include <set>
#include <sstream>

namespace houji {
inline long long integer(const fs::path &path) {
    auto s=trim(read(path)); size_t end=0; auto value=std::stoll(s,&end);
    if(end!=s.size()) throw std::runtime_error("Invalid integer telemetry");
    return value;
}
inline void syswrite(const fs::path &path,long long value) {
    int fd=open(path.c_str(),O_WRONLY|O_CLOEXEC|O_TRUNC);
    if(fd<0) throw std::runtime_error("Cannot write control "+path.string());
    auto s=std::to_string(value)+"\n"; ssize_t n;
    do {n=write(fd,s.data(),s.size());} while(n<0 && errno==EINTR);
    close(fd);
    if(n!=static_cast<ssize_t>(s.size())) throw std::runtime_error("Control write failed");
}
using Temperatures=std::map<std::string,long long>;
inline const std::map<std::string,int> weights={{"pa_therm0",119},{"quiet_therm",799},{"charger_therm0",48},
    {"cpu_therm",115},{"battery",306},{"wifi_therm",-457}};
inline long long skin_temperature(const Temperatures &t) {
    if(!t.count("usb_therm")) throw std::runtime_error("Missing USB thermistor");
    for(const auto &[name,value]:t) { (void)name; if(value < -20000 || value > 100000) throw std::runtime_error("Invalid thermistor reading"); }
    long long weighted=0;
    for(const auto &[name,weight]:weights) weighted+=t.at(name)*weight;
    long long skin=weighted/1000+1779;
    if(skin<0 || skin>80000) throw std::runtime_error("Invalid virtual skin temperature");
    return skin;
}
inline Temperatures board_temperatures(const fs::path &battery,const fs::path &iio) {
    Temperatures t={{"battery",integer(battery/"temp")*100}}; int found=0;
    for(const auto &entry:fs::directory_iterator(iio)) {
        if(entry.path().filename().string().rfind("iio:device",0)!=0) continue;
        if(trim(read(entry.path()/"name"))!="spmi-adc5-gen3") continue;
        ++found;
        for(const auto &label:fs::directory_iterator(entry.path())) {
            auto filename=label.path().filename().string();
            if(filename.rfind("in_temp",0)!=0 || filename.size()<6 || filename.substr(filename.size()-6)!="_label") continue;
            auto name=trim(read(label.path()));
            if(!weights.count(name) && name!="usb_therm") continue;
            if(t.count(name)) throw std::runtime_error("Duplicate board thermistor");
            t[name]=integer(entry.path()/(filename.substr(0,filename.size()-6)+"_input"));
        }
    }
    if(found!=1) throw std::runtime_error("Board ADC unavailable or ambiguous");
    skin_temperature(t);return t;
}
struct Hysteresis {
    std::vector<int> trigger,clear; std::vector<bool> active; bool reverse;
    Hysteresis(std::vector<int> t,std::vector<int> c,bool r=false):trigger(t),clear(c),active(t.size(),false),reverse(r) {
        if(t.empty() || t.size()!=c.size()) throw std::runtime_error("Invalid hysteresis table");
        for(size_t i=0;i<t.size();++i) if(r ? c[i]<=t[i] : c[i]>=t[i]) throw std::runtime_error("Invalid hysteresis threshold");
    }
    int update(long long value) {
        for(size_t i=0;i<trigger.size();++i) {
            if(reverse ? value<=trigger[i] : value>=trigger[i]) active[i]=true;
            if(reverse ? value>=clear[i] : value<=clear[i]) active[i]=false;
        }
        if(reverse) {int i=static_cast<int>(active.size())-1;while(i>=0 && active[i]) --i;return i+1<static_cast<int>(active.size())?i+1:-1;}
        size_t i=0;while(i<active.size() && active[i]) ++i;return static_cast<int>(i)-1;
    }
};
}
