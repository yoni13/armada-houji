#!/usr/bin/python3
"""Exercise NFC idle exit on a private D-Bus without opening a controller."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from gi.repository import Gio, GLib
spec=importlib.util.spec_from_file_location('nfc_idle_integration',Path(__file__).with_name('service.py'))
service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)

class Activation(unittest.TestCase):
    def test_dbus_mode_actions_prevent_idle_exit_until_off(self):
        daemon=subprocess.Popen(['dbus-daemon','--session','--nofork','--print-address=1'],stdout=subprocess.PIPE,text=True)
        address=daemon.stdout.readline().strip()
        flags=Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        server=Gio.DBusConnection.new_for_address_sync(address,flags,None,None)
        client=Gio.DBusConnection.new_for_address_sync(address,flags,None,None)
        loop=GLib.MainLoop();thread=threading.Thread(target=loop.run,daemon=True);thread.start()
        self.addCleanup(lambda:(loop.quit(),thread.join(timeout=3),client.close_sync(None),server.close_sync(None),daemon.terminate(),daemon.wait(timeout=3),daemon.stdout.close()))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(service,'SETTINGS',root/'settings.json'),patch.object(service.controller,'RECOVERY',root/'recovery'),\
                 patch.object(service.Service,'authorized',return_value=True),patch.object(service.Service,'poll',return_value=True),\
                 patch.object(service.Service,'start_desired_mode',lambda self:self.state.update(mode=service.desired_mode(self.settings),busy=False)),\
                 patch.object(service.Gio,'bus_get_sync',return_value=server):
                s=service.Service();s.restore()
                def call(method,args=None):
                    return client.call_sync(service.NAME,service.PATH,service.IFACE,method,args,None,Gio.DBusCallFlags.NONE,3000,None)
                # Explicit ownership makes the test independent of idle callbacks.
                server.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','RequestName',GLib.Variant('(su)',(service.NAME,0)),None,Gio.DBusCallFlags.NONE,3000,None)
                value=json.loads(call('GetStatus').unpack()[0]);self.assertEqual(value['desired_mode'],'off')
                s.last_use=0
                with patch.object(s,'shutdown') as shutdown:
                    self.assertFalse(s.idle_exit());shutdown.assert_called_once()
                call('SetMode',GLib.Variant('(s)',('ipass',)))
                self.assertTrue(s.idle_exit())
                self.assertTrue(json.loads((root/'settings.json').read_text())['ipass_enabled'])
                call('SetMode',GLib.Variant('(s)',('off',)))
                self.assertFalse(json.loads((root/'settings.json').read_text())['ipass_enabled'])
                s.last_use=0
                with patch.object(s,'shutdown') as shutdown:
                    self.assertFalse(s.idle_exit());shutdown.assert_called_once()
                Gio.bus_unown_name(s.name_id)

if __name__=='__main__':unittest.main()
