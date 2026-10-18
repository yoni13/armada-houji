#!/usr/bin/env python3
"""Synthetic identity/lifetime tests; no live kernel accesses."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('trace', Path(__file__).with_name('gpu-lock-trace.py'))
trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)


class Correlation(unittest.TestCase):
    def emit(self, tracker, tid, when, event, **fields):
        values = ' '.join(f'{k}={v}' for k, v in fields.items())
        tracker.feed(f'task-{tid} [001] d.... {when:.6f}: {event}: {values}')

    def setup_chain(self, tracker, owner=0x1000, worker=10, task=20):
        self.emit(tracker, task, 1, 'alloc', obj=0xAA, resv=0xBB, owner_raw=owner)
        self.emit(tracker, worker, 2, 'retire', submit=0xCC)
        self.emit(tracker, worker, 2.1, 'lock', lock=0xBB, owner_raw=owner | 1, owner_pid1=task)
        self.emit(tracker, task, 2.2, 'evict', obj=0xDD, resv=0xEE, owner_raw=owner)
        self.emit(tracker, task, 2.25, 'resv_wait', resv=0xEE, owner_raw=owner, timeout=10)
        self.emit(tracker, task, 2.3, 'wait', fence=0xFF)

    def test_matches_owner_pointer_tid_lock_and_hardware_completion(self):
        state = trace.Dependency()
        self.setup_chain(state)
        self.emit(state, 20, 2.31, 'wait_detail', fence=0xFF, hw_seqno=100, hardware=101, flags=0)
        self.assertEqual(len(state.candidates), 1)
        candidate = state.candidates[0]
        self.assertEqual(candidate['owner_tid'], 20)
        self.assertTrue(candidate['hardware_complete'])
        self.assertFalse(candidate['user_fence_signaled'])
        self.assertEqual(candidate['lock_matches'], 'allocation')
        self.assertTrue(state.pending_worker(10, 5))
        self.emit(state, 10, 5.1, 'lock_end', lock=0xBB)
        self.assertFalse(state.pending_worker(10, 6))

    def test_wrong_owner_and_stale_lock_do_not_match(self):
        for mode in ('pointer', 'pid', 'released', 'retired', 'reservation'):
            state = trace.Dependency()
            self.setup_chain(state)
            if mode == 'pointer': state.locks[10]['owner_raw'] = 0x2001
            if mode == 'pid': state.locks[10]['owner_tid'] = 21
            if mode == 'released': self.emit(state, 10, 2.25, 'lock_end', lock=0xBB)
            if mode == 'retired': self.emit(state, 10, 2.25, 'retire_end', submit=0xCC)
            if mode == 'reservation': state.reservations[20]['resv'] = 0xAB
            self.emit(state, 20, 2.31, 'wait_detail', fence=0xFF, hw_seqno=100, hardware=101, flags=0)
            self.assertFalse(state.candidates, mode)

    def test_hardware_pending_is_not_reported_as_complete(self):
        state = trace.Dependency()
        self.setup_chain(state)
        self.emit(state, 20, 2.31, 'wait_detail', fence=0xFF, hw_seqno=102, hardware=101, flags=0)
        self.assertFalse(state.candidates[0]['hardware_complete'])
        self.assertTrue(trace.complete32(2, 0xFFFFFFFE))
        self.assertFalse(trace.complete32(0xFFFFFFFE, 2))

    def test_end_identity_does_not_remove_a_different_lock(self):
        state = trace.Dependency()
        self.setup_chain(state)
        self.emit(state, 10, 3, 'lock_end', lock=0xAB)
        self.assertIn(10, state.locks)

    def test_detail_can_arrive_before_generic_wait_probe(self):
        state = trace.Dependency()
        self.setup_chain(state)
        state.waits.clear()
        self.emit(state, 20, 2.31, 'wait_detail', fence=0xFF, hw_seqno=100, hardware=101, flags=0)
        self.emit(state, 20, 2.310001, 'wait', fence=0xFF)
        self.assertEqual(state.waits[20]['hardware'], 101)
        self.assertEqual(len(state.candidates), 1)


class Storage(unittest.TestCase):
    def test_continuous_mode_outlives_both_one_shot_limits(self):
        self.assertEqual(trace.limit_reason(False, 21600, 21600, 0), 'time-limit')
        self.assertEqual(trace.limit_reason(False, 10, 21600, 8 << 30), 'total-write-limit')
        self.assertIsNone(trace.limit_reason(True, 365 * 86400, 21600, 1 << 50))

    def test_continuous_retention_includes_reset_runs_and_preserves_foreign_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = []
            for i in range(10):
                run = root / f'20261017T0100{i:02d}.000001'
                run.mkdir()
                (run / 'start.json').write_text(json.dumps({'collector': trace.GROUP, 'continuous': True}))
                (run / 'trace-000001.txt').write_bytes(b'evidence')
                # Half the runs have no end.json, as after an unexpected reset.
                if i % 2:
                    (run / 'end.json').write_text('{}')
                os.utime(run, ns=(i + 1, i + 1))
                runs.append(run)
            foreign = root / '20261017T000000.000001'
            foreign.mkdir()
            (foreign / 'start.json').write_text(json.dumps({'collector': trace.GROUP, 'continuous': True}))
            (foreign / 'owner-notes.txt').write_text('keep')
            old = root / '20261016T143030'
            old.mkdir()
            (old / 'start.json').write_text('{}')
            outside = root / 'outside'
            outside.mkdir()
            (outside / 'evidence').write_text('keep')
            (root / '20261016T000000.000001').symlink_to(outside, target_is_directory=True)
            trace.prune_runs(root, runs[-1])
            self.assertEqual([p.exists() for p in runs], [False] * 2 + [True] * 8)
            self.assertEqual((foreign / 'owner-notes.txt').read_text(), 'keep')
            self.assertTrue(old.exists())
            self.assertEqual((outside / 'evidence').read_text(), 'keep')

    def test_rolling_storage_preserves_order_and_caps_files(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            writer = trace.Rolling(out, size=8, count=2)
            for value in (b'first\n', b'second\n', b'third\n'):
                writer.write(value)
            writer.close()
            files = sorted(out.glob('trace-*.txt'))
            self.assertEqual(len(files), 2)
            self.assertEqual(b''.join(p.read_bytes() for p in files), b'second\nthird\n')

    def test_partial_setup_cleanup_removes_only_owned_probes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collector = trace.Trace(root)
            collector.instance.mkdir(parents=True)
            (collector.instance/'events').mkdir()
            (collector.instance/'events/enable').write_text('1')
            (collector.instance/'tracing_on').write_text('1')
            collector.owned = True
            collector.installed = ['one', 'two']
            commands = []
            def remove(command):
                commands.append(command)
                if command.endswith('/two'): raise OSError(16, 'busy')
            with patch.object(collector, 'command', side_effect=remove), \
                    patch.object(Path, 'rmdir'):
                errors = collector.cleanup()
            self.assertEqual(commands, ['-:houji_gpu_locks/two', '-:houji_gpu_locks/one'])
            self.assertEqual(len(errors), 1)
            self.assertEqual((collector.instance/'tracing_on').read_text(), '0')

    def test_post_exit_cleanup_selects_only_this_collectors_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collector = trace.Trace(root)
            collector.instance.mkdir(parents=True)
            (root / 'kprobe_events').write_text(
                'p:houji_gpu_locks/alloc drm_gem_get_pages\n'
                'r128:houji_gpu_locks/alloc_end drm_gem_get_pages\n'
                'p:other/probe drm_gem_get_pages\n')
            with patch.object(collector, 'cleanup', return_value=[]) as cleanup:
                self.assertEqual(collector.cleanup_stale(), [])
            self.assertTrue(collector.owned)
            self.assertEqual(collector.installed, ['alloc', 'alloc_end'])
            cleanup.assert_called_once()

    def test_btf_member_offsets(self):
        strings = b'\0sample\0member\0'
        types = struct.pack('<III', 1, (4 << 24) | 1, 16) + struct.pack('<III', 8, 0, 64)
        blob = struct.pack('<HBBIIIII', 0xEB9F, 1, 0, 24, 0, len(types), len(types), len(strings)) + types + strings
        self.assertEqual(trace.btf_layout(blob)['sample']['members']['member'], 8)
        with self.assertRaises(ValueError): trace.btf_layout(blob[:-1])


if __name__ == '__main__':
    unittest.main()
