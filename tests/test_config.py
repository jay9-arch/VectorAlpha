"""Tests for the data-folder configuration (src/config.py)."""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import config  # noqa: E402


class DataDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="valpha_cfg_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def touch(self, *parts):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        return path

    def test_absolute_setting_with_spaces(self):
        self.touch("Trading Bot", "data", "AAA.csv")
        folder = os.path.join(self.tmp, "Trading Bot", "data")
        self.assertEqual(config.resolve_data_dir(folder, environ={}), (folder, None))

    def test_relative_setting_is_relative_to_the_project_root(self):
        folder, _ = config.resolve_data_dir("../Trading Bot/data", environ={})
        expected = os.path.normpath(os.path.join(config.PROJECT_ROOT, "..", "Trading Bot", "data"))
        # the folder usually does not exist in CI, in which case the bundled data (or the
        # intended path) is reported -- either way a relative path must never be cwd-relative
        self.assertIn(folder, (expected, config.BUNDLED_DATA_DIR))

    def test_environment_variable_overrides_the_setting(self):
        self.touch("env_dir", "BBB.csv")
        env_folder = os.path.join(self.tmp, "env_dir")
        folder, note = config.resolve_data_dir("/definitely/not/here", environ={"VALPHA_DATA_DIR": env_folder})
        self.assertEqual((folder, note), (env_folder, None))

    def test_price_data_subfolder_is_used_when_the_folder_has_no_csvs(self):
        self.touch("proj_data", "price_data", "CCC.csv")
        folder, note = config.resolve_data_dir(os.path.join(self.tmp, "proj_data"), environ={})
        self.assertEqual(folder, os.path.join(self.tmp, "proj_data", "price_data"))
        self.assertIsNone(note)

    def test_csvs_directly_in_the_folder_win_over_a_price_data_subfolder(self):
        self.touch("both", "DDD.csv")
        self.touch("both", "price_data", "EEE.csv")
        folder = os.path.join(self.tmp, "both")
        self.assertEqual(config.resolve_data_dir(folder, environ={})[0], folder)

    def test_missing_folder_warns_and_never_crashes(self):
        missing = os.path.join(self.tmp, "nope")
        folder, note = config.resolve_data_dir(missing, environ={})
        self.assertIn(folder, (missing, config.BUNDLED_DATA_DIR))
        self.assertIn("not found", note)
        self.assertIn(missing, note)

    def test_any_single_subfolder_with_csvs_is_found(self):
        self.touch("data", "my_prices", "FFF.csv")
        folder, note = config.resolve_data_dir(os.path.join(self.tmp, "data"), environ={})
        self.assertEqual(folder, os.path.join(self.tmp, "data", "my_prices"))
        self.assertIsNone(note)

    def test_generated_demo_folders_never_shadow_the_data_folder(self):
        self.touch("data", "demo_price_data", "STOCKA.csv")
        self.touch("data", "synthetic_demo", "SYN001.csv")
        folder, _ = config.resolve_data_dir(os.path.join(self.tmp, "data"), environ={})
        self.assertEqual(folder, os.path.join(self.tmp, "data"))


if __name__ == "__main__":
    unittest.main()
