import unittest

from app.routes.browser import mix_needs_vision_fallback


class ObserveModeTests(unittest.TestCase):
    def test_mix_skips_vision_when_dom_has_elements(self) -> None:
        self.assertFalse(mix_needs_vision_fallback({"elements": [{"tag": "button"}]}))

    def test_mix_uses_vision_when_dom_is_empty_or_invalid(self) -> None:
        self.assertTrue(mix_needs_vision_fallback({"elements": []}))
        self.assertTrue(mix_needs_vision_fallback({}))
        self.assertTrue(mix_needs_vision_fallback(None))


if __name__ == "__main__":
    unittest.main()
