#!/usr/bin/python3
"""Preserving-update copy must retain the private files exactly and refuse overwrite."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[1]/'install/stage-update.py'
spec = importlib.util.spec_from_file_location('stage', source)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class Copy(unittest.TestCase):
    def test_throttled_copy_is_exact_and_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src, dst = root/'source', root/'destination'
            # Force multiple chunks, including a short final chunk.
            data = bytes(range(256))*8193
            src.write_bytes(data)
            with patch.object(stage.time, 'sleep'), patch.object(stage.Path, 'glob', return_value=[]):
                stage.copy_image(src, dst)
                self.assertEqual(dst.read_bytes(), data)
                with self.assertRaises(FileExistsError):
                    stage.copy_image(src, dst)
            self.assertEqual(dst.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
