#!/usr/bin/env python3
"""Refuse a Houji build that replaces or masks upstream Armada session switching.

Desktop switching broke after more than one release pull for the same reason:
the port installed its own copy of an upstream file, upstream then changed the
mechanism, and the copy kept shipping. Upstream's own tests kept passing because
they test upstream's file, which the image did not contain.

    check-upstream.py                 run before a build or after a release pull
    check-upstream.py --staged-root R verify a staged image root (stage-root.py)

Tree checks:
  1. No file in the port's overlay trees (copied over the image by stage-root.py)
     has the path of a file upstream ships in system_files.
  2. stage-root.py and the overlays do not write, link or mask the upstream
     session-switching files and units.
  3. Upstream's own session test passes, so the files that ship are the files
     that were tested.

Staged-root check: each upstream session file in the image is byte-identical to
system_files, and upstream's boot session default is not masked.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

DEFAULT_REPO = Path(__file__).resolve().parents[2]
OVERLAYS = ('ports/houji/runtime', 'ports/houji/firmware/root')
STAGING = ('ports/houji/stage-root.py',)
# Upstream owns session switching: Steam's "Switch to Desktop" hook, the switch
# and boot-default worker, the desktop/mobile choice and the SDDM autologin files.
SESSION_FILES = (
    'usr/libexec/os-session-select',
    'usr/libexec/armada/session-control',
    'usr/lib/armada/plasma-session-lib',
    'usr/libexec/armada/steam-default-session',
    'usr/share/wayland-sessions/armada-plasma.desktop',
    'usr/share/wayland-sessions/armada-plasma-mobile.desktop',
    'usr/lib/systemd/system/armada-session-default.service',
)
SESSION_TOKENS = (
    'os-session-select', 'steamos-session-select', 'session-control', 'plasma-session-lib',
    'steam-default-session', 'armada-session-default', 'wayland-sessions',
    'zz-holo-autologin', 'zz-steamos-autologin', 'zzt-holo-temp-login', 'zzt-steamos-temp-login',
    'houji-session-switch',
)
UPSTREAM_TEST = 'tests/plasma-session-test.sh'
# Device firmware the port must replace, pinned to the upstream file it was
# reviewed against. A release pull that changes one of these stops the build
# until the port copy is reviewed again and the pin updated. Session files can
# never be listed here.
REVIEWED_OVERRIDES = {
    # Kiwi-v2 firmware, N3 board data and regulatory files (HISTORY: Wi-Fi).
    'usr/lib/firmware/ath12k/WCN7850/hw2.0/amss.bin':
        '5660a1aa3db12716b42c687b0c88ef4f6ac80bd46e42cfe3636ed332d3f6e6ee',
    'usr/lib/firmware/ath12k/WCN7850/hw2.0/m3.bin':
        'ff40c57556c2fac552ac192c6e2ad59689fd8f50c9f9bcc997d13623c2bcee86',
    'usr/lib/firmware/ath12k/WCN7850/hw2.0/regdb.bin':
        'fe1327d789e4c93a9f6686afea5e17cd048b925693c6b9a7ac2c36026335d311',
    # Adreno 750 firmware set from the firmware baseline; SQE differs from upstream.
    'usr/lib/firmware/qcom/gen70900_aqe.fw':
        '25237c161d727b8ea92b4beb837a31651b44ea78bfc34d95672af8f346a45825',
    'usr/lib/firmware/qcom/gen70900_sqe.fw':
        'a739a1ef14e4418c5a1f1e1da444deb71aeeb4a31370ab41a4dddf378ab5683d',
    'usr/lib/firmware/qcom/gmu_gen70900.bin':
        '9685f62c42543befcaee68d25355e16bf55cdb0d573bddcc9f6633f59eec3f72',
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(directory):
    if not directory.is_dir():
        return set()
    return {str(p.relative_to(directory)) for p in directory.rglob('*')
            if (p.is_file() or p.is_symlink()) and '__pycache__' not in p.parts}


def tree_problems(repo):
    problems = []
    upstream = files(repo / 'system_files')
    if not upstream:
        return ['upstream system_files is missing or empty: ' + str(repo / 'system_files')]
    for path in sorted(set(REVIEWED_OVERRIDES) & set(SESSION_FILES)):
        problems.append(f'{path} is upstream session switching and cannot be a reviewed override')
    for overlay in OVERLAYS:
        for path in sorted(files(repo / overlay) & upstream):
            pinned = REVIEWED_OVERRIDES.get(path)
            if pinned is None or path in SESSION_FILES:
                problems.append(f'{overlay}/{path} replaces upstream system_files/{path}; '
                                'use upstream, or review it and add it to REVIEWED_OVERRIDES')
            elif sha256(repo / 'system_files' / path) != pinned:
                problems.append(f'upstream changed system_files/{path}, which {overlay} replaces; '
                                'review the port copy against it, then update its pin')
    scanned = [repo / name for name in STAGING]
    for overlay in OVERLAYS:
        scanned += [repo / overlay / path for path in sorted(files(repo / overlay))]
    for path in scanned:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        for token in SESSION_TOKENS:
            if token in text:
                problems.append(f'{path.relative_to(repo)} refers to upstream session switching '
                                f'({token}); leave session switching to upstream')
    test = repo / UPSTREAM_TEST
    if not test.is_file():
        problems.append(f'upstream session test {UPSTREAM_TEST} is missing; review the release pull')
    else:
        result = subprocess.run(['bash', str(test)], cwd=repo, capture_output=True, text=True, timeout=300)
        if result.returncode:
            tail = (result.stdout + result.stderr).strip().splitlines()[-5:]
            problems.append(f'upstream session test {UPSTREAM_TEST} failed: ' + ' | '.join(tail))
    return problems


def staged_problems(repo, root):
    problems = []
    for name in SESSION_FILES:
        upstream, staged = repo / 'system_files' / name, root / name
        if not upstream.is_file():
            problems.append(f'upstream no longer ships {name}; review SESSION_FILES after the release pull')
        elif not staged.is_file():
            problems.append(f'staged image lacks upstream {name}')
        elif sha256(staged) != sha256(upstream):
            problems.append(f'staged {name} differs from upstream system_files/{name}')
    mask = root / 'etc/systemd/system/armada-session-default.service'
    if mask.is_symlink() and str(mask.readlink()) == '/dev/null':
        problems.append('staged image masks upstream armada-session-default.service')
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--repo', type=Path, default=DEFAULT_REPO, help=argparse.SUPPRESS)
    parser.add_argument('--staged-root', type=Path, help='staged image root to verify')
    args = parser.parse_args()
    repo = args.repo.resolve()
    problems = staged_problems(repo, args.staged_root.resolve()) if args.staged_root else tree_problems(repo)
    for problem in problems:
        print('UPSTREAM CHECK FAILED:', problem, file=sys.stderr)
    if problems:
        sys.exit(1)
    print('Upstream session switching: staged image matches upstream' if args.staged_root
          else 'Upstream session switching: no port overrides; upstream session test passed')


if __name__ == '__main__':
    main()
