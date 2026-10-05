#!/usr/bin/env python3
"""Houji native sleep: sensor DSP wakeups must stay invisible, everything else not."""
import errno
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True  # the script lives in a tree that is copied into images
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'ports/houji/runtime/usr/libexec/armada/houji-sleep'
loader = importlib.machinery.SourceFileLoader('houji_sleep', str(SCRIPT))
spec = importlib.util.spec_from_loader('houji_sleep', loader)
sleep = importlib.util.module_from_spec(spec)
loader.exec_module(sleep)

DSP = '6800000.remoteproc:glink-edge.fastrpcglink-apps-dsp.-1.-1'
PWRKEY = 'c400000.spmi:pmic@0:pon@1300:pwrkey'
BATTERY = 'battery'
UCSI = 'ucsi-source-psy-pmic_glink.ucsi.01'
RTC = 'c400000.spmi:pmic@0:rtc@6100'

HEADER = ('name\t\tactive_count\tevent_count\twakeup_count\texpire_count\t'
          'active_since\ttotal_time\tmax_time\tlast_change\tprevent_suspend_time\n')

INTERRUPTS = '''           CPU0       CPU1
 11:       1000       2000     GICv3  27 Level     arch_timer
 21:          3          4  spmi-pmic-arb  pmic_pwrkey
 22:          0          2  spmi-pmic-arb  pmic_resin
160:          5          6  msmgpio  25 Edge      Volume Up
217:          0          0  spmi-pmic-arb  pm8xxx_rtc_alarm
IPI0:         9          9       Rescheduling interrupts
'''


class Fake(sleep.Platform):
    """A phone whose suspends follow a script. Nothing here touches the host."""

    def __init__(self, outcomes, supported=True, forced=False, freeze_ok=True):
        self.outcomes = list(outcomes)
        self._supported, self._forced, self._freeze_ok = supported, forced, freeze_ok
        self.calls = []
        self.counts = {DSP: 0, BATTERY: 0}
        self.irq = None
        self.inputs = {'pmic_pwrkey': 7}
        self.boot = 1000.0
        self.mono = 100.0
        self.writes = 0

    def classic(self, argv):
        self.calls.append('classic')
        return 'classic'

    def shield_signals(self):
        self.calls.append('shield')

    def classic_forced(self):
        return self._forced

    def supported(self):
        return self._supported

    def systemctl(self, *args):
        self.calls.append('systemctl ' + ' '.join(args))
        return self._freeze_ok or args[0] != 'freeze'

    def run_hooks(self, phase):
        self.calls.append('hooks ' + phase)

    def write_state(self):
        self.calls.append('suspend')
        outcome = self.outcomes.pop(0)
        if outcome.get('errno'):
            raise OSError(outcome['errno'], 'busy')
        self.writes += 1
        for name, delta in outcome.get('events', {}).items():
            self.counts[name] = self.counts.get(name, 0) + delta
        self.irq = outcome.get('irq')
        self.boot += outcome.get('slept', 600.0) + 1.0
        self.mono += 1.0
        for name, delta in outcome.get('inputs', {}).items():
            self.inputs[name] = self.inputs.get(name, 0) + delta

    def during_awake(self, **inputs):
        for name, delta in inputs.items():
            self.inputs[name] += delta

    def wake_irq(self):
        return self.irq

    def sources(self):
        return dict(self.counts)

    def input_counts(self):
        return dict(self.inputs)

    def boottime(self):
        return self.boot

    def monotonic(self):
        return self.mono

    def restore_watchdogs(self, delay):
        self.calls.append('restore watchdogs')

    def sleep(self, seconds):
        self.boot += seconds
        self.mono += seconds


def dsp(slept=600.0, **extra):
    events = {DSP: 1}
    events.update(extra)
    return {'events': events, 'slept': slept}


class ParsingTests(unittest.TestCase):
    def test_debugfs_rows_with_padding_and_double_tabs(self):
        text = (HEADER +
                'ucsi-source-psy-pmic_glink.ucsi.01\t22\t\t41\t\t0\t\t0\t\t0\t\t6\t\t1\t\t4716087\t\t0\n' +
                DSP + '\t13\t\t13\t\t0\t\t13\t\t0\t\t66792\t\t5252\t\t4704476\t\t0\n' +
                'short\t1\n')
        self.assertEqual(sleep.parse_sources(text), {'ucsi-source-psy-pmic_glink.ucsi.01': 41, DSP: 13})

    def test_only_key_like_interrupts_are_watched(self):
        counts = sleep.parse_input_irqs(INTERRUPTS)
        self.assertEqual(sorted(counts.values()), [2, 7, 11])
        self.assertTrue(any(label.endswith('Volume Up') for label in counts))
        self.assertEqual(len(counts), 3)


class ClassifyTests(unittest.TestCase):
    def check(self, delta, irq=None):
        before = {DSP: 5, BATTERY: 5, UCSI: 5, RTC: 5, PWRKEY: 0}
        after = {name: count + delta.get(name, 0) for name, count in before.items()}
        return sleep.classify(before, after, irq)[0]

    def test_dsp_request_alone_is_dark(self):
        self.assertEqual(self.check({DSP: 1}), 'dark')

    def test_charger_and_rtc_bookkeeping_do_not_make_it_real(self):
        self.assertEqual(self.check({DSP: 1, BATTERY: 6, UCSI: 2, RTC: 3}), 'dark')

    def test_any_wake_interrupt_is_real(self):
        self.assertEqual(self.check({DSP: 1}, irq='21'), 'real')
        self.assertEqual(self.check({}, irq='217'), 'real')

    def test_unlisted_activity_alongside_the_dsp_is_real(self):
        self.assertEqual(self.check({DSP: 1, PWRKEY: 1}), 'real')

    def test_no_recorded_cause_is_not_dark(self):
        self.assertEqual(self.check({}), 'unknown')
        self.assertEqual(self.check({BATTERY: 1}), 'unknown')


class SleepFlowTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch('sys.stderr', io.StringIO())
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_sleep(self, platform):
        return sleep.run(platform, ['suspend'])

    def test_single_real_wake_matches_systemd_sleep_order(self):
        phone = Fake([{'irq': '21', 'slept': 120}])
        self.assertEqual(self.run_sleep(phone), 0)
        self.assertEqual(phone.calls, [
            'systemctl freeze user.slice', 'systemctl service-watchdogs no', 'hooks pre',
            'suspend', 'shield', 'hooks post', 'systemctl thaw user.slice',
            'restore watchdogs'])

    def test_dsp_wakes_are_absorbed_and_the_desktop_thaws_once(self):
        phone = Fake([dsp(), dsp(), dsp(), {'irq': '21'}])
        self.assertEqual(self.run_sleep(phone), 0)
        self.assertEqual(phone.writes, 4)
        self.assertEqual(phone.calls.count('hooks pre'), 1)
        self.assertEqual(phone.calls.count('hooks post'), 1)
        self.assertEqual(phone.calls.count('systemctl thaw user.slice'), 1)
        self.assertEqual(phone.calls.index('hooks post'), len(phone.calls) - 3)
        self.assertEqual(phone.calls[-4], 'shield')

    def test_power_key_after_dsp_wake_ends_the_loop_immediately(self):
        phone = Fake([dsp(), {'irq': '21'}, {'irq': '21'}])
        self.run_sleep(phone)
        self.assertEqual(phone.writes, 2)

    def test_listener_still_busy_is_retried_not_failed(self):
        phone = Fake([dsp(), {'errno': errno.EBUSY}, {'errno': errno.EBUSY}, {'irq': '21'}])
        self.assertEqual(self.run_sleep(phone), 0)
        self.assertEqual(phone.writes, 2)

    def test_listener_that_never_answers_gives_up_and_still_thaws(self):
        phone = Fake([{'errno': errno.EBUSY}] * 200)
        with self.assertRaises(OSError):
            self.run_sleep(phone)
        self.assertEqual(phone.calls[-4:], ['shield', 'hooks post', 'systemctl thaw user.slice',
                                            'restore watchdogs'])

    def test_other_suspend_errors_are_not_retried(self):
        phone = Fake([{'errno': errno.EINVAL}, {'irq': '21'}])
        with self.assertRaises(OSError):
            self.run_sleep(phone)
        self.assertEqual(phone.calls.count('suspend'), 1)
        self.assertEqual(phone.calls[-2:], ['systemctl thaw user.slice', 'restore watchdogs'])

    def test_a_wake_loop_is_cut_short(self):
        phone = Fake([dsp(slept=1.0) for _ in range(40)] + [{'irq': '21'}])
        self.run_sleep(phone)
        self.assertEqual(phone.writes, sleep.DARK_BURST)
        self.assertEqual(phone.calls[-2:], ['systemctl thaw user.slice', 'restore watchdogs'])

    def test_service_watchdogs_are_paused_for_the_loop_and_restored_last(self):
        phone = Fake([dsp(slept=300) for _ in range(3)] + [{'irq': '21'}])
        self.run_sleep(phone)
        self.assertLess(phone.calls.index('systemctl service-watchdogs no'),
                        phone.calls.index('hooks pre'))
        self.assertEqual(phone.calls[-1], 'restore watchdogs')

    def test_spaced_out_dsp_wakes_never_trigger_the_loop_guard(self):
        phone = Fake([dsp(slept=300) for _ in range(30)] + [{'irq': '21'}])
        self.run_sleep(phone)
        self.assertEqual(phone.writes, 31)

    def test_suspend_that_never_slept_is_retried_a_few_times(self):
        phone = Fake([{'slept': 0.0}] * 3 + [{'irq': '21'}])
        self.run_sleep(phone)
        self.assertEqual(phone.writes, 4)

    def test_unexplained_wake_after_real_sleep_resumes(self):
        phone = Fake([{'slept': 90}, {'irq': '21'}])
        self.run_sleep(phone)
        self.assertEqual(phone.writes, 1)

    def press_after(self, phone, reads):
        """Make every key-interrupt read after the first `reads` see a press."""
        counts = phone.input_counts
        seen = []

        def counts_with_press():
            seen.append(1)
            result = counts()
            if len(seen) > reads:
                result['pmic_pwrkey'] += 1
            return result
        phone.input_counts = counts_with_press

    def test_key_pressed_in_the_awake_gap_is_not_swallowed(self):
        # Reads per cycle: before the write, right after it. The press lands
        # after the first cycle's reads, before the second cycle's.
        phone = Fake([dsp(), {'irq': '21'}])
        self.press_after(phone, 2)
        self.run_sleep(phone)
        self.assertEqual(phone.writes, 1)  # never suspended again
        self.assertEqual(phone.calls[-4:], ['shield', 'hooks post', 'systemctl thaw user.slice',
                                            'restore watchdogs'])

    def test_key_pressed_while_resuming_is_not_swallowed(self):
        # Between the write returning and the classification, which a late
        # baseline would have hidden.
        phone = Fake([dsp(), {'irq': '21'}])
        self.press_after(phone, 1)
        self.run_sleep(phone)
        self.assertEqual(phone.writes, 1)

    def test_key_interrupt_with_no_wake_irq_during_suspend_is_real(self):
        phone = Fake([dict(dsp(), inputs={'pmic_pwrkey': 1}), {'irq': '21'}])
        self.run_sleep(phone)
        self.assertEqual(phone.writes, 1)

    def test_a_wake_loop_does_not_trip_on_unchanged_inputs(self):
        phone = Fake([dsp(), dsp(), {'irq': '21'}])
        self.run_sleep(phone)
        self.assertEqual(phone.writes, 3)

    def test_listener_stuck_after_dark_wakes_resumes_normally(self):
        phone = Fake([dsp()] + [{'errno': errno.EBUSY}] * 200)
        self.assertEqual(self.run_sleep(phone), 0)
        self.assertEqual(phone.writes, 1)
        self.assertEqual(phone.calls[-4:], ['shield', 'hooks post', 'systemctl thaw user.slice',
                                            'restore watchdogs'])

    def test_a_failing_post_phase_still_thaws(self):
        phone = Fake([{'irq': '21'}])
        original = phone.run_hooks

        def hooks(phase):
            original(phase)
            if phase == 'post':
                raise RuntimeError('hook runner exploded')
        phone.run_hooks = hooks
        with self.assertRaises(RuntimeError):
            self.run_sleep(phone)
        self.assertEqual(phone.calls[-2:], ['systemctl thaw user.slice', 'restore watchdogs'])

    def test_non_suspend_requests_use_systemd_sleep(self):
        phone = Fake([])
        self.assertEqual(sleep.run(phone, ['hibernate']), 'classic')
        self.assertEqual(phone.calls, ['classic'])

    def test_kill_switch_and_unsupported_kernels_fall_back(self):
        for phone in (Fake([], forced=True), Fake([], supported=False)):
            self.assertEqual(self.run_sleep(phone), 'classic')
            self.assertEqual(phone.calls, ['classic'])

    def test_freeze_failure_falls_back_before_touching_anything(self):
        phone = Fake([], freeze_ok=False)
        self.assertEqual(self.run_sleep(phone), 'classic')
        self.assertEqual(phone.calls, ['systemctl freeze user.slice', 'classic'])


class RealPlatformTests(unittest.TestCase):
    """The pieces the fakes replace, run against temporary files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / 'hooks.log'
        patcher = mock.patch('sys.stderr', io.StringIO())
        self.stderr = patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {'HOOK_LOG': str(self.log)})
        env.start()
        self.addCleanup(env.stop)

    def hook(self, directory, name, body='echo "$(basename "$0") $1 $2 $SYSTEMD_SLEEP_ACTION" >>"$HOOK_LOG"',
             mode=0o755, shebang='#!/bin/sh\n'):
        folder = self.root / directory
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_text(shebang + body + '\n')
        path.chmod(mode)
        return path

    def run_hooks(self, *directories, phase='pre'):
        dirs = tuple(str(self.root / d) for d in directories)
        with mock.patch.object(sleep, 'HOOK_DIRS', dirs):
            sleep.Platform().run_hooks(phase)
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_hooks_get_systemd_arguments_and_environment(self):
        self.hook('lib', '10-a')
        self.hook('lib', '20-b')
        # They run in parallel, so only the set of results is defined.
        self.assertEqual(sorted(self.run_hooks('lib')),
                         ['10-a pre suspend suspend', '20-b pre suspend suspend'])

    def test_earlier_directories_override_and_mask(self):
        self.hook('lib', '10-keep')
        self.hook('lib', '20-replaced', body='echo lib-version >>"$HOOK_LOG"')
        self.hook('lib', '30-masked')
        self.hook('etc', '20-replaced', body='echo etc-version >>"$HOOK_LOG"')
        (self.root / 'etc' / '30-masked').symlink_to(os.devnull)
        lines = self.run_hooks('etc', 'lib')
        self.assertEqual(sorted(lines), ['10-keep pre suspend suspend', 'etc-version'])

    def test_non_executable_files_are_ignored(self):
        self.hook('lib', '10-data', mode=0o644)
        self.hook('lib', '20-run')
        self.assertEqual(self.run_hooks('lib'), ['20-run pre suspend suspend'])

    def test_unstartable_hook_is_logged_and_the_rest_still_run(self):
        self.hook('lib', '10-broken', shebang='')  # executable, no interpreter
        self.hook('lib', '20-fine')
        self.assertEqual(self.run_hooks('lib'), ['20-fine pre suspend suspend'])
        self.assertIn('10-broken could not start', self.stderr.getvalue())

    def test_failing_hook_is_logged_and_does_not_stop_the_others(self):
        self.hook('lib', '10-fails', body='exit 3')
        self.hook('lib', '20-fine')
        self.assertEqual(self.run_hooks('lib', phase='post'), ['20-fine post suspend suspend'])
        self.assertIn('10-fails exited with 3 in post', self.stderr.getvalue())

    def test_hook_that_hangs_is_killed_at_the_deadline(self):
        self.hook('lib', '10-hangs', body='sleep 30')
        self.hook('lib', '20-fine')
        started = sleep.time.monotonic()
        with mock.patch.object(sleep, 'HOOK_TIMEOUT', 1):
            lines = self.run_hooks('lib')
        self.assertLess(sleep.time.monotonic() - started, 10)
        self.assertEqual(lines, ['20-fine pre suspend suspend'])
        self.assertIn('10-hangs timed out', self.stderr.getvalue())

    def test_wake_irq_distinguishes_no_interrupt_from_unreadable(self):
        platform = sleep.Platform()
        with mock.patch.object(Path, 'read_text', side_effect=OSError(errno.ENODATA, 'no data')):
            self.assertIsNone(platform.wake_irq())
        with mock.patch.object(Path, 'read_text', side_effect=OSError(errno.EACCES, 'denied')):
            self.assertEqual(platform.wake_irq(), 'unreadable (EACCES)')
        with mock.patch.object(Path, 'read_text', return_value='21\n'):
            self.assertEqual(platform.wake_irq(), '21')

    def test_unreadable_wake_irq_is_never_called_dark(self):
        self.assertEqual(sleep.classify({DSP: 0}, {DSP: 1}, 'unreadable (EACCES)')[0], 'real')

    def test_suspend_writes_mem(self):
        state = self.root / 'state'
        with mock.patch.object(sleep, 'STATE', str(state)):
            sleep.Platform().write_state()
        self.assertEqual(state.read_text(), 'mem')

    def test_shielding_ignores_stop_requests(self):
        saved = {n: signal.getsignal(n) for n in (signal.SIGTERM, signal.SIGINT)}
        try:
            sleep.Platform().shield_signals()
            self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)
            self.assertEqual(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
        finally:
            for number, handler in saved.items():
                signal.signal(number, handler)


if __name__ == '__main__':
    unittest.main()
