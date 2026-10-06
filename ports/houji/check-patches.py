#!/usr/bin/env python3
"""Check that every Houji patch still applies to the source it was written for.

kernel     The shared Armada kernel series plus Houji's series, applied in the
           same order and with the same rules as packages/kernel/scripts/
           build-kernel.sh (a verbose dry run that must parse every hunk, then
           a zero-fuzz apply) onto the pinned, hash-checked Linux tarball.
           Only the files the series touches are extracted.
userspace  The pinned git revisions of hexagonrpc, iio-sensor-proxy, gamescope
           and tqftpserv with their local patches, applied like buildlib.checkout.

Nothing here needs a phone, a toolchain or the original porting workspace.
Patched userspace trees can be kept with --keep for the native tests.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

PORT = Path(__file__).resolve().parent
REPO = PORT.parents[1]
GITHUB = os.environ.get('GITHUB_ACTIONS') == 'true'


def say(message=''):
    print(message, flush=True)


def error(message, file=None):
    """Print an error, as a workflow annotation when running in Actions."""
    if GITHUB:
        where = ' file=%s' % file if file else ''
        print('::error%s::%s' % (where, message), flush=True)
    say('ERROR: ' + message)


def sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def retry(action, what, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except (OSError, subprocess.CalledProcessError) as failure:
            if attempt == attempts:
                raise
            say('%s failed (%s); retrying' % (what, failure))
            time.sleep(5 * attempt)


# --------------------------------------------------------------------------
# Kernel
# --------------------------------------------------------------------------

def read_series(series):
    """Patch file names in order. Comments and blank lines are ignored."""
    names = []
    for line in Path(series).read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            names.append(line.split()[0])
    return names


def patch_targets(patch):
    """Paths (relative to the tree, after -p1) that a patch reads or writes."""
    targets = set()
    for line in Path(patch).read_text(errors='replace').splitlines():
        if line.startswith(('--- ', '+++ ')):
            path = line[4:].split('\t', 1)[0].strip()
            if path == '/dev/null' or '/' not in path:
                continue
            targets.add(path.split('/', 1)[1])
    return targets


def hunk_count(patch):
    return sum(1 for line in Path(patch).read_text(errors='replace').splitlines()
               if line.startswith('@@ '))


def combined_series():
    """[(name, path)] for the shared series followed by Houji's."""
    shared_dir = REPO / 'packages/kernel/patches'
    houji_dir = PORT / 'patches'
    entries = []
    seen = {}
    problems = []
    for directory in (shared_dir, houji_dir):
        for name in read_series(directory / 'series'):
            path = directory / name
            if not path.is_file():
                problems.append('%s is listed in %s/series but does not exist'
                                % (name, directory.relative_to(REPO)))
                continue
            if name in seen:
                problems.append('%s appears in both %s and %s'
                                % (name, seen[name], directory.relative_to(REPO)))
                continue
            seen[name] = str(directory.relative_to(REPO))
            entries.append((name, path))
    listed = set(read_series(houji_dir / 'series'))
    for path in sorted(houji_dir.glob('*.patch')):
        if path.name not in listed:
            problems.append('%s is in ports/houji/patches but not in its series' % path.name)
    return entries, problems


def kernel_pin():
    pin = json.loads((PORT / 'sources.json').read_text())['linux']
    base = (REPO / 'packages/kernel/BASE.env').read_text().strip()
    if base != 'VERSION=' + pin['version']:
        raise SystemExit('Houji Linux pin %s does not match packages/kernel/BASE.env (%s)'
                         % (pin['version'], base))
    return pin


def fetch_kernel(pin, cache):
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / ('linux-%s.tar.xz' % pin['version'])
    if archive.exists() and sha256(archive) != pin['sha256']:
        say('Cached kernel archive is corrupt; downloading again')
        archive.unlink()
    if not archive.exists():
        say('Downloading %s' % pin['url'])
        part = archive.with_name(archive.name + '.part')

        def download():
            with urllib.request.urlopen(pin['url'], timeout=120) as source, part.open('wb') as target:
                shutil.copyfileobj(source, target)
        retry(download, 'kernel download')
        if sha256(part) != pin['sha256']:
            part.unlink()
            raise SystemExit('Kernel archive checksum mismatch (expected %s)' % pin['sha256'])
        part.replace(archive)
    return archive


def extract_needed(archive, version, wanted, tree):
    """Extract only the regular files in `wanted` from the kernel archive."""
    prefix = 'linux-%s/' % version
    found = 0
    with tarfile.open(archive, 'r:xz') as tar:
        for member in tar:
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            relative = member.name[len(prefix):]
            if relative not in wanted:
                continue
            destination = tree / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, destination.open('wb') as target:
                shutil.copyfileobj(source, target)
            found += 1
    return found


def apply_series(entries, tree):
    """The build's rules: verbose dry run parsing every hunk, then a real apply."""
    failures = []
    for name, path in entries:
        expected = hunk_count(path)
        patch = path.read_bytes()
        dry = subprocess.run(
            ['patch', '-p1', '--batch', '--forward', '-F0', '--dry-run', '--verbose'],
            input=patch, cwd=tree, capture_output=True)
        log = dry.stdout.decode(errors='replace') + dry.stderr.decode(errors='replace')
        if dry.returncode:
            failures.append((name, 'does not apply cleanly', log))
            continue
        parsed = len(re.findall(r'^Hunk #\d+.*succeeded', log, re.M))
        if parsed != expected:
            failures.append((name, '%d of %d hunks parsed' % (parsed, expected), log))
            continue
        real = subprocess.run(
            ['patch', '-p1', '--batch', '--forward', '-F0', '--no-backup-if-mismatch', '--quiet'],
            input=patch, cwd=tree, capture_output=True)
        if real.returncode:
            failures.append((name, 'rejected on apply',
                             real.stdout.decode(errors='replace') + real.stderr.decode(errors='replace')))
    return failures


def check_kernel(cache):
    pin = kernel_pin()
    entries, problems = combined_series()
    say('Linux %s: %d patches (%d shared, %d Houji)' % (
        pin['version'], len(entries),
        len(read_series(REPO / 'packages/kernel/patches/series')),
        len(read_series(PORT / 'patches/series'))))
    for problem in problems:
        error(problem)
    wanted = set()
    for _, path in entries:
        wanted |= patch_targets(path)
    archive = fetch_kernel(pin, cache)
    with tempfile.TemporaryDirectory(prefix='houji-kernel-check-') as scratch:
        tree = Path(scratch)
        started = time.monotonic()
        found = extract_needed(archive, pin['version'], wanted, tree)
        say('Extracted %d existing files the series touches in %.0f s'
            % (found, time.monotonic() - started))
        failures = apply_series(entries, tree)
    for name, reason, log in failures:
        error('%s: %s' % (name, reason), file='ports/houji/patches/%s' % name)
        say(log.rstrip()[-1500:])
    ok = not failures and not problems
    if ok:
        say('All %d kernel patches apply with no fuzz' % len(entries))
    return replay_dbs_parser(archive) and ok


def replay_dbs_parser(archive):
    """Run patch 0002's parser on the phone's real record, on the host."""
    script = PORT / 'test-wmi-dbs-parser.py'
    say('ath12k DBS/SBS parser replay (patch 0002) on the unpatched source')
    replay = subprocess.run([sys.executable, '-B', str(script), str(archive)],
                            capture_output=True, text=True)
    lines = (replay.stdout + replay.stderr).strip().splitlines()
    if replay.returncode:
        error('DBS/SBS parser replay failed', file='ports/houji/test-wmi-dbs-parser.py')
        say('\n'.join(lines[-12:]))
        return False
    say(lines[-1] if lines else 'passed')
    return True


# --------------------------------------------------------------------------
# Userspace
# --------------------------------------------------------------------------

def userspace_pins():
    """[(name, url, revision, [(patch path, sha256 or None)])]"""
    sensors = json.loads((PORT / 'sensors/sources.json').read_text())
    gamescope = json.loads((PORT / 'sensors/gamescope/sources.json').read_text())['compositor']
    gps = json.loads((PORT / 'gps/sources.json').read_text())
    pins = []
    for name in ('libssc', 'hexagonrpc', 'iio-sensor-proxy'):
        entry = sensors[name]
        pins.append((name, entry['source'], entry['revision'],
                     [(PORT / 'sensors' / entry['local_patch'], entry['patch_sha256'])]))
    pins.append(('gamescope', gamescope['repository'], gamescope['revision'],
                 [(PORT / 'sensors/gamescope' / patch, None) for patch in gamescope['patches']]))
    pins.append(('tqftpserv', 'https://github.com/linux-msm/tqftpserv.git', gps['tqftpserv'],
                 [(PORT / 'gps/tqftpserv-mbnconfig.patch', None)]))
    cellular = json.loads((PORT/'cellular/sources.json').read_text())['git']
    for name, patch in [('minkipc','rpmb-read-only.patch'), ('lpac','lpac-activation-stdin.patch'),
                        ('ModemManager','modemmanager-primary-gw.patch'),
                        ('modemmanager-qt','modemmanager-qt-lifecycle.patch'),
                        ('plasma-nm','plasma-nm-data-toggle.patch')]:
        pin = cellular[name]
        patches = [(PORT/'cellular'/patch, None)]
        if name == 'ModemManager':
            patches.append((PORT/'cellular/modemmanager-qrtr-resume.patch', None))
        pins.append((name, pin['url'], pin['revision'], patches))
    return pins


def git(*args, cwd=None):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True)


def fetch_revision(url, revision, tree):
    """Shallow-fetch exactly one revision into tree."""
    tree.mkdir(parents=True, exist_ok=True)
    git('init', '-q', cwd=tree)
    git('remote', 'add', 'origin', url, cwd=tree)
    retry(lambda: git('fetch', '-q', '--depth', '1', 'origin', revision, cwd=tree),
          'fetch of %s' % url)
    git('checkout', '-q', '--detach', 'FETCH_HEAD', cwd=tree)
    actual = git('rev-parse', 'HEAD', cwd=tree).stdout.strip()
    if actual != revision:
        raise SystemExit('%s: fetched %s, expected %s' % (url, actual, revision))


def check_userspace(keep):
    ok = True
    with tempfile.TemporaryDirectory(prefix='houji-userspace-check-') as scratch:
        for name, url, revision, patches in userspace_pins():
            say('%s @ %s' % (name, revision[:12]))
            tree = Path(scratch) / name
            for path, digest in patches:
                if digest and sha256(path) != digest:
                    error('%s does not match its pinned checksum' % path.name,
                          file=str(path.relative_to(REPO)))
                    ok = False
            try:
                fetch_revision(url, revision, tree)
            except (SystemExit, subprocess.CalledProcessError, OSError) as failure:
                detail = getattr(failure, 'stderr', '') or failure
                error('%s: cannot fetch %s: %s' % (name, revision, str(detail).strip()))
                ok = False
                continue
            for path, _ in patches:
                result = subprocess.run(['git', 'apply', str(path)], cwd=tree,
                                        capture_output=True, text=True)
                if result.returncode:
                    error('%s does not apply to %s: %s' % (path.name, name, result.stderr.strip()),
                          file=str(path.relative_to(REPO)))
                    ok = False
                    break
                say('  applied %s' % path.name)
            if keep:
                destination = Path(keep) / name
                shutil.rmtree(destination, ignore_errors=True)
                shutil.copytree(tree, destination, ignore=shutil.ignore_patterns('.git'))
    if ok:
        say('All userspace patches apply to their pinned revisions')
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('what', choices=['kernel', 'userspace', 'all'])
    parser.add_argument('--cache', type=Path,
                        default=Path(os.environ.get('HOUJI_CACHE_DIR',
                                                    Path.home() / '.cache/houji-checks')),
                        help='where the kernel tarball is kept (default: %(default)s)')
    parser.add_argument('--keep', type=Path,
                        help='copy each patched userspace tree to DIR/<name>')
    args = parser.parse_args()
    ok = True
    if args.what in ('kernel', 'all'):
        ok = check_kernel(args.cache) and ok
    if args.what in ('userspace', 'all'):
        ok = check_userspace(args.keep) and ok
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
