#!/usr/bin/env python3
"""The build gate keeps desktop switching on upstream Armada's own files."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / 'ports/houji/check-upstream.py'
spec = importlib.util.spec_from_file_location('check_upstream', CHECK)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def run(*args):
    return subprocess.run([sys.executable, str(CHECK), *map(str, args)], capture_output=True, text=True)


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


class UpstreamSessionGate(unittest.TestCase):
    def test_this_tree_ships_upstream_session_switching(self):
        result = run()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in check.SESSION_FILES:
            self.assertTrue((ROOT / 'system_files' / name).is_file(), name)
        self.assertFalse((ROOT / 'ports/houji/runtime/usr/libexec/os-session-select').exists())
        self.assertNotIn('armada-session-default', (ROOT / 'ports/houji/stage-root.py').read_text())

    def fake_repo(self, directory):
        repo = Path(directory) / 'repo'
        for name in check.SESSION_FILES:
            write(repo / 'system_files' / name, f'upstream {name}\n')
        write(repo / check.UPSTREAM_TEST, '#!/bin/bash\nexit 0\n', 0o755)
        write(repo / 'ports/houji/stage-root.py', 'print("stage")\n')
        write(repo / 'ports/houji/runtime/usr/libexec/houji-tool', 'port only\n')
        return repo

    def failures(self, repo, *args):
        result = run('--repo', repo, *args)
        self.assertNotEqual(result.returncode, 0, 'gate accepted a broken tree')
        return result.stderr

    def test_fake_tree_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run('--repo', self.fake_repo(directory))
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_port_copy_of_upstream_hook_fails(self):
        # The bug: a port os-session-select replaced upstream's Steam hook.
        with tempfile.TemporaryDirectory() as directory:
            repo = self.fake_repo(directory)
            write(repo / 'ports/houji/runtime/usr/libexec/os-session-select', 'port hook\n')
            self.assertIn('replaces upstream system_files/usr/libexec/os-session-select',
                          self.failures(repo))

    def test_staging_autologin_or_masking_fails(self):
        for text in ["write('etc/sddm.conf.d/zz-steamos-autologin.conf', 'x')",
                     "link('etc/systemd/system/armada-session-default.service', '/dev/null')",
                     "copy(src, 'usr/libexec/armada/houji-session-switch')"]:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                repo = self.fake_repo(directory)
                write(repo / 'ports/houji/stage-root.py', text + '\n')
                self.assertIn('leave session switching to upstream', self.failures(repo))

    def test_upstream_test_missing_or_failing_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.fake_repo(directory)
            write(repo / check.UPSTREAM_TEST, '#!/bin/bash\necho broken switch\nexit 1\n', 0o755)
            self.assertIn('broken switch', self.failures(repo))
            (repo / check.UPSTREAM_TEST).unlink()
            self.assertIn('is missing', self.failures(repo))

    def test_reviewed_override_needs_review_after_upstream_change(self):
        path = 'usr/lib/firmware/qcom/gen70900_sqe.fw'
        with tempfile.TemporaryDirectory() as directory:
            repo = self.fake_repo(directory)
            write(repo / 'ports/houji/firmware/root' / path, 'port firmware\n')
            write(repo / 'system_files' / path, 'new upstream firmware\n')
            self.assertIn('upstream changed system_files/' + path, self.failures(repo))
            unreviewed = 'usr/lib/firmware/qcom/other.fw'
            write(repo / 'system_files' / unreviewed, 'upstream\n')
            write(repo / 'ports/houji/firmware/root' / unreviewed, 'port\n')
            self.assertIn('add it to REVIEWED_OVERRIDES', self.failures(repo))

    def test_staged_root_must_match_upstream(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.fake_repo(directory)
            staged = Path(directory) / 'staged'
            for name in check.SESSION_FILES:
                write(staged / name, (repo / 'system_files' / name).read_text())
            result = run('--repo', repo, '--staged-root', staged)
            self.assertEqual(result.returncode, 0, result.stderr)
            write(staged / 'usr/libexec/os-session-select', 'port hook\n')
            self.assertIn('differs from upstream', self.failures(repo, '--staged-root', staged))
            write(staged / 'usr/libexec/os-session-select',
                  (repo / 'system_files/usr/libexec/os-session-select').read_text())
            mask = staged / 'etc/systemd/system/armada-session-default.service'
            mask.parent.mkdir(parents=True)
            mask.symlink_to('/dev/null')
            self.assertIn('masks upstream armada-session-default',
                          self.failures(repo, '--staged-root', staged))


if __name__ == '__main__':
    unittest.main()
