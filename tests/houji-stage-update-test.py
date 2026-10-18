#!/usr/bin/env python3
"""Preserving updates reuse the actual writable modem stores across generations."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('stage',Path(__file__).resolve().parents[1]/'ports/houji/install/stage-update.py')
stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)


class Backing(unittest.TestCase):
    def test_original_overlay_and_later_bind_resolve_to_same_store(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory);upper=data/'armada/old/upper';store=upper/'var/lib/houji-modem/efs'
            store.mkdir(parents=True);(store/'modem_fs1').write_text('private')
            path=Path('/var/lib/houji-modem/efs')
            def overlay(args,**kwargs):return '/' if args[2]=='FSROOT' else f'rw,upperdir={upper},workdir={data}/work'
            def bind(args,**kwargs):return '/'+str(store.relative_to(data)) if args[2]=='FSROOT' else 'rw,noatime'
            self.assertEqual(stage.private_backing(path,data,overlay),store)
            self.assertEqual(stage.private_backing(path,data,bind),store)
            self.assertEqual((store/'modem_fs1').read_text(),'private')

    def test_missing_or_escaping_backing_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)/'data';data.mkdir()
            for root in ('/missing','/../outside'):
                def run(args,**kwargs):return root if args[2]=='FSROOT' else 'rw'
                with self.subTest(root=root),self.assertRaises(ValueError):
                    stage.private_backing(Path('/var/lib/houji-modem/efs'),data,run)


if __name__=='__main__':unittest.main()
