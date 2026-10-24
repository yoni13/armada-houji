#!/usr/bin/env python3
"""The Steam resume watchdog: act only on a stuck UI, and never on a new suspend."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest

sys.dont_write_bytecode = True  # the script lives in a tree that is copied into images
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'ports/houji/runtime/usr/libexec/armada/houji-steam-resume'
loader = importlib.machinery.SourceFileLoader('houji_steam_resume', str(SCRIPT))
spec = importlib.util.spec_from_loader('houji_steam_resume', loader)
watchdog = importlib.util.module_from_spec(spec)
loader.exec_module(watchdog)

NORMAL = {'suspending': False, 'progress': 1}
STUCK = {'suspending': True, 'progress': 1}
PREPARING = {'suspending': True, 'progress': 0}


class FakePlatform:
    def __init__(self, states, completed=(5, 5), active=False):
        self.states = list(states)
        self.completed = list(completed)
        self.active = active
        self.calls = []
        self.snapshots = []

    def sleep(self, seconds):
        self.calls.append(('sleep', seconds))

    def ui_state(self):
        self.calls.append(('state',))
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def kick(self):
        self.calls.append(('kick',))
        self.states = [NORMAL]
        return True

    def suspends_completed(self):
        return self.completed.pop(0) if len(self.completed) > 1 else self.completed[0]

    def suspend_active(self):
        return self.active

    def snapshot(self, tag, directory=None):
        self.snapshots.append(tag)
        return Path('/evidence')

    def kicked(self):
        return ('kick',) in self.calls


class Decision(unittest.TestCase):
    def test_normal_resume_does_nothing(self):
        platform = FakePlatform([NORMAL])
        self.assertEqual(watchdog.run(platform), 0)
        self.assertFalse(platform.kicked())
        self.assertEqual(platform.snapshots, [])

    def test_stuck_ui_is_resumed_and_evidence_saved(self):
        platform = FakePlatform([STUCK, STUCK, STUCK])
        watchdog.run(platform)
        self.assertTrue(platform.kicked())
        self.assertEqual(platform.snapshots, ['stuck', 'late'])

    def test_notification_arriving_during_the_grace_period_is_left_alone(self):
        platform = FakePlatform([STUCK, NORMAL])
        watchdog.run(platform)
        self.assertFalse(platform.kicked())

    def test_a_suspend_in_preparation_is_not_stuck(self):
        # The UI clears its progress while it prepares a new suspend.
        platform = FakePlatform([PREPARING, PREPARING])
        watchdog.run(platform)
        self.assertFalse(platform.kicked())

    def test_a_new_suspend_cancels_the_kick(self):
        self.assertFalse(self._kicked(FakePlatform([STUCK], completed=(5, 6))))
        self.assertFalse(self._kicked(FakePlatform([STUCK], active=True)))

    def _kicked(self, platform):
        watchdog.run(platform)
        return platform.kicked()

    def test_unreachable_ui_is_not_an_error(self):
        platform = FakePlatform([None])
        self.assertEqual(watchdog.run(platform), 0)
        self.assertFalse(platform.kicked())

    def test_the_kick_falls_between_normal_and_late_resumes(self):
        # Of 391 resumes logged 2026-10-04..23, 371 reached the UI within 5 s; the 20 late ones took 11 s or more.
        self.assertGreater(watchdog.GRACE, 5)
        self.assertLess(watchdog.GRACE + watchdog.RECHECK, 11)

    def test_main_never_fails_the_unit(self):
        class Broken:
            def suspends_completed(self):
                raise RuntimeError('boom')
        original = watchdog.Platform
        watchdog.Platform = Broken
        try:
            self.assertEqual(watchdog.main(), 0)
        finally:
            watchdog.Platform = original


class Evidence(unittest.TestCase):
    def test_bundle_has_what_the_next_occurrence_needs_and_is_private(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'evidence'
            original = watchdog.EVIDENCE
            watchdog.EVIDENCE = directory
            try:
                platform = watchdog.Platform()
                made = platform.snapshot('stuck')
                platform.snapshot('late', made)
            finally:
                watchdog.EVIDENCE = original
            names = {p.name for p in made.iterdir()}
            for expected in ('summary-stuck.txt', 'threads-stuck.txt', 'usb.txt', 'network.txt',
                             'summary-late.txt', 'threads-late.txt', 'steam-ui-suspend.txt'):
                self.assertIn(expected, names)
            self.assertEqual(oct(directory.stat().st_mode & 0o777), '0o700')
            self.assertEqual(oct(made.stat().st_mode & 0o777), '0o700')
            self.assertIn('boot_id', (made / 'summary-stuck.txt').read_text())

    def test_only_the_newest_bundles_are_kept(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            for number in range(15):
                folder = base / ('2026%04d' % number)
                folder.mkdir()
                (folder / 'summary-stuck.txt').write_text('x')
            watchdog.prune(base)
            self.assertEqual(sorted(p.name for p in base.iterdir()),
                             ['2026%04d' % n for n in range(5, 15)])

    def test_thread_dump_names_state_and_wait_channel(self):
        text = watchdog.threads(names=(Path('/proc/self/comm').read_text().strip(),))
        self.assertIn('state=', text)
        self.assertIn('wchan=', text)


class FakeDevTools(threading.Thread):
    """A loopback DevTools server speaking just enough HTTP and WebSocket."""

    def __init__(self, answers, fragment=False):
        super().__init__(daemon=True)
        self.server = socket.socket()
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        self.answers, self.fragment, self.seen = answers, fragment, []

    def run(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            with connection:
                request = b''
                while b'\r\n\r\n' not in request:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    request += chunk
                if request.startswith(b'GET /json/list'):
                    body = json.dumps([
                        {'title': 'QuickAccess', 'webSocketDebuggerUrl': 'ws://127.0.0.1:%d/devtools/page/A' % self.port},
                        {'title': 'SharedJSContext', 'webSocketDebuggerUrl': 'ws://127.0.0.1:%d/devtools/page/B' % self.port}]).encode()
                    connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s'
                                       % (len(body), body))
                elif request.startswith(b'GET /devtools/page/B'):
                    self.seen.append(request)
                    connection.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n')
                    command = self.read_frame(connection)
                    self.seen.append(command)
                    reply = json.dumps({'id': command['id'], 'result': {'result': {
                        'type': 'string', 'value': self.answers[command['params']['expression']]}}}).encode()
                    connection.sendall(b'\x89\x00')  # a ping first: it must be ignored
                    if self.fragment:
                        connection.sendall(bytes([0x01, 5]) + reply[:5] + bytes([0x80, len(reply) - 5]) + reply[5:])
                    else:
                        connection.sendall(bytes([0x81, 126]) + struct.pack('>H', len(reply)) + reply)

    @staticmethod
    def read_frame(connection):
        head = connection.recv(2)
        assert head[0] == 0x81 and head[1] & 0x80, 'client frames must be masked text'
        size = head[1] & 0x7f
        if size == 126:
            size = struct.unpack('>H', connection.recv(2))[0]
        mask = connection.recv(4)
        data = b''
        while len(data) < size:
            data += connection.recv(size - len(data))
        return json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


class DevToolsClient(unittest.TestCase):
    def run_against(self, fragment):
        answers = {watchdog.STATE: json.dumps(STUCK), watchdog.KICK: 'ok'}
        server = FakeDevTools(answers, fragment)
        server.start()
        self.addCleanup(server.server.close)
        cef = watchdog.Cef(port=server.port)
        return cef, server

    def test_reads_the_ui_state_through_the_shared_context(self):
        cef, server = self.run_against(False)
        self.assertEqual(json.loads(cef.evaluate(watchdog.STATE)), STUCK)
        self.assertIn(b'Upgrade: websocket', server.seen[0])
        self.assertEqual(server.seen[1]['method'], 'Runtime.evaluate')
        self.assertIs(server.seen[1]['params']['returnByValue'], True)

    def test_survives_a_ping_and_fragmented_replies(self):
        cef, _ = self.run_against(True)
        self.assertEqual(cef.evaluate(watchdog.KICK), 'ok')

    def test_unreachable_devtools_is_an_oserror(self):
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        with self.assertRaises(OSError):
            watchdog.Cef(port=port, timeout=1).evaluate(watchdog.STATE)


class Packaging(unittest.TestCase):
    def test_runs_after_every_suspend_without_blocking_it(self):
        drop_in = (ROOT / 'ports/houji/runtime/usr/lib/systemd/system/systemd-suspend.service.d/'
                   '40-houji-steam-resume.conf').read_text()
        self.assertIn('ExecStopPost=-/usr/bin/systemctl --no-block start houji-steam-resume.service', drop_in)
        unit = (ROOT / 'ports/houji/runtime/usr/lib/systemd/system/houji-steam-resume.service').read_text()
        self.assertIn('ExecStart=/usr/libexec/armada/houji-steam-resume', unit)
        self.assertIn('Type=oneshot', unit)


if __name__ == '__main__':
    unittest.main()
