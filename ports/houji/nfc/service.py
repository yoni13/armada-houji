#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Persistent NFC modes, controlled by an authorized active local session."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time

from gi.repository import Gio, GLib

import controller
from protocol import validate

NAME = 'org.armada.Nfc'
PATH = '/org/armada/Nfc'
IFACE = 'org.armada.Nfc1'
XML = '''<node><interface name="org.armada.Nfc1">
  <method name="GetStatus"><arg type="s" direction="out"/></method>
  <method name="GetSettings"><arg type="s" direction="out"/></method>
  <method name="SaveTag"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="b" direction="in"/></method>
  <method name="SetReader"><arg type="b" direction="in"/></method>
  <method name="ScanAgain"/>
  <method name="StartEmulation"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
  <method name="StopEmulation"/>
</interface></node>'''
SETTINGS = Path('/var/lib/armada-nfc/settings.json')
DEFAULTS = dict(text='Armada NFC test', serial='12:34:56:78', custom_serial=False,
                reader_enabled=False, emulation_enabled=False)


def load_settings():
    if not SETTINGS.exists():
        return DEFAULTS.copy()
    if SETTINGS.stat().st_size > 4096:
        raise ValueError('Invalid saved settings')
    value = json.loads(SETTINGS.read_text())
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise ValueError('Invalid saved settings')
    validate(value['text'], value['serial'])
    if any(type(value[name]) is not bool for name in
           ['custom_serial', 'reader_enabled', 'emulation_enabled']):
        raise ValueError('Invalid saved settings')
    if value['custom_serial'] and not value['serial'].strip():
        raise ValueError('Invalid saved serial')
    return value


class Service:
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.hardware = threading.Lock()
        self.worker = None
        self.poll_worker = None
        self.last_poll = 0
        self.emulation = False
        self.closing = False
        self.settings_error = False
        try:
            self.settings = load_settings()
        except (OSError, ValueError, KeyError, TypeError):
            self.settings = DEFAULTS.copy()
            self.settings_error = True
        self.saved_settings = self.settings.copy()
        self.state = dict(mode='off', message='NFC is off', reads=0,
                          powered=False, polling=False, tags=0, busy=False)
        self.connection.register_object(PATH, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0],
                                        self.call, None, None)
        self.name_id = Gio.bus_own_name_on_connection(self.connection, NAME,
                         Gio.BusNameOwnerFlags.NONE, None, lambda *args: self.shutdown())
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.shutdown)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.shutdown)
        GLib.idle_add(self.restore)

    def save(self):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=SETTINGS.parent, delete=False) as stream:
                temporary = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                json.dump(self.settings, stream, ensure_ascii=True)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(SETTINGS)
            self.saved_settings = self.settings.copy()
        except OSError:
            self.settings = self.saved_settings.copy()
            raise
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def restore(self):
        if self.settings_error:
            self.state.update(mode='error', message='Saved NFC settings could not be read. Set them again in the app.')
        elif self.settings['emulation_enabled']:
            self.begin_emulation(self.settings['text'],
                    self.settings['serial'] if self.settings['custom_serial'] else '')
        elif self.settings['reader_enabled']:
            self.launch(lambda: controller.reader(True), 'Restoring NFC reader')
        else:
            self.poll()
        return False

    def begin_emulation(self, text, serial):
        self.emulation = True
        self.stop.clear()
        self.launch(lambda: self.run_emulation(text, serial), 'Preparing text tag')

    def authorized(self, sender):
        try:
            uid = self.connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                    'org.freedesktop.DBus', 'GetConnectionUnixUser', GLib.Variant('(s)', (sender,)),
                    GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
            if uid == 0:
                return True
            reply = self.connection.call_sync('org.freedesktop.PolicyKit1',
                    '/org/freedesktop/PolicyKit1/Authority', 'org.freedesktop.PolicyKit1.Authority',
                    'CheckAuthorization', GLib.Variant('((sa{sv})sa{ss}us)', (
                        ('system-bus-name', {'name': GLib.Variant('s', sender)}),
                        'org.armada.nfc.manage', {}, 0, '')),
                    GLib.VariantType.new('((bba{ss}))'), Gio.DBusCallFlags.NONE, 3000, None)
            return reply.unpack()[0][0]
        except GLib.Error:
            return False

    def call(self, connection, sender, path, interface, method, params, invocation):
        if method == 'GetStatus':
            self.poll()
            with self.lock:
                value = json.dumps(dict(self.state, emulation_enabled=self.settings['emulation_enabled']))
            invocation.return_value(GLib.Variant('(s)', (value,)))
            return
        if self.closing or not self.authorized(sender):
            invocation.return_dbus_error(NAME + '.NotAuthorized', 'Use NFC Manager in the active local session.')
            return
        try:
            if method == 'GetSettings':
                invocation.return_value(GLib.Variant('(s)', (json.dumps(self.settings),)))
                return
            if method == 'StopEmulation':
                self.settings['emulation_enabled'] = False
                self.save()
                self.stop.set()
                if not (self.worker and self.worker.is_alive()):
                    with self.lock:
                        self.state.update(mode='off', busy=False)
                    self.poll()
            elif method == 'SetReader' and self.emulation:
                self.settings.update(reader_enabled=params.unpack()[0], emulation_enabled=False)
                self.save()
                self.stop.set()
            elif self.worker and self.worker.is_alive():
                raise ValueError('NFC is busy. Wait for the current operation to finish.')
            elif method == 'StartEmulation':
                text, serial = params.unpack()
                validate(text, serial)
                self.settings.update(text=text, custom_serial=bool(serial.strip()), emulation_enabled=True)
                if serial.strip():
                    self.settings['serial'] = serial
                self.save()
                self.begin_emulation(text, serial)
            elif method == 'SaveTag':
                text, serial, custom = params.unpack()
                validate(text, serial)
                if custom and not serial.strip():
                    raise ValueError('Enter a four-byte serial or turn Custom serial off.')
                self.settings.update(text=text, serial=serial, custom_serial=custom)
                self.save()
            elif method == 'SetReader':
                enabled = params.unpack()[0]
                self.settings.update(reader_enabled=enabled, emulation_enabled=False)
                self.save()
                self.launch(lambda: controller.reader(enabled), 'Updating NFC reader')
            elif method == 'ScanAgain':
                self.settings.update(reader_enabled=True, emulation_enabled=False)
                self.save()
                self.launch(lambda: controller.reader(True, restart=True), 'Restarting scan')
            else:
                raise ValueError('Unknown NFC operation.')
        except ValueError as error:
            invocation.return_dbus_error(NAME + '.InvalidRequest', str(error))
            return
        except OSError:
            invocation.return_dbus_error(NAME + '.StorageError', 'Could not save NFC settings.')
            return
        invocation.return_value(GLib.Variant('()', ()))

    def changed(self, mode, message, reads):
        with self.lock:
            self.state.update(mode=mode, message=message, busy=True)
            if reads is not None:
                self.state['reads'] = reads

    def refresh(self):
        status = controller.reader_status()
        with self.lock:
            # A status read may have begun just before a user queued a change.
            if self.state['busy']:
                return
            self.state.update(status)
            if self.state['mode'] != 'error':
                mode = 'reader' if status['powered'] else 'off'
                message = ('Scanning for tags' if status['polling'] else
                           'Tag detected — use Scan again for another tag' if status['tags'] else
                           'Reader ready — use Scan again' if status['powered'] else 'NFC is off')
                self.state.update(mode=mode, message=message, busy=False)

    def launch(self, operation, message):
        self.changed('starting', message, 0)

        def run():
            with self.hardware:
                try:
                    operation()
                    with self.lock:
                        self.state.update(mode='off', busy=False)
                except Exception:
                    # Exceptions may contain transport details. Never log card data.
                    with self.lock:
                        self.state.update(mode='error', busy=False,
                            message='NFC operation failed. Turn NFC off and on, or restart the phone.')
                finally:
                    self.emulation = False
                    self.refresh()

        self.worker = threading.Thread(target=run, name='nfc-control', daemon=True)
        self.worker.start()

    def run_emulation(self, text, serial):
        controller.emulate(text, serial, self.stop, self.changed)
        if not self.closing:
            wanted = self.settings['reader_enabled']
            if controller.reader_status()['powered'] != wanted:
                controller.reader(wanted)

    def poll(self):
        if self.closing:
            return False
        if ((self.worker and self.worker.is_alive()) or
                (self.poll_worker and self.poll_worker.is_alive())):
            return True
        # A closed UI does not need periodic subprocesses or radio status reads.
        now = time.monotonic()
        if now - self.last_poll < 2:
            return True
        self.last_poll = now

        def run():
            with self.hardware:
                if not (self.worker and self.worker.is_alive()):
                    self.refresh()

        self.poll_worker = threading.Thread(target=run, name='nfc-status', daemon=True)
        self.poll_worker.start()
        return True

    def shutdown(self):
        self.closing = True
        self.stop.set()
        self.loop.quit()
        return False

    def run(self):
        self.loop.run()
        if self.worker:
            self.worker.join(60)
        if self.poll_worker:
            self.poll_worker.join(20)


if __name__ == '__main__':
    if sys.argv[1:] == ['--recover']:
        try:
            controller.recover()
        except Exception:
            print('NFC recovery failed; restart the phone.', file=sys.stderr)
            sys.exit(1)
    elif not sys.argv[1:]:
        Service().run()
    else:
        sys.exit(2)
