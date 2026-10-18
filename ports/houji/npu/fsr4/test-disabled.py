#!/usr/bin/env python3
"""The parked experiment must not start GPU/NPU work from old launch options."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('plugin',ROOT/'decky-plugin/main.py')
plugin=importlib.util.module_from_spec(spec);spec.loader.exec_module(plugin)


class Disabled(unittest.TestCase):
    def test_old_flags_do_not_enable_plugin(self):
        p=plugin.Plugin()
        with patch.dict(os.environ,{'FSR4_NPU_LIVE':'1','FSR4_NPU_DIRECT':'1'},clear=True), \
                patch.object(p,'_get_status',return_value={'available':False}), \
                patch.object(p,'_ensure_system_heap') as heap, patch.object(plugin.subprocess,'run') as run:
            p._set_enabled(True);p._restart_service()
            heap.assert_not_called();run.assert_not_called()

    def test_explicit_opt_in_keeps_existing_start_path(self):
        p=plugin.Plugin()
        with patch.dict(os.environ,{'HOUJI_FSR4_EXPERIMENTAL':'1'},clear=True), \
                patch.object(p,'_get_status',return_value={}), \
                patch.object(p,'_ensure_system_heap'), patch.object(plugin.subprocess,'run') as run:
            p._set_enabled(True)
            self.assertIn('start',run.call_args.args[0])

    def test_launcher_exits_before_systemctl(self):
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'systemctl'
            script.write_text('#!/bin/sh\ntouch "'+directory+'/called"\n');script.chmod(0o755)
            env=dict(os.environ,PATH=directory+':'+os.environ['PATH'],HOUJI_FSR4_EXPERIMENTAL='0')
            result=subprocess.run(['sh',str(ROOT/'start-frame-server.sh'),'missing'],env=env,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((Path(directory)/'called').exists())

    def test_native_gate_requires_exact_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'gate.c';binary=Path(directory)/'gate'
            source.write_text('#include "experimental-gate.h"\nint main(void){return houji_fsr4_experimental_enabled()?0:1;}\n')
            subprocess.run(['cc','-Wall','-Werror','-I',str(ROOT),str(source),'-o',str(binary)],check=True)
            for value in ('','0','true','1'):
                result=subprocess.run([str(binary)],env=dict(os.environ,HOUJI_FSR4_EXPERIMENTAL=value))
                self.assertEqual(result.returncode,0 if value=='1' else 1)


if __name__=='__main__':unittest.main()
