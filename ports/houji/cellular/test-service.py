#!/usr/bin/python3
"""Validate the privileged API without opening D-Bus or a modem."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest

# GI is only needed by the daemon; pure request validation runs on build hosts.
gi = types.ModuleType('gi')
repository = types.ModuleType('gi.repository')
repository.Gio = types.SimpleNamespace()
repository.GLib = types.SimpleNamespace()
sys.modules['gi'] = gi
sys.modules['gi.repository'] = repository
spec = importlib.util.spec_from_file_location('service', Path(__file__).with_name('service.py'))
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class Requests(unittest.TestCase):
    def test_allowed_operations(self):
        for mode in ('auto', 'physical1', 'physical2', 'esim'):
            service.validate('select', mode)
        service.validate('download', 'LPA:1$test.invalid$SYNTHETIC-TOKEN')
        service.validate('profile-enable', '0'*20)

    def test_untrusted_dbus_inputs(self):
        for operation, value in (
                ('shell','true'), ('select','esim;reboot'), ('select','../physical1'),
                ('download','LPA:1$test.invalid$X\nY'), ('download','LPA:1$'+'x'*4096),
                ('profile-delete','--help'), ('profile-enable','0'*17),
                ('enable','unexpected'), ('list','unexpected')):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                service.validate(operation,value)


if __name__ == '__main__':
    unittest.main()
