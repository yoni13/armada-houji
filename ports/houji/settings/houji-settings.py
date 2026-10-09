#!/usr/bin/python3
"""Root backend for the Houji Settings Decky plugin.

houji-settings.socket starts it for each connection from the plugin. It reads
one JSON request on stdin and writes one JSON reply to stdout. Decky
records every plugin call's arguments and result in Steam's on-disk JS log, so
replies carry no subscriber identifiers: eSIM profiles are named by per-boot
HMAC handles, and ModemManager and NetworkManager data is reduced to fixed
fields. eSIM activation codes and NFC tag settings use the plugin's private
loopback channel; the helper passes them through the root socket and D-Bus.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

ROTATION_LOCK = Path('/etc/armada/houji-rotation-lock')
ORIENTATION_STATE = Path('/run/houji-orientation/current')
CHARGE_LIMIT = Path('/etc/armada/houji-charge-limit')
CHARGE_STATE = Path('/run/houji-charging/charge-limit.json')
HANDLE_KEY = Path('/run/houji/settings-handle-key')
SIM_SELECTION = Path('/etc/armada/cellular-sim')
NFC_SETTINGS = Path('/var/lib/armada-nfc/settings.json')
NFC_NOTIFICATIONS = Path('/etc/armada/houji-nfc-notifications')
BATTERY = Path('/sys/class/power_supply/battery')
ORIENTATIONS = ('normal', 'left', 'right', 'upsidedown')
SIM_MODES = ('auto', 'physical1', 'physical2', 'esim')
CELLULAR = ('org.armada.Cellular', '/org/armada/Cellular', 'org.armada.Cellular1')
NFC = ('org.armada.Nfc', '/org/armada/Nfc', 'org.armada.Nfc1')
SETTINGS_PATH = re.compile(r'/org/freedesktop/NetworkManager/Settings/[0-9]+')
ICCID = re.compile(r'[0-9]{18,22}')


class Failure(Exception):
    """A message that is safe to show and to log."""


class System:
    """Commands and D-Bus calls; tests replace this."""

    def run(self, *args, timeout=20):
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)

    def dbus(self, service, method, signature=None, args=(), reply='(s)', start=True):
        from gi.repository import Gio, GLib
        name, path, interface = service
        flags = Gio.DBusCallFlags.NONE if start else Gio.DBusCallFlags.NO_AUTO_START
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        params = GLib.Variant(signature, args) if signature else None
        return bus.call_sync(name, path, interface, method, params, GLib.VariantType.new(reply),
                             flags, 15000, None).unpack()


def write_atomic(path, text):
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(text)
    temporary.chmod(0o644)
    temporary.replace(path)


def text(value, limit=64):
    return value[:limit] if isinstance(value, str) and value not in ('', '--') else None


# Display

def rotation_status():
    try:
        lock = ROTATION_LOCK.read_text().strip()
    except OSError:
        lock = None
    try:
        current = ORIENTATION_STATE.read_text().strip()
    except OSError:
        current = None
    return {'lock': lock if lock in ORIENTATIONS else None,
            'current': current if current in ORIENTATIONS else None}


def set_rotation(request):
    mode = request.get('mode')
    if mode == 'auto':
        ROTATION_LOCK.unlink(missing_ok=True)
    elif mode in ORIENTATIONS:
        write_atomic(ROTATION_LOCK, mode + '\n')
    else:
        raise Failure('Unknown orientation.')
    return rotation_status()


# Battery

def charging_status():
    try:
        limit = int(CHARGE_LIMIT.read_text())
        limit = limit if 50 <= limit < 100 else None
    except (OSError, ValueError):
        limit = None
    try:
        state = json.loads(CHARGE_STATE.read_text())
        holding = state.get('holding') is True and state.get('limit') == limit
    except (OSError, ValueError, AttributeError):
        holding = False
    try:
        capacity = int((BATTERY / 'capacity').read_text())
    except (OSError, ValueError):
        capacity = None
    try:
        status = (BATTERY / 'status').read_text().strip()
    except OSError:
        status = None
    return {'limit': limit, 'holding': holding, 'capacity': capacity, 'status': status}


def set_charge_limit(request):
    limit = request.get('limit')
    if limit is None or limit == 100:
        CHARGE_LIMIT.unlink(missing_ok=True)
    elif type(limit) is int and 50 <= limit < 100:
        write_atomic(CHARGE_LIMIT, f'{limit}\n')
    else:
        raise Failure('The charge limit must be between 50% and 100%.')
    return charging_status()


# NFC

def nfc_settings():
    try:
        value = json.loads(NFC_SETTINGS.read_text())
    except (OSError, ValueError):
        return False, False
    if not isinstance(value, dict):
        return False, False
    return value.get('reader_enabled') is True, value.get('emulation_enabled') is True


def nfc_status(system):
    reader, emulating = nfc_settings()
    status = {}
    try:
        # Reading status must not start the NFC service just because a menu opened.
        status = json.loads(system.dbus(NFC, 'GetStatus', start=False)[0])
    except Exception:
        pass
    modes = ('off', 'reader', 'starting', 'emulating', 'stopping', 'error', 'ese')
    def count(name):
        value = status.get(name)
        return value if type(value) is int and value >= 0 else 0
    return {'enabled': reader or emulating, 'emulating': emulating,
            'desired_mode': 'emulation' if emulating else 'reader' if reader else 'off',
            'mode': status.get('mode') if status.get('mode') in modes else 'unavailable',
            'powered': status.get('powered') is True, 'polling': status.get('polling') is True,
            'reads': count('reads'), 'tags': count('tags'),
            'notify_on_scan': nfc_notifications_enabled(),
            'detections': count('detections'),
            'detection_session': text(status.get('detection_session'), 32),
            'busy': status.get('busy') is True, 'message': text(status.get('message'), 120)}


def nfc_notifications_enabled():
    try:
        return NFC_NOTIFICATIONS.read_text().strip() == '1'
    except OSError:
        return False


def set_nfc_notifications(system, request):
    enabled = request.get('enabled')
    if type(enabled) is not bool:
        raise Failure('Invalid scan notification setting.')
    write_atomic(NFC_NOTIFICATIONS, '1\n' if enabled else '0\n')
    return nfc_status(system)


def nfc_watch(system, request):
    # Closed-panel monitoring avoids modem/NM reads and does not query NFC at
    # all when the owner has disabled scan notifications.
    if not nfc_notifications_enabled():
        return {'enabled': False}
    status = nfc_status(system)
    return {'enabled': True, 'reader': status['desired_mode'] == 'reader',
            'session': status['detection_session'], 'detections': status['detections']}


def set_nfc(system, request):
    enabled = request.get('enabled')
    if type(enabled) is not bool:
        raise Failure('Invalid NFC request.')
    try:
        if not enabled and nfc_settings()[1]:
            system.dbus(NFC, 'StopEmulation', reply='()')
        system.dbus(NFC, 'SetReader', '(b)', (enabled,), reply='()')
    except Exception:
        raise Failure('NFC is busy. Try again in a moment.') from None
    return nfc_status(system)


def nfc_tag_settings(system, request):
    """Private-channel reply only: do not expose tag data through a Decky call."""
    try:
        saved = json.loads(system.dbus(NFC, 'GetSettings')[0])
        return validate_nfc_tag(saved)
    except Failure:
        raise
    except Exception:
        raise Failure('Could not load the saved NFC tag.') from None


def validate_nfc_tag(request):
    value, serial, custom = request.get('text'), request.get('serial'), request.get('custom_serial')
    if not isinstance(value, str) or not value.strip():
        raise Failure('Enter some text for the tag.')
    if '\x00' in value or len(value.encode('utf-8')) > 200:
        raise Failure('Tag text must be at most 200 UTF-8 bytes and contain no NULs.')
    if type(custom) is not bool or not isinstance(serial, str) or len(serial) > 32:
        raise Failure('Invalid NFC tag settings.')
    compact = re.sub(r'[: -]', '', serial)
    if (custom or serial.strip()) and not re.fullmatch(r'[0-9a-fA-F]{8}', compact):
        raise Failure('Enter a four-byte hexadecimal serial or choose automatic serial.')
    return {'text': value, 'serial': serial, 'custom_serial': custom}


def nfc_tag_update(system, request):
    action = request.get('action')
    if action not in ('save', 'start'):
        raise Failure('Unknown NFC tag action.')
    saved = validate_nfc_tag(request)
    try:
        if action == 'save':
            system.dbus(NFC, 'SaveTag', '(ssb)',
                        (saved['text'], saved['serial'], saved['custom_serial']), reply='()')
        else:
            system.dbus(NFC, 'StartEmulation', '(ss)',
                        (saved['text'], saved['serial'] if saved['custom_serial'] else ''), reply='()')
    except Exception:
        raise Failure('NFC is busy or unavailable. Stop emulation before editing or restarting it.') from None
    return nfc_status(system)


def nfc_action(system, request):
    action = request.get('action')
    if action == 'start_saved':
        return nfc_tag_update(system, dict(nfc_tag_settings(system, {}), action='start'))
    if action not in ('stop', 'scan'):
        raise Failure('Unknown NFC action.')
    try:
        system.dbus(NFC, 'StopEmulation' if action == 'stop' else 'ScanAgain', reply='()')
    except Exception:
        raise Failure('NFC is busy or unavailable. Try again in a moment.') from None
    return nfc_status(system)


# Mobile network

def selection():
    try:
        value = SIM_SELECTION.read_text().strip()
    except OSError:
        return 'auto'
    return value if value in SIM_MODES else 'auto'


def handle_key():
    try:
        return HANDLE_KEY.read_bytes()
    except FileNotFoundError:
        pass
    HANDLE_KEY.parent.mkdir(exist_ok=True)
    temporary = HANDLE_KEY.with_name(f'.{HANDLE_KEY.name}.{os.getpid()}')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(secrets.token_bytes(32))
    try:
        os.link(temporary, HANDLE_KEY)
    except FileExistsError:
        pass
    finally:
        temporary.unlink()
    return HANDLE_KEY.read_bytes()


def profile_handle(key, iccid):
    return hmac.new(key, iccid.encode(), hashlib.sha256).hexdigest()[:16]


def service_state(system):
    try:
        state = json.loads(system.dbus(CELLULAR, 'Status')[0])
    except Exception:
        return None
    return state if isinstance(state, dict) else None


def esim_profiles(state):
    profiles = state.get('profiles') if state else None
    return [p for p in profiles or [] if isinstance(p, dict) and ICCID.fullmatch(str(p.get('iccid', '')))]


def modem_status(system):
    try:
        result = system.run('mmcli', '-J', '-m', 'any', timeout=10)
        modem = json.loads(result.stdout)['modem'] if not result.returncode else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        modem = None
    if not isinstance(modem, dict):
        return None
    generic = modem.get('generic') or {}
    gpp = modem.get('3gpp') or {}
    reason = text(generic.get('state-failed-reason'), 40)
    try:
        signal = int(generic['signal-quality']['value'])
    except (KeyError, TypeError, ValueError):
        signal = None
    technologies = [t for t in generic.get('access-technologies') or [] if isinstance(t, str)]
    registration = text(gpp.get('registration-state'), 20)
    return {'state': text(generic.get('state'), 20),
            'reason': reason if reason and re.fullmatch(r'[a-z-]+', reason) else None,
            'signal': signal, 'technology': ', '.join(technologies)[:40] or None,
            'operator': text(gpp.get('operator-name'), 40),
            'registered': registration in ('home', 'roaming'), 'roaming': registration == 'roaming'}


def terse_records(output):
    records, current = [], {}
    for line in output.splitlines():
        if not line.strip():
            if current:
                records.append(current)
            current = {}
            continue
        key, _, value = line.partition(':')
        current[key] = current.get(key, '') + ' ' + value.replace('\\:', ':')
    if current:
        records.append(current)
    return [{key: value.strip() for key, value in record.items()} for record in records]


def nmcli(system, *args, timeout=20):
    try:
        result = system.run('nmcli', *args, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        raise Failure('NetworkManager did not respond.') from None
    if result.returncode:
        raise Failure('NetworkManager rejected the change.')
    return result.stdout


def modem_connections(system):
    """The modem device and the data profiles NetworkManager offers for its SIM."""
    output = nmcli(system, '-t', '-f', 'GENERAL.DEVICE,GENERAL.TYPE,GENERAL.STATE,GENERAL.AUTOCONNECT,'
                   'GENERAL.CON-UUID,CONNECTIONS.AVAILABLE-CONNECTION-PATHS', 'device', 'show')
    device = next((r for r in terse_records(output) if r.get('GENERAL.TYPE') == 'gsm'), None)
    if not device:
        return None, []
    connections = []
    paths = SETTINGS_PATH.findall(' '.join(v for k, v in device.items()
                                           if k.startswith('CONNECTIONS.AVAILABLE-CONNECTION-PATHS')))
    for path in paths:
        values = dict(line.partition(':')[::2] for line in nmcli(
            system, '-t', '-f', 'connection.uuid,connection.timestamp,connection.autoconnect,gsm.home-only',
            'connection', 'show', 'path', path).splitlines())
        try:
            timestamp = int(values.get('connection.timestamp') or 0)
        except ValueError:
            timestamp = 0
        connections.append({'uuid': values.get('connection.uuid', ''), 'timestamp': timestamp,
                            'autoconnect': values.get('connection.autoconnect') == 'yes',
                            'home_only': values.get('gsm.home-only') == 'yes'})
    connections = [c for c in connections if c['uuid']]
    try:
        state = int(device.get('GENERAL.STATE', '0').split()[0])
    except (ValueError, IndexError):
        state = 0
    return {'name': device.get('GENERAL.DEVICE', ''), 'state': state,
            'autoconnect': device.get('GENERAL.AUTOCONNECT') == 'yes',
            'active': device.get('GENERAL.CON-UUID', '')}, connections


def target_connection(device, connections):
    """The active profile, else the one used most recently, as Plasma Mobile picks."""
    active = [c for c in connections if c['uuid'] == device['active']]
    return active[0] if active else max(connections, key=lambda c: c['timestamp'], default=None)


def data_status(system):
    try:
        device, connections = modem_connections(system)
    except Failure:
        device, connections = None, []
    if not device:
        return {'modem': False, 'ready': False, 'profile': False, 'enabled': False,
                'connected': False, 'roaming_allowed': None}
    target = target_connection(device, connections)
    return {'modem': True, 'ready': device['state'] >= 30, 'profile': target is not None,
            'enabled': device['autoconnect'] and any(c['autoconnect'] for c in connections),
            'connected': device['state'] == 100,
            'roaming_allowed': None if target is None else not target['home_only']}


def require_profile(system):
    device, connections = modem_connections(system)
    if not device:
        raise Failure('No cellular modem is available.')
    target = target_connection(device, connections)
    if target is None:
        raise Failure('This SIM has no mobile data profile yet. Add its APN in Desktop Mode '
                      'under Settings, Cellular Network.')
    return device, connections, target


def set_data(system, request):
    enabled = request.get('enabled')
    if type(enabled) is not bool:
        raise Failure('Invalid mobile data request.')
    device, connections, target = require_profile(system)
    # Plasma Mobile's order: block autoconnect while profiles change, then act.
    nmcli(system, 'device', 'set', device['name'], 'autoconnect', 'no')
    if enabled:
        if nmcli(system, '-t', 'radio', 'wwan').strip() != 'enabled':
            nmcli(system, 'radio', 'wwan', 'on')
        latest = max(connections, key=lambda c: c['timestamp'])
        for connection in connections:
            nmcli(system, 'connection', 'modify', 'uuid', connection['uuid'],
                  'connection.autoconnect', 'yes' if connection is latest else 'no')
        nmcli(system, '--wait', '0', 'connection', 'up', 'uuid', latest['uuid'], 'ifname', device['name'])
        nmcli(system, 'device', 'set', device['name'], 'autoconnect', 'yes')
    else:
        for connection in connections:
            nmcli(system, 'connection', 'modify', 'uuid', connection['uuid'], 'connection.autoconnect', 'no')
        if device['state'] > 30:
            nmcli(system, 'device', 'disconnect', device['name'])
    return data_status(system)


def set_roaming(system, request):
    allowed = request.get('allowed')
    if type(allowed) is not bool:
        raise Failure('Invalid roaming request.')
    device, _, target = require_profile(system)
    nmcli(system, 'connection', 'modify', 'uuid', target['uuid'], 'gsm.home-only', 'no' if allowed else 'yes')
    if target['uuid'] == device['active']:
        # An active connection keeps its old settings until it is activated again.
        nmcli(system, '--wait', '0', 'connection', 'up', 'uuid', target['uuid'], 'ifname', device['name'])
    return data_status(system)


def cellular_status(system):
    try:
        enabled = system.run('systemctl', 'is-active', '--quiet', 'houji-cellular', timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        enabled = False
    state = service_state(system) if enabled else None
    profiles = []
    if state:
        key = handle_key()
        for profile in esim_profiles(state):
            provider = text(profile.get('serviceProviderName'))
            profiles.append({'handle': profile_handle(key, profile['iccid']),
                             'name': text(profile.get('profileNickname')) or text(profile.get('profileName'))
                             or provider or 'eSIM profile',
                             'nickname': text(profile.get('profileNickname')) or '',
                             'provider': provider, 'enabled': profile.get('profileState') == 'enabled'})
    return {'enabled': enabled, 'selection': selection(), 'service': state is not None,
            'busy': bool(state and state.get('busy') is True),
            'message': text(state.get('message'), 120) if state else None,
            'profiles': profiles, 'modem': modem_status(system) if enabled else None,
            'data': data_status(system) if enabled else None}


def cellular_run(system, operation, value=''):
    try:
        system.dbus(CELLULAR, 'Run', '(ss)', (operation, value), reply='()')
    except Exception:
        # The service's errors are generic, and D-Bus text can echo arguments.
        raise Failure('The cellular service is busy or not ready. Try again in a moment.') from None


def require_esim():
    if selection() != 'esim':
        raise Failure('Choose eSIM as the data SIM first.')


def enable_cellular(system, request):
    cellular_run(system, 'enable')
    return cellular_status(system)


def select_sim(system, request):
    mode = request.get('mode')
    if mode not in SIM_MODES:
        raise Failure('Unknown SIM choice.')
    cellular_run(system, 'select', mode)
    return cellular_status(system)


def refresh_profiles(system, request):
    require_esim()
    cellular_run(system, 'list')
    return cellular_status(system)


def change_profile(system, request):
    action, handle = request.get('action'), request.get('handle')
    if action not in ('enable', 'disable', 'delete', 'nickname') or not isinstance(handle, str):
        raise Failure('Invalid eSIM profile request.')
    require_esim()
    key = handle_key()
    matches = [p['iccid'] for p in esim_profiles(service_state(system))
               if hmac.compare_digest(profile_handle(key, p['iccid']), handle)]
    if len(matches) != 1:
        raise Failure('That eSIM profile is no longer listed. Refresh the list and try again.')
    if action == 'nickname':
        nickname = request.get('nickname')
        if (not isinstance(nickname, str) or len(nickname.strip().encode()) > 64
                or not nickname.strip().isprintable()):
            raise Failure('Use up to 64 characters for the name.')
        cellular_run(system, 'profile-nickname', matches[0] + ':' + nickname.strip())
    else:
        cellular_run(system, 'profile-' + action, matches[0])
    return cellular_status(system)


def download_profile(system, request):
    code = request.get('code')
    if not isinstance(code, str):
        raise Failure('Enter an activation code.')
    code = code.strip()
    # The service's own rule; its answer to a bad code is only "invalid request".
    if not code.startswith('LPA:1$') or not 12 <= len(code) <= 2048 or any(c.isspace() for c in code):
        raise Failure('An activation code starts with LPA:1$ and has no spaces.')
    require_esim()
    cellular_run(system, 'download', code)
    return cellular_status(system)


def status(system, request):
    return {'rotation': rotation_status(), 'charging': charging_status(),
            'cellular': cellular_status(system), 'nfc': nfc_status(system)}


OPERATIONS = {
    'status': status,
    'rotation.set': lambda system, request: set_rotation(request),
    'charge_limit.set': lambda system, request: set_charge_limit(request),
    'nfc.set': set_nfc,
    'nfc.action': nfc_action,
    'nfc.notifications': set_nfc_notifications,
    'nfc.watch': nfc_watch,
    'nfc.tag.get': nfc_tag_settings,
    'nfc.tag.update': nfc_tag_update,
    'cellular.enable': enable_cellular,
    'sim.select': select_sim,
    'esim.refresh': refresh_profiles,
    'esim.profile': change_profile,
    'esim.download': download_profile,
    'data.set': set_data,
    'roaming.set': set_roaming,
}


def handle(system, raw):
    try:
        request = json.loads(raw)
        operation = OPERATIONS.get(request.get('op')) if isinstance(request, dict) else None
        if operation is None:
            raise Failure('Unknown request.')
        return {'ok': True, 'result': operation(system, request)}
    except Failure as error:
        return {'ok': False, 'error': str(error)}
    except Exception:
        # Details can name SIMs or connections, and Decky logs this reply.
        return {'ok': False, 'error': 'The setting could not be changed.'}


def main():
    if os.geteuid():
        raise SystemExit('houji-settings must run as root (Decky runs it for Houji Settings).')
    reply = handle(System(), sys.stdin.read(65536))
    sys.stdout.write(json.dumps(reply) + '\n')


if __name__ == '__main__':
    main()
