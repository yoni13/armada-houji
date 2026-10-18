#!/usr/bin/python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ledger', Path(__file__).with_name('sleep-state.py'))
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


class Ledger(unittest.TestCase):
    def test_rotation_preserves_sequence_despite_clock_reversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for i in range(7):
                ledger.append(root, {'sequence': i, 'wall': 100 - i}, maximum=1)
            files = [root / f'events.{i}.jsonl' for i in (3, 2, 1)] + [root / 'events.jsonl']
            self.assertEqual([json.loads(p.read_text())['sequence'] for p in files], [3, 4, 5, 6])
            self.assertEqual(len(list(root.glob('events*.jsonl'))), 4)
            self.assertEqual((root / 'events.jsonl').stat().st_mode & 0o777, 0o600)

    def test_disabled_diagnostics_never_collect_or_write(self):
        with patch.object(ledger.ENABLED.__class__, 'exists', return_value=False), \
                patch.object(ledger, 'append') as append:
            ledger.record('kernel-entry')
            append.assert_not_called()

    def test_disk_failure_does_not_abort_sleep(self):
        with patch.object(ledger.ENABLED.__class__, 'exists', return_value=True), \
                patch.object(ledger, 'append', side_effect=OSError('full')), \
                patch('sys.stderr'):
            ledger.record('kernel-entry')


if __name__ == '__main__':
    unittest.main()
