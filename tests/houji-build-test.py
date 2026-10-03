#!/usr/bin/env python3
"""Build helpers that adapt to the tools of the machine doing the build."""
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ports/houji'))
import buildlib

# Real `mkfs.erofs --help` lines. 1.7.1 is Ubuntu 24.04's; the 1.9.4 sample adds the one line that release prints.
HELP_1_7_1 = '''usage: [options] FILE SOURCE(s)
Generate EROFS image (FILE) from DIRECTORY, TARBALL and/or EROFS images.  And [options] are:
 -b#                   set block size to # (# = page size by default)
 -zX[,Y][:..]          X=compressor (Y=compression level, optional)
 -T#                   set a fixed UNIX timestamp # to all files
 -L volume-label       set the volume label (maximum 15)
'''
HELP_1_9_4 = HELP_1_7_1 + '''     --workers=#            set the number of worker threads to # (default: 20)
'''


class ErofsWorkerTests(unittest.TestCase):
    def test_a_release_that_has_the_option_gets_it(self):
        self.assertEqual(buildlib.erofs_worker_options(HELP_1_9_4), ['--workers=8'])

    def test_a_release_without_it_is_not_passed_it(self):
        # Ubuntu 24.04 ships erofs-utils 1.7.1, which rejects --workers outright.
        self.assertEqual(buildlib.erofs_worker_options(HELP_1_7_1), [])

    def test_empty_help_output_is_treated_as_unsupported(self):
        self.assertEqual(buildlib.erofs_worker_options(''), [])


if __name__ == '__main__':
    unittest.main()
