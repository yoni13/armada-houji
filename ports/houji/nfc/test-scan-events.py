#!/usr/bin/python3
"""NFC scan counter: object arrivals count once, emulation and polls do not."""
import importlib.util
import json
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

gi = types.ModuleType('gi')
repo = types.ModuleType('gi.repository')
repo.Gio = types.SimpleNamespace()
repo.GLib = types.SimpleNamespace()
sys.modules['gi'] = gi
sys.modules['gi.repository'] = repo
spec = importlib.util.spec_from_file_location('nfc_service', Path(__file__).with_name('service.py'))
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class Detections(unittest.TestCase):
    def test_new_objects_count_once_and_never_expose_paths(self):
        s = service.Service.__new__(service.Service)
        s.lock = threading.Lock()
        s.state = dict(mode='reader', busy=False, detections=0)
        s.settings = dict(reader_enabled=True, emulation_enabled=False)
        s.seen_tags = set()
        def scan(paths):
            with patch.object(service.controller, 'reader_status', return_value={
                    'powered': True, 'polling': False, 'tags': len(paths), 'tag_paths': paths}):
                s.refresh()
        path = '/org/neard/nfc0/tag0'
        scan([path]); scan([path])
        self.assertEqual(s.state['detections'], 1)
        scan([]); scan([path])
        self.assertEqual(s.state['detections'], 2)
        self.assertNotIn(path, json.dumps(s.state))
        s.state['busy'] = True
        scan(['/org/neard/nfc0/tag1'])
        self.assertEqual(s.state['detections'], 2)
        s.state['busy'] = False
        s.settings['emulation_enabled'] = True
        scan(['/org/neard/nfc0/tag1'])
        self.assertEqual(s.state['detections'], 2)
        self.assertFalse(s.seen_tags)


if __name__ == '__main__':
    unittest.main()
