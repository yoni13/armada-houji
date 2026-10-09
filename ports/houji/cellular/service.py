#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Authorized, serialized SIM/eSIM operations for Plasma Mobile Settings."""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from gi.repository import Gio, GLib

NAME = 'org.armada.Cellular'
PATH = '/org/armada/Cellular'
IFACE = NAME+'1'
ROOT = Path('/usr/libexec/houji-cellular')
XML = f'''<node><interface name="{IFACE}">
<method name="Status"><arg type="s" direction="out"/></method>
<method name="Run"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
</interface></node>'''
OPS = {'enable', 'select', 'list', 'download', 'profile-enable', 'profile-disable', 'profile-delete',
       'profile-nickname'}


def validate(operation, value):
    if operation not in OPS:
        raise ValueError('Unknown operation')
    if operation == 'select':
        if value not in ('auto', 'physical1', 'physical2', 'esim'):
            raise ValueError('Invalid SIM selection')
    elif operation == 'profile-nickname':
        # ICCID:nickname. An empty nickname clears it; SGP.22 allows 64 bytes.
        iccid, separator, nickname = value.partition(':')
        if (not separator or not re.fullmatch(r'[0-9]{18,22}', iccid)
                or len(nickname.encode()) > 64 or not nickname.isprintable()
                or nickname != nickname.strip()):
            raise ValueError('Invalid profile nickname')
    elif operation.startswith('profile-'):
        if not re.fullmatch(r'[0-9]{18,22}', value):
            raise ValueError('Invalid profile identifier')
    elif operation == 'download':
        if not value.startswith('LPA:1$') or not 12 <= len(value) <= 2048 or any(c.isspace() for c in value):
            raise ValueError('Invalid activation code')
    elif value:
        raise ValueError('Unexpected argument')


def command(*args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=kwargs.pop('timeout', 60), **kwargs)
    if result.returncode:
        raise RuntimeError('Operation failed; check the cellular service status.')
    return result.stdout


class Service:
    def __init__(self):
        self.state = dict(busy=False, message='', profiles=[], selection='auto')
        self.connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.connection.register_object(PATH, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], self.call, None, None)
        self.owner = Gio.bus_own_name_on_connection(self.connection, NAME, Gio.BusNameOwnerFlags.NONE, None, None)

    def authorized(self, sender):
        reply = self.connection.call_sync('org.freedesktop.PolicyKit1',
            '/org/freedesktop/PolicyKit1/Authority', 'org.freedesktop.PolicyKit1.Authority',
            'CheckAuthorization', GLib.Variant('((sa{sv})sa{ss}us)', (
                ('system-bus-name', {'name': GLib.Variant('s', sender)}),
                'org.armada.cellular.manage', {}, 0, '')),
            GLib.VariantType.new('((bba{ss}))'), Gio.DBusCallFlags.NONE, 3000, None)
        return reply.unpack()[0][0]

    def call(self, connection, sender, path, interface, method, params, invocation):
        try:
            if not self.authorized(sender):
                invocation.return_dbus_error(NAME+'.NotAuthorized', 'Use Settings in the active local session.')
                return
            if method == 'Status':
                saved = Path('/etc/armada/cellular-sim')
                self.state['selection'] = saved.read_text().strip() if saved.exists() else 'auto'
                invocation.return_value(GLib.Variant('(s)', (json.dumps(self.state),)))
                return
            operation, value = params.unpack()
            validate(operation, value)
            if self.state['busy']:
                raise ValueError('A SIM operation is already running')
            self.state.update(busy=True, message='Working…')
            threading.Thread(target=self.work, args=(operation, value), daemon=True).start()
            invocation.return_value(None)
        except (ValueError, GLib.Error, OSError):
            invocation.return_dbus_error(NAME+'.Failed', 'Invalid request or service unavailable.')

    def finished(self, message, profiles):
        self.state.update(busy=False, message=message)
        if profiles is not None:
            self.state['profiles'] = profiles
        return False

    def work(self, operation, value):
        profiles = None
        restart_mm = False
        message = 'Done'
        try:
            Path('/run/houji').mkdir(exist_ok=True)
            with open('/run/houji/cellular-control.lock', 'w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if operation == 'enable':
                    command('systemctl', 'stop', 'ModemManager')
                    restart_mm = True
                    command('systemctl', 'enable', '--now', 'houji-cellular.service', timeout=120)
                    command('systemctl', 'start', 'ModemManager')
                    restart_mm = False
                else:
                    command('systemctl', 'is-active', '--quiet', 'houji-cellular.service')
                    command('systemctl', 'stop', 'ModemManager')
                    restart_mm = True
                    if operation == 'select':
                        command(str(ROOT/'sim.py'), value, '--save')
                    else:
                        command(str(ROOT/'sim.py'), 'esim', '--save')
                        env = {k:v for k,v in os.environ.items() if not k.startswith(('LIBEUICC_', 'LPAC_'))}
                        env.update(LPAC_APDU='qmi_qrtr', LPAC_APDU_QMI_UIM_SLOT='2',
                                   LPAC_CUSTOM_ES10X_MSS='255', LPAC_HTTP='curl')
                        args = ['profile', 'list']
                        stdin = None
                        if operation == 'download':
                            args = ['profile', 'download', '-a', '-']
                            stdin = value+'\n'
                        elif operation == 'profile-nickname':
                            args = ['profile', 'nickname', *value.split(':', 1)]
                        elif operation.startswith('profile-'):
                            args = ['profile', operation[8:], value]
                        command(str(ROOT/'lpac'), *args, env=env, input=stdin, timeout=300)
                        if operation not in ('download', 'list', 'profile-nickname'):
                            command(str(ROOT/'sim.py'), 'refresh')
                        # A nickname is local to the eUICC; it sends the carrier nothing.
                        if operation not in ('list', 'profile-nickname'):
                            # Delivery can be retried; never undo a successful profile operation.
                            try:
                                command(str(ROOT/'lpac'), 'notification', 'process', '-a', '-r', env=env, timeout=90)
                            except RuntimeError:
                                message = 'Profile updated; carrier notification is pending.'
                        data = command(str(ROOT/'lpac'), 'profile', 'list', env=env)
                        records = [json.loads(line) for line in data.splitlines()]
                        result = next(r['payload'] for r in records if r.get('type') == 'lpa')
                        if result['code']:
                            raise RuntimeError('Could not list profiles')
                        profiles = [{key: item.get(key, '') for key in
                                     ('iccid','profileName','profileNickname','serviceProviderName',
                                      'profileState')}
                                    for item in result['data']]
                if restart_mm:
                    command('systemctl', 'start', 'ModemManager')
                    restart_mm = False
        except Exception:
            # Raw subprocess/HTTP/APDU errors can contain activation codes or IDs.
            message = 'Operation failed. Check cellular readiness and Wi-Fi, then retry.'
        finally:
            if restart_mm:
                try:
                    subprocess.run(['systemctl','start','ModemManager'], capture_output=True, timeout=60)
                except (OSError, subprocess.TimeoutExpired):
                    message = 'SIM operation ended; ModemManager needs to be started again.'
            GLib.idle_add(self.finished, message, profiles)


if __name__ == '__main__':
    service = Service()
    GLib.MainLoop().run()
