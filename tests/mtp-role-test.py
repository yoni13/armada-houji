#!/usr/bin/env python3
"""Exercise gadget role transitions against a disposable mock sysfs tree."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'system_files/usr/libexec/armada/mtp-gadget'


class Roles(unittest.TestCase):
    def exercise(self, policy, initial, after_bind, changed_role=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sys/class/udc/test.usb').mkdir(parents=True)
            role = root / 'sys/class/usb_role/test.usb-role-switch/role'
            role.parent.mkdir(parents=True)
            role.write_text(initial)
            udc = root / 'sys/kernel/config/usb_gadget/armada-mtp/UDC'
            udc.parent.mkdir(parents=True)
            udc.write_text('')
            (root / 'run/armada-mtp').mkdir(parents=True)
            script = SCRIPT.read_text()
            script = script.replace('source /usr/lib/armada/storage-lib', ':')
            script = script.replace('eval "$(/usr/libexec/armada/device-env)"', 'ARMADA_USB_ROLE_POLICY=' + policy)
            for path in ('/sys/', '/run/armada-mtp'):
                script = script.replace(path, str(root) + path)
            local = root / 'gadget'
            local.write_text(script)
            subprocess.run(['bash', local, 'bind'], check=True)
            self.assertEqual(role.read_text(), after_bind)
            self.assertEqual(udc.read_text(), 'test.usb')
            if changed_role is not None:
                role.write_text(changed_role)
            subprocess.run(['bash', local, 'unbind'], check=True)
            self.assertEqual(udc.read_text().strip(), '')
            expected = changed_role if policy == 'typec' and changed_role is not None else initial
            self.assertEqual(role.read_text(), expected)
            self.assertFalse((root / 'run/armada-mtp/previous-role').exists())

    def test_manual_device_selection_and_restore(self):
        self.exercise('manual', 'none', 'device')

    def test_typec_preserves_host(self):
        self.exercise('typec', 'host', 'host')

    def test_typec_preserves_changed_cable_role(self):
        self.exercise('typec', 'device', 'device', 'host')


if __name__ == '__main__':
    unittest.main()
