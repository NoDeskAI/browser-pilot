import unittest

from app.routes.browser import mix_observe_strategy


class ObserveModeTests(unittest.TestCase):
    def test_mix_strategy_uses_visual_anchor_fusion(self) -> None:
        self.assertEqual(mix_observe_strategy(), "vision_anchor_fusion")


if __name__ == "__main__":
    unittest.main()
