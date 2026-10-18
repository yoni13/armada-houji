#!/usr/bin/env python3
"""Run native helpers against a private D-Bus and temporary process/config trees."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import runpy
import socket
import subprocess
import tempfile
import threading
import time
import unittest

import gi
gi.require_version('Gio','2.0')
from gi.repository import Gio, GLib

HERE=Path(__file__).resolve().parent
PORT=HERE.parent
ROOT=PORT.parents[1]
POWER=runpy.run_path(str(PORT/'power/houji-steam-power'))
loader=importlib.machinery.SourceFileLoader('x2s_reference',str(PORT/'runtime/usr/libexec/armada/houji-x2s-session'))
spec=importlib.util.spec_from_loader(loader.name,loader)
X2S=importlib.util.module_from_spec(spec);loader.exec_module(X2S)
X2S.SOURCE=ROOT/'system_files/usr/share/inputplumber/devices/11-gamesir-x2s.yaml'

def wait_for(predicate, timeout=6):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.03)
    raise AssertionError('Timed out waiting for helper behavior')


class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory()
        cls.bin=Path(cls.build.name)
        subprocess.run(['make','-C',str(HERE),'OUT='+str(cls.bin),'-j3'],check=True)

    @classmethod
    def tearDownClass(cls):cls.build.cleanup()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.procs=[]
        self.addCleanup(self.stop_helpers)

    def stop_helpers(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=6)
                except subprocess.TimeoutExpired:p.kill();p.wait()

    def launch(self,name,*args,env=None):
        output=(self.root/(name+'.log')).open('a')
        self.addCleanup(output.close)
        p=subprocess.Popen([str(self.bin/name),*map(str,args)],stdout=output,stderr=output,env=env)
        self.procs.append(p)
        return p

    def bus(self):
        daemon=subprocess.Popen(['dbus-daemon','--session','--nofork','--print-address=1'],stdout=subprocess.PIPE,text=True)
        address=daemon.stdout.readline().strip()
        self.connection=Gio.DBusConnection.new_for_address_sync(address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,None,None)
        self.loop=GLib.MainLoop()
        thread=threading.Thread(target=self.loop.run,daemon=True);thread.start()
        def cleanup():
            self.stop_helpers();self.loop.quit();thread.join(timeout=3)
            self.connection.close_sync(None);daemon.terminate();daemon.wait(timeout=5);daemon.stdout.close()
        self.addCleanup(cleanup)
        return address

    def own(self,name):
        self.connection.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',
            'RequestName',GLib.Variant('(su)',(name,0)),None,Gio.DBusCallFlags.NONE,3000,None)

    def drop(self,name):
        self.connection.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',
            'ReleaseName',GLib.Variant('(s)',(name,)),None,Gio.DBusCallFlags.NONE,3000,None)

    def upower(self):
        self.properties=dict(IsPresent=True,Percentage=35.0,State=1,TimeToFull=5520,TimeToEmpty=0)
        self.on_battery=False
        types={'IsPresent':'b','Percentage':'d','State':'u','TimeToFull':'x','TimeToEmpty':'x'}
        xml='<node><interface name="org.freedesktop.UPower.Device">'+''.join(
            f'<property name="{key}" type="{typ}" access="read"/>' for key,typ in types.items())+'</interface></node>'
        node=Gio.DBusNodeInfo.new_for_xml(xml)
        self.connection.register_object('/org/freedesktop/UPower/devices/DisplayDevice',node.interfaces[0],None,
            lambda _c,_s,_p,_i,key:GLib.Variant(types[key],self.properties[key]),None)
        node=Gio.DBusNodeInfo.new_for_xml('<node><interface name="org.freedesktop.UPower"><property name="OnBattery" type="b" access="read"/></interface></node>')
        self.connection.register_object('/org/freedesktop/UPower',node.interfaces[0],None,
            lambda *_:GLib.Variant('b',self.on_battery),None)
        self.own('org.freedesktop.UPower')

    def readings(self,directory):
        return {p.name:p.read_text().strip() for p in directory.iterdir() if not p.name.startswith('.')}

    def test_upower_protocol_matches_reference_in_all_states(self):
        address=self.bus();self.upower()
        directory=self.root/'vpower'
        for state in range(8):
            for battery in (False,True):
                self.properties.update(State=state,TimeToEmpty=4120 if state in (2,6) else 0)
                self.on_battery=battery
                p=self.launch('houji-steam-power','--bus-address',address,'--directory',directory,'--once')
                self.assertEqual(p.wait(timeout=6),0)
                self.assertEqual(self.readings(directory),POWER['values'](self.properties,battery))

    def test_upower_invalid_percentage_never_publishes_discovery(self):
        address=self.bus();self.upower()
        for i,change in enumerate(({'IsPresent':False},{'Percentage':float('nan')},{'Percentage':-1},{'Percentage':101})):
            self.properties.update(IsPresent=True,Percentage=35.0);self.properties.update(change)
            directory=self.root/f'vpower{i}'
            p=self.launch('houji-steam-power','--bus-address',address,'--directory',directory,'--once')
            self.assertEqual(p.wait(timeout=6),1)
            self.assertFalse((directory/'battery_percent').exists())

    def test_telemetry_loss_clears_estimates_and_recovers_without_shutdown(self):
        address=self.bus();self.upower();directory=self.root/'vpower'
        notify=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);self.addCleanup(notify.close)
        notify.bind(str(self.root/'notify'));notify.settimeout(6)
        p=self.launch('houji-steam-power','--bus-address',address,'--directory',directory,'--interval-ms','100',
            env=dict(os.environ,NOTIFY_SOCKET=str(self.root/'notify')))
        self.assertEqual(notify.recv(64),b'READY=1')
        self.assertEqual(self.readings(directory),POWER['values'](self.properties,False))
        inode=(directory/'battery_percent').stat().st_ino
        time.sleep(.3);self.assertEqual((directory/'battery_percent').stat().st_ino,inode)
        self.drop('org.freedesktop.UPower')
        wait_for(lambda:(directory/'battery_status').read_text().strip()=='Unknown')
        self.assertEqual((directory/'secs_until_shutdown_request').read_text().strip(),'-1')
        self.assertEqual((directory/'secs_until_battery_full').read_text().strip(),'-1')
        self.assertEqual((directory/'battery_percent').read_text().strip(),'35')
        self.properties.update(State=2,Percentage=34.,TimeToEmpty=3600);self.on_battery=True
        self.own('org.freedesktop.UPower')
        wait_for(lambda:self.readings(directory)==POWER['values'](self.properties,True))
        p.terminate();self.assertEqual(p.wait(timeout=6),0)

    def test_vpower_refuses_foreign_provider(self):
        directory=self.root/'vpower';directory.mkdir();(directory/'battery_percent').write_text('99\n')
        p=self.launch('houji-steam-power','--directory',directory,'--once')
        self.assertEqual(p.wait(timeout=6),1)
        self.assertEqual((directory/'battery_percent').read_text(),'99\n')

    def test_startup_with_stale_owned_snapshot_does_not_keep_old_estimates(self):
        address=self.bus();directory=self.root/'vpower';directory.mkdir()
        (directory/'.houji-upower').touch()
        (directory/'battery_percent').write_text('45\n')
        (directory/'secs_until_shutdown_request').write_text('8\n')
        (directory/'secs_until_battery_full').write_text('400\n')
        p=self.launch('houji-steam-power','--bus-address',address,'--directory',directory,'--once')
        self.assertEqual(p.wait(timeout=6),1)
        self.assertEqual((directory/'battery_percent').read_text(),'45\n')
        self.assertEqual((directory/'secs_until_shutdown_request').read_text(),'-1\n')
        self.assertEqual((directory/'secs_until_battery_full').read_text(),'-1\n')

    def proc(self,number,name,args=(),exe=True):
        directory=self.root/'proc'/str(number);directory.mkdir(parents=True)
        (directory/'cmdline').write_bytes(b'\0'.join([('/usr/bin/'+name).encode(),*args])+b'\0')
        if exe:
            binary=self.root/'bin'/name;binary.parent.mkdir(exist_ok=True);binary.touch()
            (directory/'exe').symlink_to(binary)
        return directory

    def routing(self):
        source=ROOT/'system_files/usr/share/inputplumber/devices/11-gamesir-x2s.yaml'
        output=self.root/'profiles/x2s.yaml';log=self.root/'restarts'
        restart=self.root/'systemctl'
        restart.write_text(f'#!/bin/sh\ntest "$*" = "restart inputplumber.service" || exit 1\ncat "{output}" >> "{log}"\n')
        restart.chmod(0o755)
        return source,output,log,['--proc',self.root/'proc','--uid',str(os.getuid()),'--source',source,
                                '--output',output,'--systemctl',restart,'--interval-ms','100']

    def test_x2s_exact_profile_transition_and_logout(self):
        self.proc(1,'gamescope',(b'--steam',));source,output,log,args=self.routing()
        p=self.launch('houji-x2s-session',*args)
        wait_for(lambda:log.exists())
        self.assertEqual(output.read_text(),X2S.HEADER+source.read_text())
        desktop=self.proc(2,'kwin_wayland')
        wait_for(lambda:'ignore: true' in output.read_text())
        self.assertEqual(output.read_text(),X2S.profile('desktop'))
        time.sleep(.3);self.assertEqual(log.read_text().count(X2S.HEADER),2)
        (desktop/'exe').unlink();(self.root/'proc/1/exe').unlink()
        time.sleep(.3);self.assertIn('ignore: true',output.read_text())
        self.assertEqual(log.read_text().count(X2S.HEADER),2)
        self.proc(3,'gamescope',(b'--steam',))
        wait_for(lambda:log.read_text().count(X2S.HEADER)==3)
        self.assertEqual(output.read_text(),X2S.profile('game'))
        p.terminate();self.assertEqual(p.wait(timeout=6),0)

    def test_x2s_custom_configuration_and_non_session_gamescope(self):
        self.proc(1,'gamescope');self.proc(2,'kwin_wayland',exe=False)
        _,output,log,args=self.routing()
        p=self.launch('houji-x2s-session',*args,'--once');self.assertEqual(p.wait(timeout=6),0)
        self.assertFalse(log.exists())
        self.proc(3,'gamescope',(b'--steam',));output.parent.mkdir();output.write_text('custom\n')
        p=self.launch('houji-x2s-session',*args,'--once');self.assertEqual(p.wait(timeout=6),1)
        self.assertEqual(output.read_text(),'custom\n');self.assertFalse(log.exists())

    def test_x2s_changed_profile_layout_refuses_restart(self):
        self.proc(1,'kwin_wayland');_,output,log,args=self.routing()
        source=self.root/'bad-profile';source.write_text('no expected gamepad or target anchors\n')
        args[args.index('--source')+1]=source
        p=self.launch('houji-x2s-session',*args,'--once')
        self.assertEqual(p.wait(timeout=6),1)
        self.assertFalse(output.exists());self.assertFalse(log.exists())

    def sensor(self):
        self.orientation='left-up';self.claims=[]
        xml='<node><interface name="net.hadess.SensorProxy"><method name="ClaimAccelerometer"/><method name="ReleaseAccelerometer"/><property name="AccelerometerOrientation" type="s" access="read"/></interface></node>'
        node=Gio.DBusNodeInfo.new_for_xml(xml)
        def method(_c,_s,_p,_i,name,_args,invocation):
            self.claims.append(name);invocation.return_value(None)
        self.connection.register_object('/net/hadess/SensorProxy',node.interfaces[0],method,
            lambda *_:GLib.Variant('s',self.orientation),None)
        self.own('net.hadess.SensorProxy')

    def sensor_value(self,value):
        self.orientation=value
        self.connection.emit_signal(None,'/net/hadess/SensorProxy','org.freedesktop.DBus.Properties',
            'PropertiesChanged',GLib.Variant('(sa{sv}as)',('net.hadess.SensorProxy',
                {'AccelerometerOrientation':GLib.Variant('s',value)},[])))

    def test_orientation_lock_debounce_owner_restart_and_session_replacement(self):
        address=self.bus();self.sensor()
        self.proc(1,'gamescope',(b'--force-composition-rotation',))
        runtime=self.root/'runtime';runtime.mkdir()
        sock=socket.socket(socket.AF_UNIX);self.addCleanup(sock.close)
        sock.bind(str(runtime/'gamescope-0'))
        log=self.root/'rotations';helper=self.root/'rotate'
        helper.write_text(f'#!/bin/sh\nprintf "%s\\n" "$1" >> "{log}"\n');helper.chmod(0o755)
        lock=self.root/'lock';state=self.root/'current';lock.write_text('right\n')
        p=self.launch('houji-gamescope-orientation','--bus-address',address,'--proc',self.root/'proc',
            '--runtime',runtime,'--lock',lock,'--state',state,'--helper',helper,'--interval-ms','100')
        wait_for(lambda:state.exists() and state.read_text()=='right\n')
        self.assertEqual(self.claims,[])
        lock.unlink()
        wait_for(lambda:'ClaimAccelerometer' in self.claims)
        wait_for(lambda:state.read_text()=='left\n')
        self.sensor_value('undefined');time.sleep(.9)
        self.assertEqual(state.read_text(),'left\n')
        lock.write_text('normal')
        wait_for(lambda:state.read_text()=='normal\n')
        wait_for(lambda:'ReleaseAccelerometer' in self.claims)
        self.drop('net.hadess.SensorProxy');time.sleep(.2)
        lock.unlink();time.sleep(.2)
        self.orientation='bottom-up';self.own('net.hadess.SensorProxy')
        wait_for(lambda:state.read_text()=='upsidedown\n')
        self.assertEqual(self.claims.count('ClaimAccelerometer'),2)
        # A restarted compositor using a new socket must get the same rotation again.
        before=len(log.read_text().splitlines());(self.root/'proc/1/cmdline').unlink()
        wait_for(lambda:self.claims.count('ReleaseAccelerometer')==2)
        sock.close();(runtime/'gamescope-0').unlink()
        replacement=socket.socket(socket.AF_UNIX);self.addCleanup(replacement.close)
        replacement.bind(str(runtime/'gamescope-1'))
        self.proc(2,'gamescope',(b'--force-composition-rotation',))
        wait_for(lambda:len(log.read_text().splitlines())==before+1)
        self.assertEqual(log.read_text().splitlines()[-1],'upsidedown')
        p.terminate();self.assertEqual(p.wait(timeout=6),0)
        self.assertEqual(self.claims.count('ReleaseAccelerometer'),3)

    def test_gyro_controller_gating_rates_sample_output_and_shutdown(self):
        address=self.bus()
        self.ip_properties={'Name':GLib.Variant('s','Test motion pad'),
            'SourceDevicePaths':GLib.Variant('as',[]),
            'TargetDevices':GLib.Variant('as',['/org/shadowblip/InputPlumber/devices/target/gamepad0'])}
        xml='<node><interface name="org.shadowblip.Input.CompositeDevice">'+''.join(
            f'<property name="{key}" type="{value.get_type_string()}" access="read"/>' for key,value in self.ip_properties.items())+'</interface></node>'
        node=Gio.DBusNodeInfo.new_for_xml(xml)
        self.connection.register_object('/org/shadowblip/InputPlumber/CompositeDevice0',node.interfaces[0],None,
            lambda _c,_s,_p,_i,key:self.ip_properties[key],None)
        self.own('org.shadowblip.InputPlumber')
        fake=self.root/'fake-ssc.c'
        fake.write_text(r'''
#include <glib-object.h>
#include <gio/gio.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct {GObject parent; gboolean opened; guint timer; int kind;} TestSensor;
typedef struct {GObjectClass parent;} TestSensorClass;
G_DEFINE_TYPE(TestSensor,test_sensor,G_TYPE_OBJECT)
static guint measurement;
static void test_sensor_class_init(TestSensorClass*c){measurement=g_signal_new("measurement",G_TYPE_FROM_CLASS(c),G_SIGNAL_RUN_LAST,0,NULL,NULL,NULL,G_TYPE_NONE,3,G_TYPE_FLOAT,G_TYPE_FLOAT,G_TYPE_FLOAT);}
static void test_sensor_init(TestSensor*s){(void)s;}
static void record(const char *op,int kind,float rate){FILE*f=fopen(getenv("SSC_LOG"),"a");fprintf(f,"%s %d %.0f\n",op,kind,(double)rate);fclose(f);}
static gboolean emit(gpointer ptr){TestSensor*s=ptr;if(s->opened)g_signal_emit(s,measurement,0,s->kind?0.f:1.f,0.f,s->kind?9.80665f:0.f);return G_SOURCE_CONTINUE;}
void *ssc_sensor_gyroscope_new_sync(GCancellable*c,GError**e){(void)c;(void)e;TestSensor*s=g_object_new(test_sensor_get_type(),NULL);s->kind=0;record("new",0,0);return s;}
void *ssc_sensor_accelerometer_new_sync(GCancellable*c,GError**e){(void)c;(void)e;TestSensor*s=g_object_new(test_sensor_get_type(),NULL);s->kind=1;record("new",1,0);return s;}
gboolean ssc_sensor_gyroscope_open_sync(TestSensor*s,GCancellable*c,GError**e){(void)c;(void)e;s->opened=TRUE;s->timer=g_timeout_add(10,emit,s);record("open",s->kind,0);return TRUE;}
gboolean ssc_sensor_accelerometer_open_sync(TestSensor*s,GCancellable*c,GError**e){return ssc_sensor_gyroscope_open_sync(s,c,e);}
gboolean ssc_sensor_gyroscope_close_sync(TestSensor*s,GCancellable*c,GError**e){(void)c;(void)e;s->opened=FALSE;if(s->timer){g_source_remove(s->timer);s->timer=0;}record("close",s->kind,0);return TRUE;}
gboolean ssc_sensor_accelerometer_close_sync(TestSensor*s,GCancellable*c,GError**e){return ssc_sensor_gyroscope_close_sync(s,c,e);}
void ssc_sensor_send(TestSensor*s,guint32 id,GBytes*b,GCancellable*c,GAsyncReadyCallback cb,gpointer data){(void)c;(void)cb;(void)data;gsize n;const guint8*p=g_bytes_get_data(b,&n);guint32 bits;float rate=0;if(id==513&&n==5&&p[0]==0x0d){memcpy(&bits,p+1,4);bits=GUINT32_FROM_LE(bits);memcpy(&rate,&bits,4);}record("rate",s->kind,rate);}
''')
        import shlex
        flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','gio-2.0'],text=True))
        library=self.root/'ssc.so'
        subprocess.run(['cc','-Wall','-Wextra','-Werror','-shared','-fPIC',str(fake),*flags,'-o',str(library)],check=True)
        config=self.root/'configs';config.mkdir()
        (config/'pad.yaml').write_text('name: Test motion pad\nsource_devices:\n  name: bmi-houji-imu\n')
        iio=self.root/'iio';device=iio/'iio:device9';device.mkdir(parents=True)
        (device/'name').write_text('bmi-houji-imu\n');(device/'sample').touch()
        module=self.root/'modprobe';module_log=self.root/'modules'
        module.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{module_log}"\n');module.chmod(0o755)
        setting=self.root/'enabled';setting.touch();light=self.root/'light';state=self.root/'state';rotation=self.root/'rotation';rotation.write_text('left')
        sensor_log=self.root/'ssc.log'
        p=self.launch('houji-gyro','--bus-address',address,'--ssc-library',library,'--modprobe',module,
            '--config-etc',config,'--config-share',self.root/'missing','--iio',iio,'--state',state,
            '--setting',setting,'--orientation',rotation,'--light-sleep',light,'--interval-ms','100',
            env=dict(os.environ,SSC_LOG=str(sensor_log)))
        wait_for(lambda:state.exists() and 'waiting' in state.read_text())
        self.assertFalse(sensor_log.exists())
        self.ip_properties['SourceDevicePaths']=GLib.Variant('as',['/dev/input/event6'])
        wait_for(lambda:'active' in state.read_text())
        wait_for(lambda:(device/'sample').stat().st_size>100)
        self.assertIn('rate 0 200',sensor_log.read_text());self.assertIn('rate 1 50',sensor_log.read_text())
        samples=[list(map(int,l.split())) for l in (device/'sample').read_text().splitlines() if len(l.split())==6]
        self.assertIn([0,0,16384,0,-1365333,0],samples)
        light.touch();wait_for(lambda:'waiting' in state.read_text())
        self.assertEqual(sensor_log.read_text().count('close'),2)
        light.unlink();wait_for(lambda:'active' in state.read_text())
        self.assertEqual(sensor_log.read_text().count('new'),2) # objects reused after queued callbacks
        self.ip_properties['TargetDevices']=GLib.Variant('as',['/org/shadowblip/InputPlumber/devices/target/keyboard0'])
        wait_for(lambda:'waiting' in state.read_text())
        self.assertEqual(sensor_log.read_text().count('close'),4)
        self.ip_properties['TargetDevices']=GLib.Variant('as',['/org/shadowblip/InputPlumber/devices/target/gamepad0'])
        wait_for(lambda:'active' in state.read_text())
        self.drop('org.shadowblip.InputPlumber')
        wait_for(lambda:'waiting' in state.read_text())
        self.assertEqual(sensor_log.read_text().count('close'),6)
        self.own('org.shadowblip.InputPlumber')
        wait_for(lambda:'active' in state.read_text())
        p.terminate();self.assertEqual(p.wait(timeout=6),0)
        self.assertIn('off',state.read_text());self.assertEqual(sensor_log.read_text().count('close'),8)
        self.assertEqual(module_log.read_text().splitlines()[-1],'-r houji_imu')


if __name__=='__main__':unittest.main()
