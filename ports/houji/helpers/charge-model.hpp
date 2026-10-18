// SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include "board-thermal.hpp"
#include <deque>

namespace houji {
constexpr long long FCC=500000;
struct ChargeLimit {
    bool holding=false,attached=false,zeroed=false;
    bool step(int limit,int capacity,bool online) {
        if(limit<0 || !online)holding=false;
        else if(capacity<0){}
        else if(!attached || !holding)holding=capacity>=limit;
        else if(capacity<=limit-5)holding=false;
        attached=online;return holding;
    }
};
struct Wired {
    Hysteresis table{{34000,36000,37500,38500,39500,40500,41500,43000,44000,45000},
                     {32000,34200,36200,38000,39200,39500,41000,42000,43200,44200}};
    int update(int skin,bool screen) {
        constexpr int target[]={500,700,1100,1200,1200,1400,1400,1400,1400,1500};
        int i=table.update(skin),v=i<0?0:target[i];return screen?v/100:v%100;
    }
};
struct Wireless {
    Hysteresis table{{36800,37900,38600,39300,40000,40700,41300,41900,42500,43100,43700,44500},
                     {36000,36800,37900,38600,39300,40000,40700,41300,41900,42500,43100,43700}};
    int update(int skin,bool screen) {
        constexpr int target[]={301,402,503,604,705,806,1008,1210,1311,1412,1413,1515};
        int i=table.update(skin),v=i<0?0:target[i];return screen?v/100:v%100;
    }
};
struct Sic {
    Hysteresis table{{15000,35000,35200,38500,41300,44500,45000,46000},
                     {14000,34000,34500,37700,39000,44000,44500,45500}};
    long long update(const std::deque<long long>& history,long long current) {
        if(history.size()!=3 || current<0 || current>22000000)throw std::runtime_error("Invalid SIC input");
        for(auto t:history)if(t < -20000 || t>100000)throw std::runtime_error("Invalid SIC history");
        constexpr long long target[]={0,35000,37200,39500,43500,44500,45000,46000};
        constexpr long long maxma[]={15600,15600,13500,8000,4500,1000,500,300};
        constexpr long long minma[]={15600,15600,4600,4500,2500,1000,500,300};
        int i=table.update(history[0]);if(i<0)return 22000000;
        long long error=target[i]-history[0],previous=target[i]-history[1];
        long long delta=i<2?0:(100000*error+6500000*(error-previous))/1000;
        return std::clamp(current+delta,minma[i]*1000,maxma[i]*1000);
    }
};
struct ChargingControl {
    Wired wired;Wireless wireless;Sic sic;std::deque<long long> history;
    double last=-1,next=0;long long vote=FCC;int source=-1,wired_level=0,wireless_level=0;
    void reset(){*this=ChargingControl();}
    long long step(const Temperatures&t,int authentic,int verified,long long current,long long maximum,bool screen,double now,
                   bool wls,bool usb,bool fast) {
        if(maximum<FCC || maximum>15600000 || (wls&&fast))throw std::runtime_error("Invalid charging ceiling/state");
        int src=(wls?2:0)|(usb?1:0);
        if((source!=-1 && source!=src) || (last!=-1 && !(now-last>0 && now-last<=2.5)))reset();
        source=src;last=now;long long skin=skin_temperature(t);history.push_front(skin);if(history.size()>3)history.pop_back();
        wired_level=wired.update(skin,screen);wireless_level=wireless.update(skin,screen);
        long long battery_max=fast&&usb&&verified==1?47000:38000;
        bool ready=history.size()==3 && authentic==1 && (wls||verified==0||verified==1) &&
            t.at("battery")>=15000 && t.at("battery")<=battery_max && t.at("usb_therm")<45000;
        for(const auto &[name,v]:t){(void)name;if(v>=55000)ready=false;}
        if(!ready){vote=FCC;next=now+2;}
        else if(now>=next){
            long long target=std::min(maximum,wls?10000000LL:verified?15600000LL:5350000LL);
            if(usb)target=std::min(target,sic.update(history,current));
            vote=std::min(target,vote+250000);next=now+2;
        }
        vote=std::min(vote,maximum);return vote;
    }
};
}
