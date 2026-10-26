import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import system_check


class HelperTests(unittest.TestCase):
    def test_version_tuple_compares_yt_dlp_date_versions(self):
        vt = system_check.version_tuple
        self.assertLess(vt("2026.06.09"), vt("2026.10.21"))
        self.assertLess(vt("2026.10.21"), vt("2026.10.21.1"))
        self.assertEqual(vt(None), ())

    def test_program_version_reads_version_number(self):
        self.assertEqual(system_check.program_version([sys.executable, "--version"]),
                         ".".join(map(str, sys.version_info[:3])))

    def test_program_version_is_none_for_missing_program(self):
        self.assertIsNone(system_check.program_version(["definitely-not-a-real-program-xyz", "--version"]))

    def test_last_line(self):
        self.assertEqual(system_check.last_line("Traceback...\n  File x\nImportError: boom\n"), "ImportError: boom")
        self.assertEqual(system_check.last_line(""), "ไม่ทราบสาเหตุ")

    def test_every_critical_step_exists(self):
        keys = {key for key, _ in system_check.STEPS}
        self.assertLessEqual(system_check.CRITICAL_STEPS | system_check.NEEDS_PACKAGES, keys)
        for key in keys:
            self.assertTrue(hasattr(system_check.SystemCheck, "check_" + key), key)


if __name__ == "__main__":
    unittest.main()
