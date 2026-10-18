#!/usr/bin/env python3
"""Private-bus cellular broker tests: fake Polkit and command binaries only."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import unittest

from gi.repository import Gio, GLib

HERE=Path(__file__).resolve().parent
NAME='org.armada.Cellular'
PATH='/org/armada/Cellular'
IFACE=NAME+'1'

def wait_for(check,timeout=8):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if check():return
        time.sleep(.03)
    raise AssertionError('Timed out waiting for broker')


class Broker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory();cls.bin=Path(cls.build.name)/'houji-cellular-control'
        subprocess.run(['make','-C',str(HERE),'OUT='+cls.build.name,str(cls.bin)],check=True)
    @classmethod
    def tearDownClass(cls):cls.build.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.log=self.root/'commands';self.selection=self.root/'selection';self.lock=self.root/'control.lock'
        self.allow=True;self.checks=[];self.process=None
        self.daemon=subprocess.Popen(['dbus-daemon','--session','--nofork','--print-address=1'],stdout=subprocess.PIPE,text=True)
        address=self.daemon.stdout.readline().strip()
        flags=Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        self.server=Gio.DBusConnection.new_for_address_sync(address,flags,None,None)
        self.client=Gio.DBusConnection.new_for_address_sync(address,flags,None,None)
        self.loop=GLib.MainLoop();self.thread=threading.Thread(target=self.loop.run,daemon=True);self.thread.start()
        self.addCleanup(self.cleanup)
        self.server.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','RequestName',
            GLib.Variant('(su)',('org.freedesktop.PolicyKit1',0)),None,Gio.DBusCallFlags.NONE,3000,None)
        node=Gio.DBusNodeInfo.new_for_xml('''<node><interface name="org.freedesktop.PolicyKit1.Authority">
        <method name="CheckAuthorization"><arg type="(sa{sv})" direction="in"/><arg type="s" direction="in"/>
        <arg type="a{ss}" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
        <arg type="(bba{ss})" direction="out"/></method></interface></node>''')
        def authorize(_c,_s,_p,_i,_m,args,inv):
            self.checks.append(args.unpack())
            inv.return_value(GLib.Variant('((bba{ss}))',((self.allow,False,{}),)))
        self.server.register_object('/org/freedesktop/PolicyKit1/Authority',node.interfaces[0],authorize,None,None)
        script=self.root/'command'
        script.write_text('''#!/usr/bin/python3
import json,os,sys,time
from pathlib import Path
r=Path(os.environ['FAKE_ROOT']);name=Path(sys.argv[0]).name;args=sys.argv[1:]
value=sys.stdin.read()
with (r/'commands').open('a') as f:f.write(json.dumps({'name':name,'args':args,'input':value,'env':{k:v for k,v in os.environ.items() if k.startswith(('LPAC_','LIBEUICC_'))}})+'\\n')
failure=(r/'failure').read_text() if (r/'failure').exists() else ''
if name=='sim.py' and args==['esim','--save']:(r/'selection').write_text('esim\\n')
if name=='sim.py' and args[-1:] == ['--save']:(r/'selection').write_text(args[0]+'\\n')
if name=='lpac' and args[:2]==['profile','download']:
    if (r/'slow').exists():time.sleep(4)
    if failure=='download':print('PRIVATE-ACTIVATION-CODE',file=sys.stderr);sys.exit(1)
if name=='lpac' and args[:1]==['notification'] and failure=='notification':sys.exit(1)
if name=='sim.py' and failure=='sim':print('PRIVATE-ICCID',file=sys.stderr);sys.exit(1)
if name=='lpac' and args==['profile','list']:
    if failure=='list':print('not json');sys.exit(0)
    print(json.dumps({'type':'lpa','payload':{'code':0,'data':[{'iccid':'0'*20,'profileName':'Carrier','profileNickname':'Travel','serviceProviderName':'Example','profileState':'enabled','privateExtra':'must not return'}]}}))
''')
        script.chmod(0o755)
        for n in ('systemctl','sim.py','lpac'):(self.root/n).symlink_to(script)
        self.output=(self.root/'broker.log').open('w');self.addCleanup(self.output.close)
        self.args=[str(self.bin),'--bus-address',address,'--root',str(self.root),'--systemctl',str(self.root/'systemctl'),
                   '--selection',str(self.selection),'--lock',str(self.lock),'--command-timeout','1']
        self.env=dict(os.environ,FAKE_ROOT=str(self.root),LPAC_APDU='bad',LIBEUICC_SECRET='bad',LPAC_OLD='bad')
        self.start()
    def start(self):
        self.process=subprocess.Popen(self.args,stdout=self.output,stderr=self.output,env=self.env)
        def owner():
            return self.client.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','NameHasOwner',
                GLib.Variant('(s)',(NAME,)),None,Gio.DBusCallFlags.NONE,1000,None).unpack()[0]
        wait_for(owner)
    def cleanup(self):
        if self.process and self.process.poll() is None:self.process.terminate();self.process.wait(timeout=6)
        self.loop.quit();self.thread.join(timeout=3)
        self.client.close_sync(None);self.server.close_sync(None)
        self.daemon.terminate();self.daemon.wait(timeout=3);self.daemon.stdout.close()
    def call(self,method,args=None):
        return self.client.call_sync(NAME,PATH,IFACE,method,args,None,Gio.DBusCallFlags.NO_AUTO_START,3000,None)
    def status(self):return json.loads(self.call('Status').unpack()[0])
    def run_op(self,op,value=''):
        self.call('Run',GLib.Variant('(ss)',(op,value)))
        wait_for(lambda:not self.status()['busy'])
        return self.status()
    def commands(self):return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []
    def test_authorization_covers_status_and_mutations(self):
        self.allow=False
        for method,args in [('Status',None),('Run',GLib.Variant('(ss)',('enable','')))]:
            with self.assertRaises(GLib.Error) as error:self.call(method,args)
            self.assertIn('NotAuthorized',str(error.exception))
        self.assertEqual(self.commands(),[])
        self.assertTrue(all(c[1]=='org.armada.cellular.manage' and c[0][0]=='system-bus-name' and c[3]==0 for c in self.checks))
    def test_selection_and_enable_preserve_command_order(self):
        result=self.run_op('select','physical1')
        self.assertEqual(result['selection'],'physical1');self.assertEqual(result['message'],'Done')
        self.assertEqual([(x['name'],x['args']) for x in self.commands()],[('systemctl',['is-active','--quiet','houji-cellular.service']),
            ('systemctl',['stop','ModemManager']),('sim.py',['physical1','--save']),('systemctl',['start','ModemManager'])])
        self.log.unlink();self.run_op('enable')
        self.assertEqual([x['args'] for x in self.commands()],[['stop','ModemManager'],['enable','--now','houji-cellular.service'],['start','ModemManager']])
    def test_download_stdin_clean_environment_and_filtered_profiles(self):
        code='LPA:1$test.invalid$SYNTHETIC-TOKEN'
        result=self.run_op('download',code);calls=self.commands()
        lpac=[x for x in calls if x['name']=='lpac']
        self.assertEqual(lpac[0]['args'],['profile','download','-a','-']);self.assertEqual(lpac[0]['input'],code+'\n')
        self.assertNotIn(code,str([x['args'] for x in calls]))
        self.assertEqual(lpac[0]['env'],{'LPAC_APDU':'qmi_qrtr','LPAC_APDU_QMI_UIM_SLOT':'2','LPAC_CUSTOM_ES10X_MSS':'255','LPAC_HTTP':'curl'})
        self.assertEqual(set(result['profiles'][0]),{'iccid','profileName','profileNickname','serviceProviderName','profileState'})
        self.assertNotIn('SYNTHETIC-TOKEN',(self.root/'broker.log').read_text())
    def test_profile_update_notification_failure_and_nickname(self):
        (self.root/'failure').write_text('notification')
        result=self.run_op('profile-enable','0'*20)
        self.assertIn('notification is pending',result['message'])
        self.assertIn(['refresh'],[x['args'] for x in self.commands() if x['name']=='sim.py'])
        self.log.unlink();(self.root/'failure').unlink()
        result=self.run_op('profile-nickname','0'*20+':Travel 中文')
        self.assertEqual(result['message'],'Done')
        self.assertIn(['profile','nickname','0'*20,'Travel 中文'],[x['args'] for x in self.commands()])
        self.assertFalse(any('refresh' in x['args'] or 'notification' in x['args'] for x in self.commands()))
    def test_failure_timeout_and_shutdown_restore_modemmanager(self):
        for failure in ('sim','list','download','timeout'):
            with self.subTest(failure=failure):
                if self.log.exists():self.log.unlink()
                (self.root/'failure').write_text(failure)
                if failure=='timeout':(self.root/'slow').touch()
                result=self.run_op('download','LPA:1$test.invalid$SYNTHETIC-TOKEN')
                self.assertIn('Operation failed',result['message'])
                self.assertEqual(self.commands()[-1]['args'],['start','ModemManager'])
                self.assertNotIn('PRIVATE-',(self.root/'broker.log').read_text())
                (self.root/'slow').unlink(missing_ok=True)
        (self.root/'failure').unlink();(self.root/'slow').touch();self.log.unlink()
        self.call('Run',GLib.Variant('(ss)',('download','LPA:1$test.invalid$SYNTHETIC-TOKEN')))
        wait_for(lambda:any(x['name']=='lpac' for x in self.commands()))
        self.process.terminate();self.assertEqual(self.process.wait(timeout=6),0)
        self.assertEqual(self.commands()[-1]['args'],['start','ModemManager'])
    def test_busy_and_interprocess_lock_prevent_overlap(self):
        with self.lock.open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            self.call('Run',GLib.Variant('(ss)',('select','physical2')))
            self.assertTrue(self.status()['busy'])
            with self.assertRaises(GLib.Error):self.call('Run',GLib.Variant('(ss)',('enable','')))
            time.sleep(.1);self.assertEqual(self.commands(),[])
            fcntl.flock(lock,fcntl.LOCK_UN)
            wait_for(lambda:not self.status()['busy'])
        self.assertEqual(self.status()['selection'],'physical2')
    def test_validation_rejects_unsafe_inputs_before_commands(self):
        for op,value in [('shell','true'),('select','esim;reboot'),('download','LPA:1$abc$X\nY'),
                         ('profile-delete','--help'),('profile-nickname','0'*20+': padded'),
                         ('profile-nickname','0'*20+':a\nb'),('profile-nickname','0'*20+':'+'é'*33),('enable','extra')]:
            with self.subTest(op=op,value=value),self.assertRaises(GLib.Error):self.call('Run',GLib.Variant('(ss)',(op,value)))
        self.assertEqual(self.commands(),[])
        self.assertEqual(self.run_op('profile-nickname','0'*20+':')['message'],'Done')


if __name__=='__main__':unittest.main()
