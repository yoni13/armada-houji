#!/usr/bin/env python3
"""The patch checker has to fail when a patch is wrong, not only pass when it is right."""
import difflib
import hashlib
import importlib.util
import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('check_patches', ROOT / 'ports/houji/check-patches.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

BASE = ''.join('line %d\n' % n for n in range(1, 21))


def make_patch(path, before, after, name='x.patch'):
    body = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        'a/' + path, 'b/' + path))
    return 'Subject: [PATCH] test\n\n---\n' + body


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for patcher in (mock.patch('sys.stdout', io.StringIO()), mock.patch('time.sleep')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


class SeriesTests(Workspace):
    def test_comments_blank_lines_and_trailing_comments_are_ignored(self):
        series = self.write('series', '# header\n\n0001-a.patch\n  0002-b.patch # note\n# 0003-off.patch\n')
        self.assertEqual(check.read_series(series), ['0001-a.patch', '0002-b.patch'])

    def test_targets_cover_both_sides_and_skip_dev_null(self):
        patch = self.write('p.patch', '--- a/old/file.c\t2026-01-01\n+++ b/new/file.c\n'
                                      '@@ -1 +1 @@\n-a\n+b\n--- /dev/null\n+++ b/created.c\n@@ -0,0 +1 @@\n+x\n')
        self.assertEqual(check.patch_targets(patch), {'old/file.c', 'new/file.c', 'created.c'})

    def test_hunks_are_counted(self):
        patch = self.write('p.patch', make_patch('f', BASE, BASE.replace('line 3', 'three').replace('line 18', 'eighteen')))
        self.assertEqual(check.hunk_count(patch), 2)


class ApplyTests(Workspace):
    def apply(self, patch_text, tree_text=BASE):
        tree = self.root / 'tree'
        tree.mkdir(exist_ok=True)
        (tree / 'f').write_text(tree_text)
        path = self.write('p.patch', patch_text)
        return check.apply_series([('p.patch', path)], tree), tree

    def test_a_matching_patch_applies(self):
        failures, tree = self.apply(make_patch('f', BASE, BASE.replace('line 5', 'five')))
        self.assertEqual(failures, [])
        self.assertIn('five', (tree / 'f').read_text())

    def test_changed_context_is_rejected(self):
        patch = make_patch('f', BASE, BASE.replace('line 5', 'five'))
        failures, _ = self.apply(patch, BASE.replace('line 4', 'moved'))
        self.assertEqual([f[0] for f in failures], ['p.patch'])

    def test_a_patch_that_needs_fuzz_is_rejected(self):
        # Default patch would apply this by ignoring one context line.
        patch = make_patch('f', BASE, BASE.replace('line 10', 'ten'))
        drifted = BASE.replace('line 9', 'changed 9')
        failures, tree = self.apply(patch, drifted)
        self.assertEqual(len(failures), 1)
        self.assertIn('line 10', (tree / 'f').read_text())  # nothing was written

    def test_a_failing_patch_does_not_stop_later_ones_being_checked(self):
        tree = self.root / 'tree'
        tree.mkdir()
        (tree / 'f').write_text(BASE)
        bad = self.write('bad.patch', make_patch('f', BASE.replace('line 1\n', 'other\n'), BASE))
        good = self.write('good.patch', make_patch('f', BASE, BASE.replace('line 15', 'fifteen')))
        failures = check.apply_series([('bad.patch', bad), ('good.patch', good)], tree)
        self.assertEqual([f[0] for f in failures], ['bad.patch'])
        self.assertIn('fifteen', (tree / 'f').read_text())

    def test_offsets_are_allowed_like_the_build(self):
        shifted = 'new first line\n' + BASE
        failures, _ = self.apply(make_patch('f', BASE, BASE.replace('line 12', 'twelve')), shifted)
        self.assertEqual(failures, [])


class SeriesLayoutTests(Workspace):
    def layout(self, shared, houji, shared_files=(), houji_files=()):
        shared_dir, houji_dir = self.root / 'shared', self.root / 'houji'
        for directory, series, files in ((shared_dir, shared, shared_files), (houji_dir, houji, houji_files)):
            directory.mkdir(exist_ok=True)
            (directory / 'series').write_text(''.join(n + '\n' for n in series))
            for name in files:
                (directory / name).write_text('')
        patcher = mock.patch.multiple(check, REPO=self.root, PORT=self.root / 'port')
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.root / 'port').mkdir(exist_ok=True)
        (self.root / 'packages/kernel').mkdir(parents=True, exist_ok=True)
        (self.root / 'packages/kernel/patches').symlink_to(shared_dir)
        (self.root / 'port/patches').symlink_to(houji_dir)
        return check.combined_series()

    def test_shared_patches_come_first_then_houji(self):
        entries, problems = self.layout(['1.patch'], ['2.patch'], ['1.patch'], ['2.patch'])
        self.assertEqual([n for n, _ in entries], ['1.patch', '2.patch'])
        self.assertEqual(problems, [])

    def test_a_listed_patch_that_is_missing_is_a_problem(self):
        _, problems = self.layout(['1.patch'], ['2.patch'], ['1.patch'], [])
        self.assertTrue(any('2.patch' in p and 'does not exist' in p for p in problems))

    def test_a_patch_file_missing_from_the_series_is_a_problem(self):
        _, problems = self.layout([], ['1.patch'], [], ['1.patch', 'forgotten.patch'])
        self.assertTrue(any('forgotten.patch' in p and 'not in its series' in p for p in problems))

    def test_the_same_name_in_both_series_is_a_problem(self):
        _, problems = self.layout(['1.patch'], ['1.patch'], ['1.patch'], ['1.patch'])
        self.assertTrue(any('appears in both' in p for p in problems))


class KernelArchiveTests(Workspace):
    def archive(self, files, version='9.9'):
        path = self.root / ('linux-%s.tar.xz' % version)
        with tarfile.open(path, 'w:xz') as tar:
            for name, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo('linux-%s/%s' % (version, name))
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return path

    def test_only_the_wanted_existing_files_are_extracted(self):
        archive = self.archive({'a.txt': 'A', 'dir/b.txt': 'B', 'other.txt': 'O'})
        tree = self.root / 'tree'
        found = check.extract_needed(archive, '9.9', {'a.txt', 'dir/b.txt', 'not-in-tarball.c'}, tree)
        self.assertEqual(found, 2)
        self.assertEqual(sorted(p.relative_to(tree).as_posix() for p in tree.rglob('*') if p.is_file()),
                         ['a.txt', 'dir/b.txt'])

    def pin(self, archive, digest=None):
        return {'version': '9.9', 'url': archive.as_uri(),
                'sha256': digest or hashlib.sha256(archive.read_bytes()).hexdigest()}

    def test_a_download_with_the_right_checksum_is_kept(self):
        source = self.archive({'a': 'x'})
        cache = self.root / 'cache'
        path = check.fetch_kernel(self.pin(source), cache)
        self.assertEqual(path.read_bytes(), source.read_bytes())

    def test_a_download_with_the_wrong_checksum_is_rejected_and_not_cached(self):
        source = self.archive({'a': 'x'})
        cache = self.root / 'cache'
        with self.assertRaises(SystemExit):
            check.fetch_kernel(self.pin(source, '0' * 64), cache)
        self.assertEqual(list(cache.glob('linux-*')), [])

    def test_a_corrupt_cached_archive_is_downloaded_again(self):
        source = self.archive({'a': 'x'})
        cache = self.root / 'cache'
        cache.mkdir()
        (cache / 'linux-9.9.tar.xz').write_bytes(b'truncated')
        path = check.fetch_kernel(self.pin(source), cache)
        self.assertEqual(path.read_bytes(), source.read_bytes())


class GitFetchTests(Workspace):
    def repo(self):
        origin = self.root / 'origin'
        origin.mkdir()
        run = lambda *a: subprocess.run(['git', *a], cwd=origin, check=True, capture_output=True, text=True)
        run('init', '-q')
        run('config', 'user.email', 't@example.invalid')
        run('config', 'user.name', 'test')
        run('config', 'uploadpack.allowAnySHA1InWant', 'true')
        (origin / 'f').write_text(BASE)
        run('add', 'f')
        run('commit', '-q', '-m', 'one')
        first = run('rev-parse', 'HEAD').stdout.strip()
        (origin / 'f').write_text(BASE + 'more\n')
        run('commit', '-qam', 'two')
        return origin, first

    def test_one_exact_revision_is_fetched_shallowly(self):
        origin, first = self.repo()
        tree = self.root / 'tree'
        check.fetch_revision(origin.as_uri(), first, tree)
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=tree, capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, first)
        self.assertEqual((tree / 'f').read_text(), BASE)

    def test_an_unknown_revision_fails_instead_of_silently_passing(self):
        origin, _ = self.repo()
        with self.assertRaises(subprocess.CalledProcessError):
            check.fetch_revision(origin.as_uri(), 'f' * 40, self.root / 'tree')


class RepositoryPinTests(unittest.TestCase):
    """Offline consistency of the real repository."""

    def test_pinned_patch_checksums_match_the_files(self):
        pins = check.userspace_pins()
        self.assertGreaterEqual(len(pins), 4)
        for name, url, revision, patches in pins:
            self.assertRegex(revision, r'^[0-9a-f]{40}$', name)
            self.assertTrue(url.startswith('https://'), name)
            for path, digest in patches:
                self.assertTrue(path.is_file(), '%s: %s missing' % (name, path))
                if digest:
                    self.assertEqual(check.sha256(path), digest,
                                     '%s: update patch_sha256 after editing %s' % (name, path.name))

    def test_the_real_kernel_series_is_complete_and_unambiguous(self):
        entries, problems = check.combined_series()
        self.assertEqual(problems, [])
        self.assertGreater(len(entries), 150)

    def test_the_kernel_pin_matches_the_shared_package(self):
        pin = check.kernel_pin()
        self.assertRegex(pin['sha256'], r'^[0-9a-f]{64}$')
        self.assertTrue(pin['url'].endswith('linux-%s.tar.xz' % pin['version']))


if __name__ == '__main__':
    unittest.main()
