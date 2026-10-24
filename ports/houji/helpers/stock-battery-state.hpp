// SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include "board-thermal.hpp"

namespace houji {
// Stock xm_batt_update_work: poll capacity + XM116; USB online is refreshed
// with the display active. Poll at 8s below 15%, otherwise 10s; screen-off
// uses 20s, and connected/display-on uses 5s. Never substitute a SOC source.
struct StockBatteryState {
    double next=0;int last_blank=-1;
    void update(const fs::path &battery,const fs::path &usb,const fs::path &charger,
                bool screen_on,double now) {
        int blank=screen_on?0:1;
        auto state=charger/"fb_blank_state";
        if(fs::exists(state) && (blank!=last_blank || now>=next)) {
            // Readback also detects firmware-state loss across remote restart.
            if(integer(state)!=blank)syswrite(state,blank);
            last_blank=blank;
        }
        if(now<next)return;
        auto percent=integer(battery/"capacity");
        if(percent<0 || percent>100)throw std::runtime_error("Invalid stock capacity");
        if(fs::exists(charger/"thermal_temp"))(void)integer(charger/"thermal_temp");
        int seconds=screen_on?(percent<15?8:10):20;
        if(screen_on && integer(usb/"online")==1)seconds=5;
        next=now+seconds;
    }
};
}
