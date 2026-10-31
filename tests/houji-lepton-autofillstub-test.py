#!/usr/bin/env python3
"""The autofill stub Lepton's Android 11 needs: what ships, where it lands, and which calls it answers.

Whether an app survives with it running is checked on a phone with packages/lepton/autofillstub/device-test.
"""
import hashlib
from pathlib import Path
import re
import sys
import unittest
import zipfile

sys.dont_write_bytecode = True  # the scripts live in a tree that is copied into images
ROOT = Path(__file__).resolve().parents[1]
LEPTON = ROOT / 'packages/lepton'
JAR = LEPTON / 'prebuilt/system/framework/armada-autofillstub.jar'
RC = LEPTON / 'overlay/system/etc/init/armada-autofillstub.rc'
SOURCE = LEPTON / 'autofillstub/AutofillStub.java'

# IAutoFillManager's calls (Android 11, from Lepton's framework.jar) by transaction code. All are one-way.
# The ones that end in an IResultReceiver are the ones that expect an answer.
WITH_RECEIVER = {
    1: 'addClient', 3: 'startSession', 4: 'getFillEventHistory', 5: 'restoreSession',
    13: 'isServiceSupported', 14: 'isServiceEnabled', 16: 'getUserData', 17: 'getUserDataId',
    19: 'isFieldClassificationEnabled', 20: 'getAutofillServiceComponentName',
    21: 'getAvailableFieldClassificationAlgorithms', 22: 'getDefaultFieldClassificationAlgorithm',
    23: 'setAugmentedAutofillWhitelist',
}
WITHOUT_RECEIVER = {
    2: 'removeClient', 6: 'updateSession', 7: 'setAutofillFailure', 8: 'finishSession', 9: 'cancelSession',
    10: 'setAuthenticationResult', 11: 'setHasCallback', 12: 'disableOwnedAutofillServices', 15: 'onPendingSaveUi',
    18: 'setUserData',
}


class Shipped(unittest.TestCase):
    def test_the_jar_is_checksummed_with_the_other_prebuilt_files(self):
        sums = dict(line.split('  ', 1)[::-1] for line in (LEPTON / 'prebuilt.sha256').read_text().splitlines())
        digest = hashlib.sha256(JAR.read_bytes()).hexdigest()
        self.assertEqual(sums.get('system/framework/armada-autofillstub.jar'), digest)
        paths = list(sums)
        self.assertEqual(paths, sorted(paths), 'prebuilt.sha256 stays in C-locale path order')

    def test_the_jar_holds_dex_code_for_the_stub_and_nothing_else(self):
        with zipfile.ZipFile(JAR) as archive:
            self.assertEqual(archive.namelist(), ['classes.dex'])
            dex = archive.read('classes.dex')
        self.assertEqual(dex[:4], b'dex\n')
        for needle in (b'AutofillStub', b'android.view.autofill.IAutoFillManager',
                       b'com.android.internal.os.IResultReceiver', b'autofill'):
            self.assertIn(needle, dex)

    def test_the_init_file_starts_it_only_on_android_11_and_after_the_service_manager_is_up(self):
        text = RC.read_text()
        self.assertIn('-Djava.class.path=/system/framework/armada-autofillstub.jar /system/bin AutofillStub', text)
        self.assertIn('on zygote-start && property:ro.build.version.sdk=30\n    start armada_autofillstub', text)
        self.assertRegex(text, r'(?m)^    disabled$')
        self.assertTrue(text.startswith('service armada_autofillstub '))

    def test_the_two_files_land_where_lepton_takes_its_overlay_from(self):
        # The wrapper copies <tool>/overlay over the container's rootfs overlay, which Lepton bind-mounts.
        stage = (ROOT / 'ports/houji/stage-root.py').read_text()
        self.assertIn("lepton_tool+'overlay/'+name", stage)
        for source in ('prebuilt/system/framework/armada-autofillstub.jar',
                       'overlay/system/etc/init/armada-autofillstub.rc'):
            self.assertIn("'%s'" % source, stage)
        for name in ('system/framework/armada-autofillstub.jar', 'system/etc/init/armada-autofillstub.rc'):
            self.assertIn("'%s'" % name, stage)
        # packages/lepton/build.sh bundles overlay/ and prebuilt/ into the RPM's overlay.tar.
        build = (LEPTON / 'build.sh').read_text()
        self.assertIn('cp -a /work/overlay/. /work/prebuilt/. /tmp/overlay/', build)

    def test_the_port_runs_the_lepton_tests_in_its_release_workflow(self):
        runner = (ROOT / 'ports/houji/unit-tests.sh').read_text()
        self.assertIn('lepton-armada-test.sh', runner)
        self.assertIn('armada-apk-launcher-test.py', runner)


class Calls(unittest.TestCase):
    source = SOURCE.read_text()

    def test_the_stub_answers_exactly_the_calls_that_carry_a_result_receiver(self):
        body = self.source.split('static boolean hasReceiver(int code)', 1)[1].split('}\n    }\n', 1)[0]
        codes = {int(n) for n in re.findall(r'case (\d+):', body)}
        self.assertEqual(codes, set(WITH_RECEIVER))
        self.assertFalse(codes & set(WITHOUT_RECEIVER))
        self.assertEqual(sorted(set(WITH_RECEIVER) | set(WITHOUT_RECEIVER)), list(range(1, 24)))

    def test_it_registers_under_the_name_the_framework_looks_up(self):
        self.assertIn('"autofill", new AutofillStub()', self.source)
        self.assertIn('private static final String DESCRIPTOR = "android.view.autofill.IAutoFillManager";', self.source)
        self.assertIn('private static final int LAST_CALL = 23;', self.source)

    def test_it_reports_autofill_off_and_no_session(self):
        # addClient's answer is the flags (0: disabled); startSession's is the session id (MAX_VALUE: none).
        self.assertIn('answer(data, code == START_SESSION ? NO_SESSION : 0);', self.source)
        self.assertIn('private static final int NO_SESSION = Integer.MAX_VALUE;', self.source)
        self.assertIn('private static final int START_SESSION = 3;', self.source)

    def test_the_receiver_is_found_at_the_end_of_the_parcel_for_64_bit_binders(self):
        # A flattened binder is 24 bytes, plus a 4-byte stability marker on Android 10 and later.
        tails = re.search(r'RECEIVER_TAIL = \{([^}]*)\}', self.source).group(1)
        self.assertEqual([int(n) for n in tails.split(',')][:2], [28, 24])


if __name__ == '__main__':
    unittest.main()
