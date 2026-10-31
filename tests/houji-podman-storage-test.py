#!/usr/bin/env python3
"""Rootless podman gets fuse-overlayfs on Houji, because /var/home is an overlay and the kernel refuses
an overlay on an overlay. The file is the image's own default with one setting; nothing else may change."""
import os
from pathlib import Path
import re
import sys
import tomllib
import unittest

sys.dont_write_bytecode = True  # the scripts live in a tree that is copied into images
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ports/houji'))
import buildlib  # noqa: E402

# Fedora 44's /usr/share/containers/storage.conf, comments removed, as the Armada image ships it.
DEFAULT = '''[storage]
driver = "overlay"
runroot = "/run/containers/storage"
# comment
graphroot = "/var/lib/containers/storage"

[storage.options]
additionalimagestores = [
"/usr/lib/containers/storage",
]

[storage.options.pull_options]
# a comment

[storage.options.overlay]
# ignore_chown_errors can be set to allow a non privileged user
mountopt = "nodev,metacopy=on"
'''


class StorageConf(unittest.TestCase):
    def test_adds_only_the_mount_program_and_drops_the_root_only_paths(self):
        text = buildlib.rootless_podman_storage_conf(DEFAULT)
        before, after = tomllib.loads(DEFAULT), tomllib.loads(text)
        self.assertEqual(after['storage']['options']['overlay']['mount_program'], '/usr/bin/fuse-overlayfs')
        self.assertEqual(after['storage']['options']['overlay']['mountopt'], 'nodev,metacopy=on')
        self.assertEqual(after['storage']['options']['additionalimagestores'], ['/usr/lib/containers/storage'])
        self.assertEqual(after['storage']['driver'], 'overlay')
        # A file chosen by the user is taken literally, so the root-only locations must not be in it.
        self.assertNotIn('graphroot', after['storage'])
        self.assertNotIn('runroot', after['storage'])
        for table in ('graphroot', 'runroot'):
            self.assertIn(table, before['storage'])

    def test_comments_survive(self):
        text = buildlib.rootless_podman_storage_conf(DEFAULT)
        self.assertIn('# ignore_chown_errors can be set', text)
        self.assertIn('# comment', text)

    def test_the_setting_goes_into_the_overlay_table(self):
        text = buildlib.rootless_podman_storage_conf(DEFAULT)
        self.assertEqual(len(re.findall(r'^mount_program', text, re.M)), 1)
        self.assertLess(text.index('[storage.options.overlay]'), text.index('mount_program'))

    def test_a_default_without_an_overlay_table_gets_one(self):
        text = buildlib.rootless_podman_storage_conf('[storage]\ndriver = "overlay"\ngraphroot = "/x"\n')
        self.assertEqual(tomllib.loads(text), {'storage': {'driver': 'overlay',
                         'options': {'overlay': {'mount_program': '/usr/bin/fuse-overlayfs'}}}})

    def test_an_image_that_already_chose_one_must_be_reviewed(self):
        chosen = DEFAULT.replace('mountopt = ', 'mount_program = "/usr/bin/other"\nmountopt = ')
        with self.assertRaises(ValueError):
            buildlib.rootless_podman_storage_conf(chosen)

    def test_invalid_defaults_are_errors(self):
        with self.assertRaises(tomllib.TOMLDecodeError):
            buildlib.rootless_podman_storage_conf('[storage\n')

    def test_the_overlay_is_not_assumed_native_by_other_keys(self):
        # Only the three keys in question may differ from the default.
        before = tomllib.loads(DEFAULT)
        after = tomllib.loads(buildlib.rootless_podman_storage_conf(DEFAULT))
        before['storage'].pop('graphroot')
        before['storage'].pop('runroot')
        before['storage']['options']['overlay']['mount_program'] = '/usr/bin/fuse-overlayfs'
        self.assertEqual(before, after)


class Packaging(unittest.TestCase):
    unit = (ROOT / 'ports/houji/runtime/usr/lib/systemd/user/houji-podman-storage.service').read_text()
    stage = (ROOT / 'ports/houji/stage-root.py').read_text()

    def test_the_file_is_generated_from_the_images_own_default(self):
        self.assertIn("'usr/lib/houji/containers-storage.conf'", self.stage)
        self.assertIn("target('usr/share/containers/storage.conf').read_text()", self.stage)
        self.assertIn('rootless_podman_storage_conf', self.stage)

    def test_the_service_gives_each_user_their_own_file_and_keeps_an_existing_one(self):
        self.assertIn('ConditionPathExists=!%h/.config/containers/storage.conf', self.unit)
        self.assertIn('/usr/lib/houji/containers-storage.conf %h/.config/containers/storage.conf', self.unit)
        self.assertIn('Type=oneshot', self.unit)
        self.assertIn('WantedBy=default.target', self.unit)
        # Copying root's ownership would fail for a user and leave the user manager degraded.
        self.assertNotIn('tmpfiles', self.unit)
        self.assertIn('install -D -m 0644', self.unit)

    def test_the_service_is_enabled_in_the_image(self):
        self.assertIn("link('etc/systemd/user/default.target.wants/houji-podman-storage.service',"
                      "'/usr/lib/systemd/user/houji-podman-storage.service')", self.stage)

    def test_lepton_needs_it_in_the_users_file_not_the_environment(self):
        # Lepton runs podman exec as `env -i podman ...`, which drops CONTAINERS_STORAGE_CONF.
        self.assertFalse((ROOT / 'ports/houji/runtime/usr/lib/environment.d/50-houji-podman.conf').exists())


if __name__ == '__main__':
    unittest.main()
