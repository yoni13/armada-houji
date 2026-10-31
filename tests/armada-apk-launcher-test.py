#!/usr/bin/env python3
"""armada-apk-launcher finds an APK's launcher, including one declared on an <activity-alias>.

The manifests are built here in Android's binary XML, so no APK from anywhere is needed.
"""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.dont_write_bytecode = True  # the script lives in a tree that is copied into images
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'packages/lepton/files/armada-apk-launcher'
loader = importlib.machinery.SourceFileLoader('armada_apk_launcher', str(SCRIPT))
spec = importlib.util.spec_from_loader('armada_apk_launcher', loader)
launcher = importlib.util.module_from_spec(spec)
loader.exec_module(launcher)

NONE = 0xFFFFFFFF
ANDROID_ATTRIBUTES = {'name': 0x01010003, 'enabled': 0x0101000E, 'targetActivity': 0x01010202}
MAIN, LAUNCHER = 'android.intent.action.MAIN', 'android.intent.category.LAUNCHER'


def length(n, utf8):
    if utf8:
        return bytes([n]) if n < 0x80 else bytes([(n >> 8) | 0x80, n & 0xFF])
    return struct.pack('<H', n) if n < 0x8000 else struct.pack('<HH', (n >> 16) | 0x8000, n & 0xFFFF)


def string_pool(strings, utf8):
    blobs = []
    for text in strings:
        if utf8:
            data = text.encode()
            blobs.append(length(len(text), True) + length(len(data), True) + data + b'\0')
        else:
            blobs.append(length(len(text), False) + text.encode('utf-16-le') + b'\0\0')
    offsets, body = [], b''
    for blob in blobs:
        offsets.append(len(body))
        body += blob
    body += b'\0' * (-len(body) % 4)
    start = 28 + 4 * len(strings)
    size = start + len(body)
    return (struct.pack('<HHIIIIII', 0x0001, 28, size, len(strings), 0, 0x100 if utf8 else 0, start, 0)
            + b''.join(struct.pack('<I', o) for o in offsets) + body)


def manifest(tree, utf8=False, strip_names=False):
    """tree is (element, {attribute: value}, [children]). 'android:x' attributes carry their resource id.

    A value is a str, a bool or ('ref', id)."""
    strings = list(ANDROID_ATTRIBUTES)
    if strip_names:  # the resource id still says what these are
        strings = ['a%d' % i for i in range(len(ANDROID_ATTRIBUTES))]

    def index(text):
        if text not in strings:
            strings.append(text)
        return strings.index(text)

    chunks = []

    def walk(node):
        name, attributes, children = node
        encoded = []
        for key, value in attributes.items():
            if key.startswith('android:'):
                label = key[8:]
                position = list(ANDROID_ATTRIBUTES).index(label)
                name_index = position
            else:
                name_index = index(key)
            if isinstance(value, bool):
                raw, kind, data = NONE, 0x12, 0xFFFFFFFF if value else 0
            elif isinstance(value, tuple):
                raw, kind, data = NONE, 0x01, value[1]
            else:
                raw = index(value)
                kind, data = 0x03, raw
            encoded.append(struct.pack('<IIIHBBI', NONE, name_index, raw, 8, 0, kind, data))
        element = index(name)
        chunks.append(struct.pack('<HHIIIIIHHHHHH', 0x0102, 16, 36 + 20 * len(encoded), 1, NONE, NONE, element,
                                  20, 20, len(encoded), 0, 0, 0) + b''.join(encoded))
        for child in children:
            walk(child)
        chunks.append(struct.pack('<HHIIIII', 0x0103, 16, 24, 1, NONE, NONE, element))

    walk(tree)
    ids = struct.pack('<%dI' % len(ANDROID_ATTRIBUTES), *ANDROID_ATTRIBUTES.values())
    resource_map = struct.pack('<HHI', 0x0180, 8, 8 + len(ids)) + ids
    body = string_pool(strings, utf8) + resource_map + b''.join(chunks)
    return struct.pack('<HHI', 0x0003, 8, 8 + len(body)) + body


def component(kind, name, enabled=None, target=None, main=True, category=LAUNCHER):
    attributes = {'android:name': name}
    if enabled is not None:
        attributes['android:enabled'] = enabled
    if target:
        attributes['android:targetActivity'] = target
    actions = [('action', {'android:name': MAIN}, [])] if main else []
    categories = [('category', {'android:name': category}, [])] if category else []
    return (kind, attributes, [('intent-filter', {}, actions + categories)])


def apk(*components, package='org.example.app', **options):
    tree = ('manifest', {'package': package}, [('application', {}, list(components))])
    return manifest(tree, **options)


class Parser(unittest.TestCase):
    def test_launcher_on_an_activity(self):
        self.assertEqual(launcher.launcher(apk(component('activity', 'org.example.app.Main'))), 'org.example.app.Main')

    def test_launcher_on_an_alias(self):
        data = apk(component('activity', 'org.example.app.Home', main=False, category=None),
                   component('activity-alias', 'org.example.app.Launch', target='org.example.app.Home'))
        self.assertEqual(launcher.launcher(data), 'org.example.app.Launch')

    def test_firefox_shape_picks_the_enabled_alias_among_disabled_icons(self):
        icons = [component('activity-alias', 'org.example.app.Icon%d' % n, enabled=False, target='org.example.app.Home')
                 for n in range(24)]
        data = apk(component('activity', 'org.example.app.Home', main=False, category=None),
                   component('activity', 'org.example.app.Receiver', category='android.intent.category.APP_BROWSER'),
                   *icons, component('activity-alias', 'org.example.app.App', target='org.example.app.Home'))
        self.assertEqual(launcher.launcher(data), 'org.example.app.App')

    def test_every_launcher_disabled_means_none(self):
        data = apk(component('activity-alias', 'org.example.app.A', enabled=False),
                   component('activity', 'org.example.app.B', enabled=False))
        self.assertIsNone(launcher.launcher(data))

    def test_an_alias_of_a_disabled_activity_is_skipped(self):
        data = apk(component('activity', 'org.example.app.Off', enabled=False, main=False, category=None),
                   component('activity-alias', 'org.example.app.Launch', target='org.example.app.Off'),
                   component('activity', 'org.example.app.Main'))
        self.assertEqual(launcher.launcher(data), 'org.example.app.Main')

    def test_main_and_launcher_must_share_one_filter(self):
        split = ('activity', {'android:name': 'org.example.app.Split'}, [
            ('intent-filter', {}, [('action', {'android:name': MAIN}, [])]),
            ('intent-filter', {}, [('category', {'android:name': LAUNCHER}, [])])])
        self.assertIsNone(launcher.launcher(apk(split)))

    def test_no_launcher(self):
        self.assertIsNone(launcher.launcher(apk(component('activity', 'org.example.app.Other', category=None))))
        self.assertIsNone(launcher.launcher(apk(component('activity', 'org.example.app.Other', main=False))))

    def test_an_enabled_component_beats_one_that_depends_on_a_resource(self):
        data = apk(component('activity-alias', 'org.example.app.Maybe', enabled=('ref', 0x7F040001)),
                   component('activity-alias', 'org.example.app.Sure', enabled=True))
        self.assertEqual(launcher.launcher(data), 'org.example.app.Sure')
        data = apk(component('activity-alias', 'org.example.app.Maybe', enabled=('ref', 0x7F040001)))
        self.assertEqual(launcher.launcher(data), 'org.example.app.Maybe')

    def test_relative_class_names_get_the_package(self):
        self.assertEqual(launcher.launcher(apk(component('activity', '.ui.Main'))), 'org.example.app.ui.Main')
        self.assertEqual(launcher.launcher(apk(component('activity', 'Main'))), 'org.example.app.Main')

    def test_both_string_pool_encodings_and_long_names(self):
        name = 'org.example.app.' + 'x' * 300
        for utf8 in (False, True):
            self.assertEqual(launcher.launcher(apk(component('activity', name), utf8=utf8)), name)
        non_ascii = 'org.example.app.Fenêtre'
        for utf8 in (False, True):
            self.assertEqual(launcher.launcher(apk(component('activity', non_ascii), utf8=utf8)), non_ascii)

    def test_stripped_attribute_names_are_read_by_resource_id(self):
        data = apk(component('activity-alias', 'org.example.app.Off', enabled=False),
                   component('activity-alias', 'org.example.app.On'), strip_names=True)
        self.assertEqual(launcher.launcher(data), 'org.example.app.On')

    def test_damaged_manifests_are_errors_not_guesses(self):
        good = apk(component('activity', 'org.example.app.Main'))
        for bad in (b'', b'not a manifest at all', good[:40], good[:-10], good[:8] + b'\xff' * 40):
            with self.assertRaises((launcher.ManifestError, struct.error, IndexError, UnicodeError)):
                launcher.launcher(bad)
        # A header with nothing after it is a manifest without components.
        self.assertIsNone(launcher.launcher(b'\x03\x00\x08\x00' + b'\x00' * 4))


class Command(unittest.TestCase):
    def run_on(self, name, data=None, entry='AndroidManifest.xml', raw=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            if raw is not None:
                path.write_bytes(raw)
            elif data is not None:
                with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr(entry, data)
                    archive.writestr('classes.dex', b'dex' * 1000)
            return subprocess.run([sys.executable, '-B', str(SCRIPT), str(path)], capture_output=True, text=True)

    def test_prints_the_class_and_succeeds(self):
        result = self.run_on('a.apk', apk(component('activity-alias', 'org.example.app.App')))
        self.assertEqual((result.returncode, result.stdout), (0, 'org.example.app.App\n'))

    def test_silent_failure_when_there_is_nothing_to_launch(self):
        for result in (self.run_on('a.apk', apk(component('activity', 'org.example.app.X', category=None))),
                       self.run_on('a.apk', b'junk'),
                       self.run_on('a.apk', apk(component('activity', 'org.example.app.X')), entry='Other.xml'),
                       self.run_on('a.apk', raw=b'PK not really a zip'),
                       self.run_on('missing.apk')):
            self.assertEqual((result.returncode, result.stdout), (1, ''))

    def test_wrong_usage(self):
        result = subprocess.run([sys.executable, '-B', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertIn('usage', result.stderr)

    def test_a_manifest_is_all_that_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'big.apk'
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as archive:
                archive.writestr('AndroidManifest.xml', apk(component('activity', 'org.example.app.Main')))
                archive.writestr('lib/arm64-v8a/libhuge.so', os.urandom(1 << 20))
            result = subprocess.run([sys.executable, '-B', str(SCRIPT), str(path)], capture_output=True, text=True)
            self.assertEqual(result.stdout, 'org.example.app.Main\n')

    def test_the_script_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        self.assertEqual(SCRIPT.read_text().splitlines()[0], '#!/usr/bin/python3')


if __name__ == '__main__':
    unittest.main()
