#!/usr/bin/python3
"""Conservative Houji USB-PD charging policy for the stock ADSP firmware.

HyperOS reports VERIFY_PROCESS=0 on verification failure as well as completion.
When installed, the hash-pinned stock agent performs real battery/adapter
authentication. Charging uses 500 mA on incomplete telemetry and a leased,
gradual ramp when all sensors and authentication pass. The kernel staging
ceiling is configured separately for normal PD and authenticated HyperCharge.
Rated 90 W input power is not inferred from the adapter's advertised capability.
"""
from pathlib import Path
import glob
import subprocess
import sys
import time
from collections import deque
from board_thermal import read_temperatures, virtual_temperature
from stock_thermal import WiredThermal, WirelessThermal, SicThermal

BAT = Path('/sys/class/power_supply/qcom-battmgr-bat')
USB = Path('/sys/class/power_supply/qcom-battmgr-usb')
WLS = Path('/sys/class/power_supply/qcom-battmgr-wls')
AUX = '/sys/bus/auxiliary/devices/pmic_glink.power-supply.*/houji_charger_state'
FCC_UA = 500_000
AUTH_AGENT = Path('/usr/libexec/armada/houji-stock-auth')
AUTH_BLOB = Path('/usr/lib/armada/houji/charging/batterysecret')


def configured_ceiling(path, default):
    value = int(path.read_text()) if path.exists() else default
    if not 1_000_000 <= value <= 15_600_000:
        raise ValueError('charging ceiling outside stock range')
    return value


def wired_limits(directory, usb_type, authentication, normal, hypercharge):
    """Return (FCC ceiling, stock fast-charge temperature range enabled)."""
    if ('[PD_PPS]' not in usb_type
            or authentication.get('battery_authentic') != 1
            or authentication.get('pd_verified') != 1):
        return normal, False
    try:
        # XM55 is the firmware's fast-charge mode, not an enable switch.
        # XM61 is advertised APDO power in W, not measured charging power.
        # Require the native-suspend guard before permitting this higher cap.
        read = lambda name: int((directory / name).read_text())
        if (read('fcc_suspend_guard') == 1 and read('fastchg_mode') == 1
                and read('apdo_max') >= 50):
            # Older kernels retain their 38 C high-current gate. Only opt in
            # when the kernel independently checks authentication, mode and
            # health on every FCC write over the stock normal range.
            try:
                stock_range = read('fcc_fastcharge_temp_max') == 470
            except (OSError, ValueError):
                stock_range = False
            return max(normal, hypercharge), stock_range
    except (OSError, ValueError):
        pass
    return normal, False


def finish_charging(bat=BAT):
    """A service stop must not strand a high-current lease in firmware."""
    try:
        (bat / 'constant_charge_current').write_text(f'{FCC_UA}\n')
    except OSError:
        # Zero is also permitted without an attached supply or healthy pack.
        (bat / 'constant_charge_current').write_text('0\n')


def charging_needs_light_sleep(bat=BAT, usb=USB, aux_pattern=AUX):
    """Keep thermal monitoring alive for an attached, authenticated PD session."""
    try:
        paths = glob.glob(aux_pattern)
        if len(paths) != 1:
            return False
        directory = Path(paths[0]).parent / 'houji_charger'
        kind = (usb / 'usb_type').read_text()
        status = (bat / 'status').read_text().strip()
        capacity = int((bat / 'capacity').read_text())
        return (int((usb / 'online').read_text()) == 1
                and ('[PD]' in kind or '[PD_PPS]' in kind)
                # The gauge can display 100% while the pack still takes current.
                # Keep host thermal monitoring alive until charging ends.
                and (status == 'Charging' or
                     (status == 'Not charging' and capacity < 100))
                and (bat / 'health').read_text().strip() == 'Good'
                and int((directory / 'authentic').read_text()) == 1)
    except (OSError, ValueError):
        return False


def desired_current(online, usb_type, temperature, health, wireless=False, *,
                    fast_charge=False):
    if type(fast_charge) is not bool or (wireless and fast_charge):
        raise ValueError('measured wired fast-charge state is required')
    if not wireless and (online != 1 or ('[PD]' not in usb_type and '[PD_PPS]' not in usb_type)):
        return None
    maximum = 470 if fast_charge and '[PD_PPS]' in usb_type else 400
    if health != 'Good' or not 100 <= temperature <= maximum:
        return 0
    return FCC_UA


def verification_idle_needed(state, input_limit):
    # A readback of zero alone does not clear the firmware's initial SIC vote.
    # Only the normal SET completion message performs that transition.
    return (state.get('verify_process') == 0
            and state.get('pd_verified') == 0
            and input_limit <= 100_000)


def tick(bat=BAT, usb=USB, aux_pattern=AUX, next_idle=0, now=None,
         current_limit=FCC_UA, wireless=False, *, fast_charge=False):
    now = time.monotonic() if now is None else now
    read_int = lambda path: int(path.read_text().strip())
    desired = desired_current(read_int(usb / 'online'),
                              (usb / 'usb_type').read_text(),
                              read_int(bat / 'temp'),
                              (bat / 'health').read_text().strip(), wireless,
                              fast_charge=fast_charge)
    if desired is None:
        return 0, 'waiting for USB-PD'
    if desired:
        if type(current_limit) is not int or not 0 <= current_limit <= 15_600_000:
            raise ValueError('unsupported host current cap')
        desired = current_limit
    # Every positive vote above 500 mA must renew the kernel's 10-second lease.
    if desired > FCC_UA or read_int(bat / 'constant_charge_current') != desired:
        (bat / 'constant_charge_current').write_text(f'{desired}\n')
    if desired == 0:
        return next_idle, 'host charging paused: battery temperature/health'
    paths = glob.glob(aux_pattern)
    if len(paths) != 1:
        raise RuntimeError('Houji charger completion interface unavailable')
    state_path = Path(paths[0])
    state = dict((k, int(v)) for k, v in
                 (line.split('=', 1) for line in state_path.read_text().splitlines()))
    if (not wireless and now >= next_idle
            and verification_idle_needed(state, read_int(usb / 'input_current_limit'))):
        state_path.with_name('houji_charger_verify_idle').write_text('0\n')
        next_idle = now + 30
        return next_idle, 'adapter verification idle; battery current capped at 500 mA'
    source = 'wireless' if wireless else 'USB-PD'
    return next_idle, f'{source} policy active; battery current capped at {desired // 1000} mA'


def wireless_selected(usb=USB, wls=WLS):
    if int((usb / 'online').read_text()) == 1:
        kind = (usb / 'usb_type').read_text()
        if '[PD]' in kind or '[PD_PPS]' in kind:
            return False
    return (wls / 'online').exists() and int((wls / 'online').read_text()) == 1


def finish_authentication(aux_pattern=AUX):
    for state in glob.glob(aux_pattern):
        path = Path(state).parent / 'houji_charger' / 'verify_process'
        try:
            if path.read_text().strip() == '1':
                path.write_text('0\n')
        except OSError:
            pass


def report_board_temperature(aux_pattern=AUX, sample=None):
    paths = glob.glob(aux_pattern)
    if len(paths) != 1:
        raise RuntimeError('charger thermal reporting interface unavailable')
    values, temperature = read_temperatures() if sample is None else sample
    # HyperOS mi_thermal_interface converts milli-C to deci-C for XM property 10.
    path = Path(paths[0]).parent / 'houji_charger/thermal_board_temp'
    path.write_text(f'{temperature // 100}\n')
    return f'board skin {temperature / 1000:.1f} C; USB {values["usb_therm"] / 1000:.1f} C'


def current_from_thermals(temperatures, authentication):
    skin = virtual_temperature(temperatures)
    if (authentication.get('battery_authentic') == 1
            and authentication.get('pd_verified') == 1
            and 15_000 <= temperatures['battery'] <= 38_000
            and skin < 35_000
            and temperatures['usb_therm'] < 40_000
            and max(temperatures.values()) < 45_000):
        return 1_000_000
    return FCC_UA


class StockChargingControl:
    """Stock normal-mode thermal votes plus a gradual, bounded current ramp."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.wired = WiredThermal()
        self.wireless = WirelessThermal()
        self.sic = SicThermal()
        self.history = deque(maxlen=3)
        self.last_sample = None
        self.next_control = 0
        self.vote = FCC_UA
        self.source = None
        self.wired_level = 0
        self.wireless_level = 0

    def step(self, temperatures, authentication, readback, maximum, screen_on, now,
             wireless=False, *, usb_online=True, fast_charge=False):
        if type(maximum) is not int or not 500_000 <= maximum <= 15_600_000:
            raise ValueError('invalid kernel staging ceiling')
        if type(wireless) is not bool or type(usb_online) is not bool:
            raise ValueError('measured power-source states are required')
        if type(fast_charge) is not bool or (wireless and fast_charge):
            raise ValueError('measured wired fast-charge state is required')
        source = (wireless, usb_online)
        if self.source is not None and self.source != source:
            self.reset()
        if self.last_sample is not None and not 0 < now - self.last_sample <= 2.5:
            self.reset()
        self.source = source
        self.last_sample = now
        skin = virtual_temperature(temperatures)
        self.history.appendleft(skin)
        # HyperOS runs both normal-mode monitor tables. Keep the battery vote
        # current even on wireless, so a previous wired vote cannot linger.
        self.wired_level = self.wired.update(skin, screen_on)
        self.wireless_level = self.wireless.update(skin, screen_on)
        level = self.wireless_level if wireless else self.wired_level
        # All four stock aging profiles use 15-47 C for the normal zone.
        # Firmware still owns voltage steps, taper and aging. Use this range
        # only with the matching kernel guard and a verified active FFC mode.
        battery_max = (47000 if fast_charge and usb_online
                       and authentication.get('pd_verified') == 1 else 38000)
        ready = (len(self.history) == 3
                 and authentication.get('battery_authentic') == 1
                 and (wireless or authentication.get('pd_verified') in (0, 1))
                 and 15000 <= temperatures['battery'] <= battery_max
                 # ADSP's stock connector warning/stop thresholds are 50/55 C.
                 # Keep a separate host margin while SIC controls virtual skin.
                 and temperatures['usb_therm'] < 45000
                 and max(temperatures.values()) < 55000)
        if not ready:
            self.vote = FCC_UA
            self.next_control = now + 2
        elif now >= self.next_control:
            # SIC uses the stock sensor history and actual winning FCC vote.
            # Standard PD/QC4 supplies do not have Xiaomi's private identity.
            # Keep the stock non-FFC 5350 mA bound when adapter auth is absent.
            protocol_max = (10000000 if wireless else
                            15600000 if authentication['pd_verified'] else 5350000)
            target = min(maximum, protocol_max)
            # mi_thermal_interface reads the USB supply's ONLINE property;
            # mi_thermald usb_monitor gates SIC on that value. Wireless-only
            # charging uses its XM81 table, not the wired SIC calculation.
            if usb_online:
                target = min(target, self.sic.update(tuple(self.history), readback))
            # Decreases apply immediately; increases are at most 250 mA / 2 s.
            self.vote = min(target, self.vote + 250000)
            self.next_control = now + 2
        self.vote = min(self.vote, maximum)
        return self.vote, level


def read_screen_state(pattern='/sys/class/backlight/*/bl_power'):
    values = [int(Path(p).read_text()) for p in glob.glob(pattern)]
    if not values or any(v not in range(5) for v in values):
        raise ValueError('display power state unavailable')
    return any(v == 0 for v in values)


class StockAuthentication:
    """Bounded retries per attachment; never synthesizes authentication results."""

    def __init__(self):
        self.next_attempt = 0
        self.attempts = 0
        self.source = None

    def step(self, bat=BAT, usb=USB, aux_pattern=AUX, agent=AUTH_AGENT,
             blob=AUTH_BLOB, now=None, runner=subprocess.run, wls=WLS):
        now = time.monotonic() if now is None else now
        if not agent.is_file() or not blob.is_file():
            return 'stock authentication agent not installed'
        wireless = wireless_selected(usb, wls)
        if int((usb / 'online').read_text()) != 1 and not wireless:
            self.attempts = 0
            self.next_attempt = 0
            self.source = None
            return 'authentication waiting for adapter'
        source = 'wireless' if wireless else 'wired'
        if source != self.source:
            self.attempts = 0
            self.next_attempt = 0
            self.source = source
        usb_type = (usb / 'usb_type').read_text()
        if not wireless and '[PD]' not in usb_type and '[PD_PPS]' not in usb_type:
            return 'authentication waiting for USB-PD'
        paths = glob.glob(aux_pattern)
        if len(paths) != 1:
            return 'authentication interface unavailable'
        directory = Path(paths[0]).parent / 'houji_charger'
        verified = lambda name: (directory / name).read_text().strip() == '1'
        passed = ('stock battery authentication passed; wireless receiver active'
                  if wireless else 'stock battery and adapter authentication passed')
        if verified('authentic') and (wireless or verified('pd_verifed')):
            return passed
        if self.attempts >= 3:
            if not wireless and verified('authentic'):
                return 'stock battery authentication passed; standard PD bound (Xiaomi adapter authentication unavailable)'
            return 'stock battery authentication unavailable; keeping 500 mA cap until next attachment'
        if (int((bat / 'constant_charge_current').read_text()) != FCC_UA
                or not 100 <= int((bat / 'temp').read_text()) <= 400
                or (bat / 'health').read_text().strip() != 'Good'):
            return 'authentication waiting for conservative charging conditions'
        if now < self.next_attempt:
            return 'stock authentication waiting for retry or next attachment'
        self.attempts += 1
        self.next_attempt = now + 60
        try:
            modes = [('--gauge', 'authentic')]
            if not wireless:
                modes.append(('--adapter', 'pd_verifed'))
            for mode, property_name in modes:
                if verified(property_name):
                    continue
                result = runner([str(agent), str(blob), mode, str(directory)],
                                timeout=30, check=False)
                if result.returncode or not verified(property_name):
                    return 'stock authentication failed; keeping 500 mA cap'
            return passed
        except subprocess.TimeoutExpired:
            return 'stock authentication timed out; keeping 500 mA cap'
        finally:
            finish_authentication(aux_pattern)


def main():
    if sys.argv[1:] == ['--needs-light-sleep']:
        sys.exit(0 if charging_needs_light_sleep() else 1)
    if sys.argv[1:] == ['--finish-auth']:
        finish_authentication()
        finish_charging()
        return
    normal = configured_ceiling(Path('/etc/armada/houji-charge-ceiling'), 3_000_000)
    hypercharge = configured_ceiling(Path('/etc/armada/houji-hypercharge-ceiling'), normal)
    # This is only the absolute write bound. Selection, authentication, ramp,
    # stock thermal votes and lease renewal still gate each current request.
    Path('/sys/module/qcom_battmgr/parameters/houji_max_fcc_ua').write_text(
        f'{max(normal, hypercharge)}\n')
    next_idle = 0
    previous = None
    authentication = StockAuthentication()
    control = StockChargingControl()
    while True:
        loop_start = time.monotonic()
        try:
            limit = FCC_UA
            fast_charge = False
            wireless = wireless_selected()
            sample = None
            board_error = None
            board_state = ''
            try:
                sample = read_temperatures()
                paths = glob.glob(AUX)
                if len(paths) != 1:
                    raise RuntimeError('charger authentication state unavailable')
                verified = dict((k, int(v)) for k, v in
                                (line.split('=', 1) for line in Path(paths[0]).read_text().splitlines()))
                board_state = report_board_temperature(sample=sample)
                lease_path = Path(paths[0]).parent / 'houji_charger/fcc_lease_seconds'
                if int(lease_path.read_text()) != 10:
                    raise RuntimeError('kernel current fallback unavailable')
                directory = Path(paths[0]).parent / 'houji_charger'
                ceiling = directory / 'fcc_max_ua'
                if ceiling.exists():
                    # Keep the 500 mA preflight while the bounded stock adapter
                    # attempts run. A standards-compliant non-Xiaomi supply may
                    # use the non-FFC path after verification is exhausted.
                    if not wireless and verified.get('pd_verified') == 0 and authentication.attempts < 3:
                        verified['pd_verified'] = -1
                    maximum = int(ceiling.read_text())
                    if wireless:
                        # Stage wireless independently; initial pad tests use 500 mA.
                        configuration = Path('/etc/armada/houji-wireless-ceiling')
                        wireless_max = int(configuration.read_text()) if configuration.exists() else FCC_UA
                        if not FCC_UA <= wireless_max <= 10000000:
                            raise ValueError('wireless ceiling outside stock range')
                        maximum = min(maximum, wireless_max)
                    else:
                        wired_max, fast_charge = wired_limits(
                            directory, (USB / 'usb_type').read_text(), verified,
                            normal, hypercharge)
                        maximum = min(maximum, wired_max)
                    limit, level = control.step(sample[0], verified,
                                                int((BAT / 'constant_charge_current').read_text()),
                                                maximum, read_screen_state(),
                                                time.clock_gettime(time.CLOCK_BOOTTIME), wireless,
                                                usb_online=int((USB / 'online').read_text()) == 1,
                                                fast_charge=fast_charge)
                    # Install both applicable stock monitor votes before FCC.
                    levels = [('wired_thermal_level', control.wired_level)]
                    if wireless:
                        levels.append(('wireless_thermal_level', level))
                    for name, thermal_level in levels:
                        thermal = directory / name
                        thermal.write_text(f'{thermal_level}\n')
                        if int(thermal.read_text()) != thermal_level:
                            raise RuntimeError('firmware thermal level readback mismatch')
                else:
                    limit = FCC_UA if wireless else current_from_thermals(sample[0], verified)
            except (OSError, ValueError, RuntimeError) as error:
                limit = FCC_UA
                fast_charge = False
                control.reset()
                board_error = error
            next_idle, state = tick(next_idle=next_idle, current_limit=limit, wireless=wireless,
                                   fast_charge=fast_charge)
            state += '; ' + authentication.step()
            if board_error is None:
                state += '; ' + board_state
            else:
                state += f'; waiting for board thermistors: {board_error}'
        except (OSError, ValueError, RuntimeError) as error:
            # Never invent a temperature or authentication result on read failure.
            control.reset()
            state = f'waiting for charger telemetry: {error}'
        if state != previous:
            print(state, flush=True)
            previous = state
        time.sleep(max(0.1, 1 - (time.monotonic() - loop_start)))


if __name__ == '__main__':
    main()
