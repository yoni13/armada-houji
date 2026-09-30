#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small touch-friendly NFC manager; all hardware access is via Polkit/D-Bus."""
import json
import os

# This small controls window does not need a Vulkan/OpenGL rendering context.
os.environ.setdefault('GSK_RENDERER', 'cairo')
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from protocol import validate

NAME = 'org.armada.Nfc'
PATH = '/org/armada/Nfc'
IFACE = 'org.armada.Nfc1'
NOTICE = 'Settings are saved on this phone. Emulation continues in the background and after reboot until you stop it.'


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id='org.armada.NfcManager', flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.connect('activate', self.activate)
        self.window = None
        self.connection = None
        self.closed = False
        self.updating = False
        self.request_pending = False
        self.status_pending = False
        self.settings_ready = False
        self.dirty = False
        self.save_source = None

    def activate(self, app):
        if self.window:
            self.window.present()
            return
        self.window = Adw.ApplicationWindow(application=self, title='NFC Manager',
                                            default_width=420, default_height=660)
        self.window.connect('close-request', self.close)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title='NFC Manager', subtitle='Xiaomi 14'))
        box.append(header)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=540, tightening_threshold=400)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                         margin_top=20, margin_bottom=24, margin_start=16, margin_end=16)
        clamp.set_child(content)
        scroll.set_child(clamp)
        box.append(scroll)
        self.window.set_content(box)

        group = Adw.PreferencesGroup(title='NFC reader')
        self.reader = Adw.SwitchRow(title='Read nearby tags', subtitle='Hold a tag against the upper back of the phone')
        self.reader.connect('notify::active', self.reader_changed)
        group.add(self.reader)
        self.status = Adw.ActionRow(title='Connecting to NFC service…')
        self.status.set_title_lines(3)
        self.status.set_subtitle_lines(2)
        group.add(self.status)
        self.scan = Gtk.Button(label='Scan again', valign=Gtk.Align.CENTER)
        self.scan.connect('clicked', lambda *_: self.call('ScanAgain'))
        self.status.add_suffix(self.scan)
        content.append(group)

        group = Adw.PreferencesGroup(title='Emulate a text tag',
            description='Present a read-only NFC text tag to another phone. Reading pauses during emulation.')
        self.text = Adw.EntryRow(title='Tag text (up to 200 UTF-8 bytes)')
        self.text.set_text('Armada NFC test')
        self.text.connect('changed', self.edited)
        group.add(self.text)
        self.custom = Adw.SwitchRow(title='Custom serial', subtitle='Four-byte NFC-A identifier; otherwise automatic')
        self.custom.connect('notify::active', lambda *_: self.serial.set_visible(self.custom.get_active()))
        self.custom.connect('notify::active', self.edited)
        group.add(self.custom)
        self.serial = Adw.EntryRow(title='Serial (hexadecimal)')
        self.serial.set_text('12:34:56:78')
        self.serial.connect('changed', self.edited)
        self.serial.set_visible(False)
        group.add(self.serial)
        content.append(group)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, homogeneous=True)
        self.start = Gtk.Button(label='Emulate text tag')
        self.start.add_css_class('suggested-action')
        self.start.connect('clicked', self.emulate)
        self.stop = Gtk.Button(label='Stop emulation', sensitive=False)
        self.stop.connect('clicked', lambda *_: self.call('StopEmulation'))
        buttons.append(self.start)
        buttons.append(self.stop)
        content.append(buttons)
        self.message = Gtk.Label(label=NOTICE, wrap=True, xalign=0)
        self.message.add_css_class('dim-label')
        content.append(self.message)
        for widget in (self.reader, self.scan, self.start, self.text, self.custom, self.serial):
            widget.set_sensitive(False)
        self.window.present()
        Gio.bus_get(Gio.BusType.SYSTEM, None, self.connected)

    def connected(self, source, result):
        try:
            self.connection = Gio.bus_get_finish(result)
        except GLib.Error:
            self.message.set_text('The system bus is unavailable.')
            return
        def loaded(connection, result):
            try:
                settings = json.loads(connection.call_finish(result).unpack()[0])
                self.updating = True
                self.text.set_text(settings['text'])
                self.serial.set_text(settings['serial'])
                self.custom.set_active(settings['custom_serial'])
                self.settings_ready = True
            except (GLib.Error, ValueError, KeyError):
                self.message.set_text('Could not load settings. Open this app from the active local desktop.')
            finally:
                self.updating = False
            self.poll()
            GLib.timeout_add_seconds(1, self.poll)

        self.connection.call(NAME, PATH, IFACE, 'GetSettings', None, GLib.VariantType.new('(s)'),
                             Gio.DBusCallFlags.NONE, 8000, None, loaded)

    def tag_settings(self):
        text, serial, custom = self.text.get_text(), self.serial.get_text(), self.custom.get_active()
        validate(text, serial)
        if custom and not serial.strip():
            raise ValueError('Enter a four-byte serial or turn Custom serial off.')
        return (text, serial, custom)

    def edited(self, *_):
        if self.updating or not self.settings_ready:
            return
        self.dirty = True
        if self.save_source:
            GLib.source_remove(self.save_source)
        self.save_source = GLib.timeout_add(400, self.save_tag)

    def save_tag(self):
        if self.request_pending:
            return True
        self.save_source = None
        if not self.dirty:
            return False
        try:
            values = self.tag_settings()
        except ValueError as error:
            self.message.set_text(str(error))
            return False

        def saved():
            self.dirty = (self.text.get_text(), self.serial.get_text(), self.custom.get_active()) != values

        self.call('SaveTag', GLib.Variant('(ssb)', values), saved)
        return False

    def reader_changed(self, row, _param):
        if not self.updating:
            self.call('SetReader', GLib.Variant('(b)', (row.get_active(),)))

    def emulate(self, *_):
        text = self.text.get_text()
        serial = self.serial.get_text() if self.custom.get_active() else ''
        try:
            validate(text, serial)
            if self.custom.get_active() and not serial.strip():
                raise ValueError('Enter a four-byte serial or turn Custom serial off.')
        except ValueError as error:
            self.message.set_text(str(error))
            return
        if self.save_source:
            GLib.source_remove(self.save_source)
            self.save_source = None
        self.call('StartEmulation', GLib.Variant('(ss)', (text, serial)),
                  lambda: setattr(self, 'dirty', False))

    def call(self, method, parameters=None, on_success=None):
        if not self.connection or self.request_pending:
            return
        self.request_pending = True
        for widget in (self.reader, self.scan, self.start, self.stop):
            widget.set_sensitive(False)

        def done(connection, result):
            self.request_pending = False
            try:
                connection.call_finish(result)
                self.message.set_text(NOTICE)
                if on_success:
                    on_success()
            except GLib.Error:
                self.message.set_text('Could not apply the change. Use the active local session and wait for NFC to be ready.')
            self.poll()

        self.connection.call(NAME, PATH, IFACE, method, parameters, None,
                             Gio.DBusCallFlags.NONE, 8000, None, done)

    def poll(self):
        if self.closed:
            return False
        if self.status_pending or not self.connection:
            return True
        self.status_pending = True

        def done(connection, result):
            self.status_pending = False
            if self.closed:
                return
            try:
                status = json.loads(connection.call_finish(result).unpack()[0])
                self.update(status)
            except (GLib.Error, ValueError, KeyError):
                self.status.set_title('NFC service unavailable')
                self.status.set_subtitle('Try reopening the app or restarting the phone.')
                for widget in (self.reader, self.scan, self.start, self.stop):
                    widget.set_sensitive(False)

        self.connection.call(NAME, PATH, IFACE, 'GetStatus', None, GLib.VariantType.new('(s)'),
                             Gio.DBusCallFlags.NONE, 8000, None, done)
        return True

    def update(self, status):
        busy = status['busy']
        emulating = status['mode'] == 'emulating'
        self.updating = True
        self.reader.set_active(status['powered'] and not busy)
        self.updating = False
        ready = self.settings_ready and not busy and not self.request_pending
        self.reader.set_sensitive(ready)
        self.scan.set_sensitive(ready and status['powered'])
        self.start.set_sensitive(ready)
        self.stop.set_sensitive(status.get('emulation_enabled', emulating) and not self.request_pending)
        for widget in (self.text, self.custom, self.serial):
            widget.set_sensitive(ready)
        self.status.set_title(status['message'])
        self.status.set_subtitle(f"Text read requests: {status['reads']}" if emulating else
                                 f"Tags detected: {status['tags']}" if status['powered'] else '')

    def close(self, *_):
        self.closed = True
        if self.save_source:
            GLib.source_remove(self.save_source)
            self.save_source = None
        # Keep the process alive briefly to flush an edit made just before close.
        # Radio operation belongs to the system service and continues separately.
        if self.dirty and self.connection and self.settings_ready:
            try:
                values = self.tag_settings()
            except ValueError:
                return False
            self.hold()

            def saved(connection, result):
                try:
                    connection.call_finish(result)
                except GLib.Error:
                    pass
                finally:
                    self.release()

            self.connection.call(NAME, PATH, IFACE, 'SaveTag', GLib.Variant('(ssb)', values), None,
                                 Gio.DBusCallFlags.NONE, 8000, None, saved)
        return False


if __name__ == '__main__':
    Application().run()
