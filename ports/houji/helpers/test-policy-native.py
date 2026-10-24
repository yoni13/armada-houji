#!/usr/bin/env python3
"""Differential tests for native charging/thermal policies and gyro scaling."""
import importlib.util
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
PORT=HERE.parent
sys.path.insert(0,str(PORT/'charging'))
sys.path.insert(0,str(PORT/'thermal'))
import thermal_policy
import board_thermal
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
charge=load('charge_reference',PORT/'charging/charge-policy.py')
gyro=load('gyro_reference',PORT/'sensors/gyro/houji-gyro.py')


class Policy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.bin=Path(cls.tmp.name)
        subprocess.run(['make','-C',str(HERE),'OUT='+str(cls.bin),'-j3'],check=True)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_thermal_thresholds_hysteresis_low_battery_and_random_walk(self):
        rng=random.Random(43)
        boundaries={x for table in thermal_policy.NORMAL_CPU.values() for x in table[0]}
        boundaries.update(x for table in thermal_policy.BALANCED_GAME_CPU.values() for x in table[0])
        boundaries.update((13000,15000,44000,45000,46000,48000,50000))
        temperatures=[v+d for v in sorted(boundaries) for d in (-1,0,1)]
        samples=[(t,p) for t in temperatures+temperatures[::-1] for p in (100,4,3,2,1,0,1,2,3,4)]
        samples += [(rng.randrange(12000,53000),rng.randrange(0,101)) for _ in range(5000)]
        for game,balanced in ((False,False),(True,False),(True,True)):
            policy=thermal_policy.Policy(game,balanced)
            expected=[]
            for skin,percent in samples:
                caps,gpu,paused=policy.update(skin,percent)
                expected.append([*(caps[n] or 0 for n in (0,2,5,7)),gpu,*sorted(paused)])
            result=subprocess.check_output([str(self.bin/'houji-thermal'),'--policy','balanced' if balanced else 'game' if game else 'normal'],
                input=''.join(f'{s} {p}\n' for s,p in samples),text=True)
            self.assertEqual([list(map(int,l.split())) for l in result.splitlines()],expected)

    def test_charge_model_matches_authentication_ramp_thermal_and_stale_reset(self):
        rng=random.Random(9);control=charge.StockChargingControl();rows=[];expected=[];now=0;current=500000
        # Long contiguous ramps exercise history, SIC and every hysteresis edge.
        samples=[(t,True,False,True,1,1) for t in range(20000,49000,100)]
        samples += [(t,False,False,True,1,1) for t in range(49000,20000,-100)]
        samples += [(t,True,True,False,1,0) for t in range(25000,47000,100)]
        samples += [(rng.randrange(18000,51000),bool(rng.randrange(2)),bool(rng.randrange(2)),bool(rng.randrange(2)),
                     rng.choice((-1,0,1)),rng.choice((-1,0,1))) for _ in range(2500)]
        for skin,screen,wls,usb,auth,pd in samples:
            now+=rng.choice((1.,1.,1.,1.,0.,3.,-1.)) if len(rows)>800 else 1.
            fast=not wls and rng.choice((True,False));maximum=rng.choice((500000,3000000,10000000,15600000))
            temperatures={k:skin for k in board_thermal.WEIGHTS};temperatures['usb_therm']=rng.choice((35000,44999,45000,54999,55000))
            # Make battery's valid/invalid zone independent of virtual skin.
            temperatures['battery']=rng.choice((14999,15000,30000,38000,38001,47000,47001))
            vote,_=control.step(temperatures,{'battery_authentic':auth,'pd_verified':pd},current,maximum,screen,now,wls,usb_online=usb,fast_charge=fast)
            rows.append([now,auth,pd,current,maximum,int(screen),int(wls),int(usb),int(fast),
                         *(temperatures[k] for k in ('pa_therm0','quiet_therm','charger_therm0','cpu_therm','battery','wifi_therm','usb_therm'))])
            expected.append([vote,control.wired_level,control.wireless_level]);current=vote
        output=subprocess.check_output([str(self.bin/'houji-charge-policy'),'--model'],input=''.join(' '.join(map(str,r))+'\n' for r in rows),text=True)
        self.assertEqual([list(map(int,l.split())) for l in output.splitlines()],expected)

    def test_charge_limit_hysteresis_attachment_and_missing_telemetry(self):
        rng=random.Random(4);model=charge.ChargeLimit();rows=[];expected=[]
        for _ in range(1000):
            limit=rng.choice((None,50,80,99));percent=rng.choice((None,0,45,49,50,51,74,75,79,80,81,94,95,99,100));attached=bool(rng.randrange(2))
            rows.append([limit if limit is not None else -1,percent if percent is not None else -1,int(attached)])
            expected.append(int(model.step(limit,percent,attached)))
        text=subprocess.check_output([str(self.bin/'houji-charge-policy'),'--limit-model'],input=''.join(' '.join(map(str,r))+'\n' for r in rows),text=True)
        self.assertEqual(list(map(int,text.splitlines())),expected)

    def test_gyro_units_rotation_and_rounding(self):
        rng=random.Random(12);rows=[];expected=[]
        for mode in ('normal','left','right','upsidedown','unknown'):
            for i in range(1000):
                accel=[rng.uniform(-30,30) for _ in range(3)] if i else [0,0,9.80665]
                angular=[rng.uniform(-5,5) for _ in range(3)] if i else [math.radians(1),0,0]
                rows.append([mode,*accel,*angular]);expected.append(gyro.deck_sample(accel,angular,gyro.ROTATIONS.get(mode,gyro.ROTATIONS['normal'])))
        text=subprocess.check_output([str(self.bin/'houji-gyro'),'--samples'],input=''.join(' '.join(map(str,r))+'\n' for r in rows),text=True)
        self.assertEqual(text,''.join(expected))

    def test_charger_unknown_power_and_stop_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);usb=root/'sys/class/power_supply/qcom-battmgr-usb';wls=usb.with_name('qcom-battmgr-wls');bat=usb.with_name('battery')
            usb.mkdir(parents=True);bat.mkdir()
            for online in ('0','1','2','invalid'):
                (usb/'online').write_text(online)
                result=subprocess.run([str(self.bin/'houji-charge-policy'),'--root',str(root),'--needs-light-sleep'],capture_output=True,text=True)
                expected=charge.charging_needs_light_sleep(usb,wls)
                self.assertEqual(result.returncode,0 if expected else 1)
                self.assertEqual(result.stdout.strip(),'light' if expected else 'offline')
            (usb/'online').write_text('0');wls.mkdir()
            result=subprocess.run([str(self.bin/'houji-charge-policy'),'--root',str(root),'--needs-light-sleep'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0)
            (bat/'constant_charge_current').write_text('5000000\n')
            subprocess.run([str(self.bin/'houji-charge-policy'),'--root',str(root),'--finish-auth'],check=True)
            self.assertEqual((bat/'constant_charge_current').read_text(),'500000\n')

    def test_charge_control_fixture_and_limit_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def put(path,value):
                p=root/path.lstrip('/');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(str(value)+'\n');return p
            put('/sys/module/qcom_battmgr/parameters/houji_max_fcc_ua',3000000)
            put('/etc/armada/houji-charge-ceiling',3000000)
            put('/sys/class/backlight/panel/bl_power',0)
            usb='/sys/class/power_supply/qcom-battmgr-usb/'
            for n,v in {'online':1,'usb_type':'[PD]','input_current_limit':1000000}.items():put(usb+n,v)
            bat='/sys/class/power_supply/battery/'
            for n,v in {'temp':250,'health':'Good','constant_charge_current':500000,'capacity':79}.items():put(bat+n,v)
            iio='/sys/bus/iio/devices/iio:device0/'
            put(iio+'name','spmi-adc5-gen3')
            for i,n in enumerate(('pa_therm0','quiet_therm','charger_therm0','cpu_therm','wifi_therm','usb_therm')):
                put(iio+f'in_temp{i}_label',n);put(iio+f'in_temp{i}_input',25000)
            aux='/sys/bus/auxiliary/devices/pmic_glink.power-supply.0/'
            put(aux+'houji_charger_state','battery_authentic=1\npd_verified=1\nverify_process=0')
            for n,v in {'fcc_lease_seconds':10,'fcc_max_ua':3000000,'thermal_board_temp':0,'wired_thermal_level':0}.items():put(aux+'houji_charger/'+n,v)
            (root/'run/houji-charging').mkdir(parents=True)
            p=subprocess.Popen([str(self.bin/'houji-charge-policy'),'--root',str(root),'--interval-ms','100'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                time=__import__('time');time.sleep(2.6)
                current=int((root/(bat+'constant_charge_current').lstrip('/')).read_text())
                self.assertGreaterEqual(current,750000);self.assertLessEqual(current,3000000)
                put('/etc/armada/houji-charge-limit',80);put(bat+'capacity',80);time.sleep(.3)
                self.assertEqual(int((root/(bat+'constant_charge_current').lstrip('/')).read_text()),0)
                put(bat+'capacity',78);time.sleep(.3)
                self.assertEqual(int((root/(bat+'constant_charge_current').lstrip('/')).read_text()),0)
                put(bat+'capacity',75);time.sleep(.3)
                self.assertGreater(int((root/(bat+'constant_charge_current').lstrip('/')).read_text()),0)
                # Missing thermistor resets the ramp to the conservative lease.
                (root/(iio+'in_temp0_input').lstrip('/')).unlink();time.sleep(.3)
                self.assertEqual(int((root/(bat+'constant_charge_current').lstrip('/')).read_text()),500000)
            finally:p.terminate();p.wait(timeout=5)

    def test_thermal_runtime_holds_on_bad_telemetry_and_respects_kernel_gpu_limit(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def put(path,value):
                p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(str(value)+'\n');return p
            put('proc/1/cmdline','/usr/bin/kwin_wayland\0')
            for i,n in enumerate((0,2,5,7)):
                put(f'thermal/cooling_device{i}/type',f'cpufreq-cpu{n}')
                put(f'thermal/cooling_device{i}/max_state',3)
                put(f'thermal/cooling_device{i}/cur_state',0)
                put(f'cpus/cpufreq/policy{n}/scaling_available_frequencies','3300000 2600000 1500000 900000')
            put('thermal/cooling_device4/type','devfreq-3d00000.gpu')
            gpu=put('thermal/cooling_device4/cur_state',0)
            put('devfreq/3d00000.gpu/available_frequencies','834000000 770000000 720000000 578000000')
            put('battery/temp',250);put('battery/capacity',50)
            put('iio/iio:device0/name','spmi-adc5-gen3')
            for i,n in enumerate(('pa_therm0','quiet_therm','charger_therm0','cpu_therm','wifi_therm','usb_therm')):
                put(f'iio/iio:device0/in_temp{i}_label',n);put(f'iio/iio:device0/in_temp{i}_input',25000)
            paths=['--'+n for n in ('thermal','cpus','devfreq','battery','iio','proc')]
            args=[v for option in paths for v in (option,str(root/option[2:]))]
            p=subprocess.Popen([str(self.bin/'houji-thermal'),*args,'--interval-ms','100'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                time.sleep(.5);self.assertIsNone(p.poll())
                self.assertEqual(int(gpu.read_text()),1)
                self.assertTrue(all(int((root/f'thermal/cooling_device{i}/cur_state').read_text())>0 for i in range(4)))
                # The kernel's stronger GPU cap cannot be reduced by this daemon.
                gpu.write_text('3\n');time.sleep(.3);self.assertEqual(int(gpu.read_text()),3)
                before=[(root/f'thermal/cooling_device{i}/cur_state').read_text() for i in range(4)]
                (root/'iio/iio:device0/in_temp0_input').write_text('invalid\n');time.sleep(.3)
                self.assertEqual([(root/f'thermal/cooling_device{i}/cur_state').read_text() for i in range(4)],before)
            finally:p.terminate();p.wait(timeout=5)
            self.assertEqual(int(gpu.read_text()),3)
            self.assertEqual([(root/f'thermal/cooling_device{i}/cur_state').read_text() for i in range(4)],before)
            # A replacement with missing telemetry also retains the old CPU
            # votes rather than exposing max clocks during failure/restart.
            failed=subprocess.run([str(self.bin/'houji-thermal'),*args,'--once'],capture_output=True)
            self.assertNotEqual(failed.returncode,0)
            self.assertEqual([(root/f'thermal/cooling_device{i}/cur_state').read_text() for i in range(4)],before)
            # Explicit removal can release them, without touching a stronger
            # kernel-owned GPU cooling vote.
            (root/'iio/iio:device0/in_temp0_input').write_text('25000\n')
            subprocess.run([str(self.bin/'houji-thermal'),*args,'--once','--exit-caps','release'],check=True,stdout=subprocess.DEVNULL)
            self.assertEqual([int((root/f'thermal/cooling_device{i}/cur_state').read_text()) for i in range(4)],[0]*4)
            self.assertEqual(int(gpu.read_text()),3)

    def test_balanced_runtime_selection_and_resume_reapplies_cooling_votes(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def put(path,value):
                p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(str(value)+'\n');return p
            put('proc/1/cmdline','/usr/bin/gamescope\0')
            freqs={0:(2265600,2035200,1689600,1459200,1344000,1017600),
                   2:(3148800,2630400,1824000,1612800,1286400,960000),
                   5:(2956800,2630400,1612800,1497600,1286400,960000),
                   7:(3052800,2630400,1939200,1708800,1248000,1132800,1017600)}
            controls=[]
            for i,(n,fs) in enumerate(freqs.items()):
                put(f'thermal/cooling_device{i}/type',f'cpufreq-cpu{n}')
                put(f'thermal/cooling_device{i}/max_state',len(fs)-1)
                controls.append(put(f'thermal/cooling_device{i}/cur_state',0))
                put(f'cpus/cpufreq/policy{n}/scaling_available_frequencies',' '.join(map(str,fs)))
            put('thermal/cooling_device4/type','devfreq-3d00000.gpu')
            gpu=put('thermal/cooling_device4/cur_state',0)
            put('devfreq/3d00000.gpu/available_frequencies','834000000 770000000 720000000 680000000 629000000 578000000 500000000')
            put('battery/temp',460);put('battery/capacity',80)
            put('iio/iio:device0/name','spmi-adc5-gen3')
            for i,n in enumerate(('pa_therm0','quiet_therm','charger_therm0','cpu_therm','wifi_therm','usb_therm')):
                put(f'iio/iio:device0/in_temp{i}_label',n);put(f'iio/iio:device0/in_temp{i}_input',46000)
            # Equal 46C inputs produce 44.559C virtual skin, a warm CPU-bound case.
            args=[v for n in ('thermal','cpus','devfreq','battery','iio','proc') for v in ('--'+n,str(root/n))]
            p=subprocess.Popen([str(self.bin/'houji-thermal'),*args,'--game-policy','balanced','--interval-ms','100'],stdout=subprocess.DEVNULL)
            try:
                time.sleep(.5);self.assertIsNone(p.poll())
                expected=[3,2,2,2]
                self.assertEqual([int(c.read_text()) for c in controls],expected)
                self.assertEqual(int(gpu.read_text()),4)
                # Kernel cooling controls can reset on resume. Reapply policy.
                for c in controls+[gpu]:c.write_text('0\n')
                time.sleep(.3)
                self.assertEqual([int(c.read_text()) for c in controls],expected)
                self.assertEqual(int(gpu.read_text()),4)
            finally:p.terminate();p.wait(timeout=5)
            self.assertEqual([int(c.read_text()) for c in controls],expected)


if __name__=='__main__':unittest.main()
