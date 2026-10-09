#!/usr/bin/env python3
"""Archived crash records get unique names and are never overwritten by the next crash."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader(
    'archive', str(ROOT / 'ports/houji/runtime/usr/libexec/armada/houji-pstore-archive'))
spec = importlib.util.spec_from_loader('archive', loader)
archive = importlib.util.module_from_spec(spec)
loader.exec_module(archive)

BOOT = 'abcdef0123456789abcdef0123456789'
CRASH_US = 1791539943_000000  # 2026-10-09T09:59:03Z


class Archive(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def names(self):
        return sorted(p.name for p in self.root.iterdir())

    def test_new_records_get_crash_time_and_boot(self):
        (self.root / 'console-ramoops-0').write_text('console')
        (self.root / 'dmesg-ramoops-0').write_text('panic')
        kept = archive.archive(self.root, info=(BOOT[:12], CRASH_US))
        self.assertEqual(kept, ['console-ramoops-0', 'dmesg-ramoops-0'])
        self.assertEqual(self.names(), ['2026-10-09T09-59-03Z-abcdef012345-console-ramoops-0',
                                        '2026-10-09T09-59-03Z-abcdef012345-dmesg-ramoops-0'])

    def test_next_crash_does_not_overwrite_and_old_ones_are_pruned(self):
        (self.root / 'console-ramoops-0').write_text('first')
        archive.archive(self.root, info=(BOOT[:12], CRASH_US))
        (self.root / 'console-ramoops-0').write_text('second')
        archive.archive(self.root, info=(BOOT[:12], CRASH_US))
        contents = sorted(p.read_text() for p in self.root.iterdir())
        self.assertEqual(contents, ['first', 'second'])
        self.assertIn('2026-10-09T09-59-03Z-abcdef012345-1-console-ramoops-0', self.names())
        for hour in range(5):
            (self.root / 'console-ramoops-0').write_text(str(hour))
            archive.archive(self.root, info=('b' * 12, CRASH_US + (hour + 1) * 3_600_000_000), keep=3)
        self.assertEqual(len(self.names()), 3)
        self.assertTrue(self.names()[0].startswith('2026-10-09T12-59-03Z'))
        # Nothing to do: an empty or already stamped directory is left alone.
        self.assertEqual(archive.archive(self.root, info=None, keep=3), [])
        self.assertEqual(len(self.names()), 3)

    def test_unknown_previous_boot_falls_back_to_now(self):
        (self.root / 'pmsg-ramoops-0').write_text('x')
        archive.archive(self.root, info=None, now=0)
        self.assertEqual(self.names(), ['1970-01-01T00-00-00Z-unknown-pmsg-ramoops-0'])

    def test_previous_boot_from_journal(self):
        def run(args, **kwargs):
            boots = [{'index': -1, 'boot_id': BOOT, 'last_entry': CRASH_US},
                     {'index': 0, 'boot_id': 'f' * 32, 'last_entry': CRASH_US + 1}]
            return subprocess.CompletedProcess(args, 0, json.dumps(boots), '')
        self.assertEqual(archive.previous_boot(run), (BOOT[:12], CRASH_US))
        for output in ['', '[]', '[{"boot_id": "x"}]', '{"a": 1}',
                       json.dumps([{'boot_id': '../etc', 'last_entry': 1}, {}])]:
            result = archive.previous_boot(lambda args, **kwargs: subprocess.CompletedProcess(args, 0, output, ''))
            self.assertIn(result, (None, (None, 1)), output)


if __name__ == '__main__':
    unittest.main()
