import unittest

from dalicontrol_e64 import commands as c


class Encoding(unittest.TestCase):
    def test_level(self):
        self.assertEqual(c.level(4, 255), "0B03FF")
        self.assertEqual(c.level(10, 0x7F), "0B097F")
        self.assertEqual(c.level(64, 0), "0B3F00")
        self.assertEqual(c.level_all(255), c.ALL_ON)

    def test_scene_and_blink(self):
        self.assertEqual(c.scene_recall(5), "070500")
        self.assertEqual(c.scene_recall(15), "070F00")
        self.assertEqual(c.blink(1), "044000")
        self.assertEqual(c.blink(38), "046500")
        self.assertEqual(c.group_level(2, 255), "0801FF")

    def test_range_errors(self):
        for call in (lambda: c.level(0, 1), lambda: c.level(65, 1), lambda: c.level(1, 256),
                     lambda: c.scene_recall(16), lambda: c.level(True, 1)):
            self.assertRaises(ValueError, call)


class Allowlist(unittest.TestCase):
    def test_operational(self):
        for ok in ("0A0000", "0BFFFF", "0B0000", "0B3FFF", "070F00", "0bff80"):
            self.assertTrue(c.is_operational(ok), ok)

    def test_everything_else_is_refused(self):
        for bad in ("010000", "020000", "050003", "060F00", "0E0102", "100000",
                    "0B4000", "071000", "070001", "0A0001", "0AFF00", "", "0B03", "zzzzzz"):
            self.assertFalse(c.is_operational(bad), bad)

    def test_new_installation_is_named(self):
        with self.assertRaisesRegex(ValueError, "NEWINST"):
            c.require_operational("010000")


if __name__ == "__main__":
    unittest.main()
