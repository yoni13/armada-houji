// SPDX-License-Identifier: GPL-2.0-only
// Retain authentication, telemetry freshness, current lease and stock tables.
#include "charge-model.hpp"
#include "stock-battery-state.hpp"
#include <iostream>

using namespace houji;
struct Paths {
    fs::path root;
    fs::path at(const std::string&p)const{return root.empty()?fs::path(p):root/p.substr(1);}
    fs::path bat()const{return at("/sys/class/power_supply/battery");}
    fs::path usb()const{return at("/sys/class/power_supply/qcom-battmgr-usb");}
    fs::path wls()const{return at("/sys/class/power_supply/qcom-battmgr-wls");}
    fs::path aux()const{
        fs::path result;int found=0;
        for(const auto &p:fs::directory_iterator(at("/sys/bus/auxiliary/devices")))
            if(p.path().filename().string().rfind("pmic_glink.power-supply.",0)==0 && fs::exists(p.path()/"houji_charger_state")){result=p.path()/"houji_charger_state";++found;}
        if(found!=1)throw std::runtime_error("Charger state unavailable or ambiguous");
        return result;
    }
};
static double mono(){return std::chrono::duration<double>(Clock::now().time_since_epoch()).count();}
static double boot(){timespec t{};clock_gettime(CLOCK_BOOTTIME,&t);return t.tv_sec+t.tv_nsec/1e9;}
static int setting(const Paths&p){try{int n=integer(p.at("/etc/armada/houji-charge-limit"));return n>=50&&n<100?n:-1;}catch(...){return -1;}}
static bool light(const Paths&p){
    bool unknown=false;
    for(const auto &s:{p.usb(),p.wls()}){
        try{auto v=trim(read(s/"online"));if(v=="1"||v=="2")return true;if(v!="0")unknown=true;}
        catch(...){if(s==p.usb()||fs::exists(s))unknown=true;}
    }
    return unknown;
}
static bool wireless(const Paths&p){
    if(integer(p.usb()/"online")==1){auto kind=read(p.usb()/"usb_type");if(kind.find("[PD]")!=std::string::npos||kind.find("[PD_PPS]")!=std::string::npos)return false;}
    return fs::exists(p.wls()/"online")&&integer(p.wls()/"online")==1;
}
static bool screen(const Paths&p){
    int count=0;bool on=false;
    for(const auto &d:fs::directory_iterator(p.at("/sys/class/backlight"))){int v=integer(d.path()/"bl_power");if(v<0||v>4)throw std::runtime_error("Invalid display state");++count;on|=v==0;}
    if(!count)throw std::runtime_error("Display state unavailable");
    return on;
}
static std::map<std::string,long long> auth_state(const fs::path&path){
    std::istringstream input(read(path));std::string line;std::map<std::string,long long> result;
    while(std::getline(input,line)){auto i=line.find('=');if(i==std::string::npos)throw std::runtime_error("Invalid charger state");size_t end;auto value=std::stoll(line.substr(i+1),&end);if(end!=line.size()-i-1)throw std::runtime_error("Invalid charger value");result[line.substr(0,i)]=value;}
    return result;
}
static void finish_auth(const Paths&p){try{auto d=p.aux().parent_path()/"houji_charger";if(trim(read(d/"verify_process"))=="1")syswrite(d/"verify_process",0);}catch(...) {}}
static void finish(const Paths&p){finish_auth(p);try{syswrite(p.bat()/"constant_charge_current",FCC);}catch(...){syswrite(p.bat()/"constant_charge_current",0);}}
struct Authentication {
    int attempts=0,source=-1;double next=0;
    std::string step(const Paths&p){
        auto agent=p.at("/usr/libexec/armada/houji-stock-auth"),blob=p.at("/usr/lib/armada/houji/charging/batterysecret");
        if(!fs::is_regular_file(agent)||!fs::is_regular_file(blob))return "stock authentication agent not installed";
        bool wls=wireless(p);if(integer(p.usb()/"online")!=1&&!wls){attempts=0;next=0;source=-1;return "waiting for adapter";}
        int src=wls?1:0;if(src!=source){attempts=0;next=0;source=src;}
        auto type=read(p.usb()/"usb_type");if(!wls && type.find("[PD]")==std::string::npos&&type.find("[PD_PPS]")==std::string::npos)return "waiting for USB-PD";
        fs::path d;try{d=p.aux().parent_path()/"houji_charger";}catch(...){return "authentication interface unavailable";}
        auto verified=[&](const char*name){return trim(read(d/name))=="1";};
        if(verified("authentic")&&(wls||verified("pd_verifed")))return "stock authentication passed";
        if(attempts>=3)return "stock authentication attempts exhausted";
        int temp=integer(p.bat()/"temp");
        if(integer(p.bat()/"constant_charge_current")!=FCC||temp<100||temp>400||trim(read(p.bat()/"health"))!="Good")return "authentication waiting for conservative conditions";
        if(mono()<next)return "authentication waiting for retry";
        ++attempts;next=mono()+60;
        try{
            for(auto [mode,key]:std::vector<std::pair<std::string,std::string>>{{"--gauge","authentic"},{"--adapter","pd_verifed"}}){
                if((wls&&mode=="--adapter")||verified(key.c_str()))continue;
                run({agent.string(),blob.string(),mode,d.string()},30);
                if(!verified(key.c_str())){finish_auth(p);return "stock authentication failed";}
            }
        }catch(const std::runtime_error&){finish_auth(p);return "stock authentication failed or timed out";}
        finish_auth(p);return "stock authentication passed";
    }
};
static long long ceiling(const Paths&p,const char*name,long long fallback){
    auto file=p.at(std::string("/etc/armada/")+name);auto v=fs::exists(file)?integer(file):fallback;
    if(v<1000000||v>15600000)throw std::runtime_error("Invalid staging ceiling");
    return v;
}
static std::pair<long long,bool> wired_limit(const fs::path&d,const std::string&type,const std::map<std::string,long long>&a,long long normal,long long hyper){
    auto get=[&](const char*key){auto i=a.find(key);return i==a.end()?-1:i->second;};
    if(type.find("[PD_PPS]")==std::string::npos||get("battery_authentic")!=1||get("pd_verified")!=1)return {normal,false};
    try{if(integer(d/"fcc_suspend_guard")==1&&integer(d/"fastchg_mode")==1&&integer(d/"apdo_max")>=50){bool stock=false;try{stock=integer(d/"fcc_fastcharge_temp_max")==470;}catch(...){}return {std::max(normal,hyper),stock};}}catch(...){}
    return {normal,false};
}
static std::string tick(const Paths&p,double&next,long long cap,bool wls,bool fast,bool hold,bool restore){
    bool online=integer(p.usb()/"online")==1;auto type=read(p.usb()/"usb_type");auto temp=integer(p.bat()/"temp");auto health=trim(read(p.bat()/"health"));
    bool pd=type.find("[PD]")!=std::string::npos||type.find("[PD_PPS]")!=std::string::npos;
    long long desired=!wls&&(!online||!pd)?-1:health!="Good"||temp<100||temp>(fast&&type.find("[PD_PPS]")!=std::string::npos?470:400)?0:FCC;
    auto fcc=p.bat()/"constant_charge_current";
    if(hold&&(online||wls)){if(integer(fcc)!=0)syswrite(fcc,0);return "charging held at configured limit";}
    if(desired<0){if(restore&&online&&integer(fcc)==0)syswrite(fcc,FCC);next=0;return "waiting for USB-PD";}
    if(desired){if(cap<0||cap>15600000)throw std::runtime_error("Invalid current cap");desired=cap;}
    if(desired>FCC||integer(fcc)!=desired)syswrite(fcc,desired);
    if(!desired)return "charging paused for temperature/health";
    auto path=p.aux();auto a=auth_state(path);
    if(!wls&&mono()>=next&&a.count("verify_process")&&a.count("pd_verified")&&a.at("verify_process")==0&&a.at("pd_verified")==0&&integer(p.usb()/"input_current_limit")<=100000){syswrite(path.parent_path()/"houji_charger_verify_idle",0);next=mono()+30;return "adapter verification idle";}
    return std::string(wls?"wireless":"USB-PD")+" cap="+std::to_string(desired/1000)+"mA";
}
int main(int argc,char **argv){
    try{
        if(argc==2&&std::string(argv[1])=="--model"){
            ChargingControl control;long long vals[7],current,maximum;int authentic,verified,screen_on,wls,usb,fast;double now;
            while(std::cin>>now>>authentic>>verified>>current>>maximum>>screen_on>>wls>>usb>>fast>>vals[0]>>vals[1]>>vals[2]>>vals[3]>>vals[4]>>vals[5]>>vals[6]){
                Temperatures t;int i=0;for(const char*name:{"pa_therm0","quiet_therm","charger_therm0","cpu_therm","battery","wifi_therm","usb_therm"})t[name]=vals[i++];
                try{auto vote=control.step(t,authentic,verified,current,maximum,screen_on,now,wls,usb,fast);std::cout<<vote<<' '<<control.wired_level<<' '<<control.wireless_level<<'\n';}
                catch(const std::exception&){std::cout<<"error\n";}
            }return 0;
        }
        if(argc==2&&std::string(argv[1])=="--limit-model"){ChargeLimit limit;int cap,percent,on;while(std::cin>>cap>>percent>>on)std::cout<<limit.step(cap,percent,on)<<'\n';return 0;}
        std::string action;std::vector<char*> args{argv[0]};
        for(int i=1;i<argc;++i){if(std::string(argv[i])=="--needs-light-sleep"||std::string(argv[i])=="--finish-auth")action=argv[i];else args.push_back(argv[i]);}
        Options o(args.size(),args.data(),{"--root","--interval-ms"});Paths p{o.get("--root","")};
        if(action=="--needs-light-sleep"){bool needed=light(p);std::cout<<(needed?"light":"offline")<<'\n';return needed?0:1;}
        if(action=="--finish-auth"){finish(p);return 0;}
        long long normal=ceiling(p,"houji-charge-ceiling",3000000),hyper=ceiling(p,"houji-hypercharge-ceiling",normal);
        syswrite(p.at("/sys/module/qcom_battmgr/parameters/houji_max_fcc_ua"),std::max(normal,hyper));
        signals();ChargingControl control;Authentication auth;ChargeLimit hold;StockBatteryState stock;double next=0;std::string previous;int published=-2;bool published_hold=false;
        do{
            auto start=Clock::now();std::string message;
            // Independent of authentication eligibility and board-current policy.
            // A state-forwarding failure must not prevent FCC lease renewal.
            try{stock.update(p.bat(),p.usb(),p.aux().parent_path()/"houji_charger",screen(p),boot());}
            catch(const std::exception&e){std::cerr<<"Stock battery state refresh failed: "<<e.what()<<'\n';}
            try{
                long long cap=FCC;bool fast=false,wls=wireless(p),board_ok=false;
                try{
                    auto t=board_temperatures(p.bat(),p.at("/sys/bus/iio/devices"));auto path=p.aux(),d=path.parent_path()/"houji_charger";auto verified=auth_state(path);
                    syswrite(d/"thermal_board_temp",skin_temperature(t)/100);
                    if(integer(d/"fcc_lease_seconds")!=10)throw std::runtime_error("Kernel current fallback unavailable");
                    if(fs::exists(d/"fcc_max_ua")){
                        if(!wls&&verified.count("pd_verified")&&verified.at("pd_verified")==0&&auth.attempts<3)verified["pd_verified"]=-1;
                        long long maximum=integer(d/"fcc_max_ua");
                        if(wls){auto f=p.at("/etc/armada/houji-wireless-ceiling");auto m=fs::exists(f)?integer(f):FCC;if(m<FCC||m>10000000)throw std::runtime_error("Invalid wireless ceiling");maximum=std::min(maximum,m);}
                        else{auto selected=wired_limit(d,read(p.usb()/"usb_type"),verified,normal,hyper);maximum=std::min(maximum,selected.first);fast=selected.second;}
                        int battery_auth=verified.count("battery_authentic")?verified.at("battery_authentic"):-1,pd_auth=verified.count("pd_verified")?verified.at("pd_verified"):-1;
                        cap=control.step(t,battery_auth,pd_auth,integer(p.bat()/"constant_charge_current"),maximum,screen(p),boot(),wls,integer(p.usb()/"online")==1,fast);
                        for(auto [name,level]:std::vector<std::pair<std::string,int>>{{"wired_thermal_level",control.wired_level},{"wireless_thermal_level",control.wireless_level}}){if(!wls&&name=="wireless_thermal_level")continue;syswrite(d/name,level);if(integer(d/name)!=level)throw std::runtime_error("Thermal level readback mismatch");}
                    }else if(!wls&&verified["battery_authentic"]==1&&verified["pd_verified"]==1&&t.at("battery")>=15000&&t.at("battery")<=38000&&skin_temperature(t)<35000&&t.at("usb_therm")<40000){bool safe=true;for(auto &[n,v]:t){(void)n;if(v>=45000)safe=false;}if(safe)cap=1000000;}
                    board_ok=true;
                }catch(const std::exception&){cap=FCC;fast=false;control.reset();}
                int limit=setting(p),capacity=-1;try{capacity=integer(p.bat()/"capacity");}catch(...){}
                bool attached=wls||integer(p.usb()/"online")==1;bool held=hold.step(limit,capacity,attached);
                message=tick(p,next,cap,wls,fast,held,hold.zeroed);
                if(held)hold.zeroed=true;else if(attached)hold.zeroed=false;
                if(limit!=published||held!=published_hold){try{atomic_write(p.at("/run/houji-charging/charge-limit.json"),"{\"limit\": "+(limit<0?"null":std::to_string(limit))+", \"holding\": "+(held?"true":"false")+"}\n");published=limit;published_hold=held;}catch(...) {}}
                message+="; "+auth.step(p);if(!board_ok)message+="; waiting for board telemetry";
            }catch(const std::exception&e){control.reset();message=std::string("waiting for charger telemetry: ")+e.what();}
            if(message!=previous){std::cout<<message<<std::endl;previous=message;}
            if(o.once)break;
            auto elapsed=std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now()-start).count();pause(std::max<long long>(100,static_cast<long long>(o.interval(1000))-elapsed));
        }while(!stopped);
        return 0;
    }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}
}
