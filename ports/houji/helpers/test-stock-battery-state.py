#!/usr/bin/env python3
"""Compile the production state synchronizer against temporary sysfs files."""
from pathlib import Path
import subprocess,tempfile,unittest

HERE=Path(__file__).resolve().parent
class StateTest(unittest.TestCase):
    def test_display_readback_polling_and_legacy_kernel(self):
        source=r'''
#include "stock-battery-state.hpp"
#include <cassert>
using namespace houji;
int main(int argc,char**argv){
 (void)argc;fs::path r=argv[1],b=r/"battery",u=r/"usb",c=r/"charger";
 fs::create_directories(b);fs::create_directories(u);fs::create_directories(c);
 auto put=[](auto p,auto v){std::ofstream(p)<<v;};
 put(b/"capacity",60);put(u/"online",1);put(c/"thermal_temp",300);
 put(c/"fb_blank_state",1);StockBatteryState s;
 s.update(b,u,c,true,100);assert(integer(c/"fb_blank_state")==0);assert(s.next==105);
 s.update(b,u,c,false,101);assert(integer(c/"fb_blank_state")==1);
 // Remote state reset is corrected at the next poll.
 put(c/"fb_blank_state",0);s.update(b,u,c,false,105);
 assert(integer(c/"fb_blank_state")==1);assert(s.next==125);
 put(u/"online",0);put(b/"capacity",14);s.update(b,u,c,true,125);assert(s.next==133);
 put(b/"capacity",60);s.update(b,u,c,true,133);assert(s.next==143);
 // Invalid capacity does not advance the poll deadline.
 put(b/"capacity",101);bool failed=false;
 try{s.update(b,u,c,true,143);}catch(...){failed=true;}assert(failed&&s.next==143);
 // Older kernel lacks the new nodes; existing charging still works.
 fs::remove(c/"fb_blank_state");fs::remove(c/"thermal_temp");put(b/"capacity",40);
 s.update(b,u,c,true,144);assert(s.next==154);
}
'''
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'test.cpp').write_text(source)
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(HERE),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test'),str(p/'sysfs')],check=True)
if __name__=='__main__':unittest.main()
