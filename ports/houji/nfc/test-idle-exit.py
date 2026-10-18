#!/usr/bin/env python3
"""Only a fully off and quiescent NFC manager may release its interpreter."""
import importlib.util
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import Mock,patch

gi=types.ModuleType('gi');repo=types.ModuleType('gi.repository')
repo.Gio=types.SimpleNamespace();repo.GLib=types.SimpleNamespace()
sys.modules['gi']=gi;sys.modules['gi.repository']=repo
spec=importlib.util.spec_from_file_location('nfc_service_idle',Path(__file__).with_name('service.py'))
service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)

class Idle(unittest.TestCase):
    def sample(self):
        s=service.Service.__new__(service.Service)
        s.closing=False;s.worker=s.poll_worker=None;s.emulation=s.mode_pending=s.settings_error=False
        s.card_request=None;s.settings=service.DEFAULTS.copy();s.state={'mode':'off','busy':False,'powered':False,'polling':False}
        s.lock=threading.Lock();s.last_use=0;s.shutdown=Mock()
        return s
    def test_off_exits_but_status_queries_do_not_extend_idle_lifetime(self):
        s=self.sample()
        with patch.object(service.time,'monotonic',return_value=31),patch.object(service.controller,'RECOVERY') as recovery:
            recovery.exists.return_value=False
            self.assertFalse(s.idle_exit());s.shutdown.assert_called_once()
    def test_active_modes_recovery_and_inflight_work_never_exit(self):
        changes=[('emulation',True),('mode_pending',True),('card_request',object()),('settings_error',True),
                 ('worker',Mock(is_alive=lambda:True)),('poll_worker',Mock(is_alive=lambda:True))]
        for key,value in changes:
            s=self.sample();setattr(s,key,value)
            with self.subTest(key=key),patch.object(service.time,'monotonic',return_value=60),patch.object(service.controller,'RECOVERY') as recovery:
                recovery.exists.return_value=False
                self.assertTrue(s.idle_exit());s.shutdown.assert_not_called()
        for mode in ('reader_enabled','emulation_enabled','ipass_enabled'):
            s=self.sample();s.settings[mode]=True
            with patch.object(service.controller,'RECOVERY') as recovery:
                recovery.exists.return_value=False
                self.assertTrue(s.idle_exit());s.shutdown.assert_not_called()
        s=self.sample()
        with patch.object(service.controller,'RECOVERY') as recovery:
            recovery.exists.return_value=True
            self.assertTrue(s.idle_exit());s.shutdown.assert_not_called()
    def test_recent_action_and_error_state_are_retained(self):
        s=self.sample();s.last_use=20
        with patch.object(service.time,'monotonic',return_value=40),patch.object(service.controller,'RECOVERY') as recovery:
            recovery.exists.return_value=False
            self.assertTrue(s.idle_exit());s.shutdown.assert_not_called()
            s.last_use=0;s.state['mode']='error'
            self.assertTrue(s.idle_exit());s.shutdown.assert_not_called()

if __name__=='__main__':unittest.main()
