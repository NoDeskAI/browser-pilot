from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "collect_web_ui_dataset.py"
spec = importlib.util.spec_from_file_location("collect_web_ui_dataset", MODULE_PATH)
collector = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(collector)


class DatasetCollectorTests(unittest.TestCase):
    def test_label_from_button_element(self) -> None:
        label = collector.label_from_element(
            {
                "tag": "button",
                "text": "Search",
                "attrs": {"role": "button"},
                "bbox": {"x": 100, "y": 20, "w": 120, "h": 40},
                "scope": "document",
            },
            viewport={"width": 1280, "height": 720},
            index=1,
        )

        self.assertIsNotNone(label)
        self.assertEqual(label["category"], "button")
        self.assertEqual(label["classId"], collector.CLASS_INDEX["button"])
        self.assertGreater(label["confidence"], 0.9)

    def test_label_from_video_link_card(self) -> None:
        label = collector.label_from_element(
            {
                "tag": "a",
                "text": "Best Cats Being Hilarious",
                "attrs": {"href": "/watch?v=abc"},
                "bbox": {"x": 320, "y": 220, "w": 520, "h": 310},
                "scope": "document",
            },
            viewport={"width": 1280, "height": 720},
            index=1,
        )

        self.assertIsNotNone(label)
        self.assertEqual(label["category"], "video_card")
        self.assertTrue(label["needsReview"])

    def test_build_labels_dedupes_same_category_overlap(self) -> None:
        observe = {
            "elements": [
                {"tag": "button", "text": "Create", "attrs": {}, "bbox": {"x": 10, "y": 10, "w": 100, "h": 40}},
                {"tag": "button", "text": "Create", "attrs": {}, "bbox": {"x": 12, "y": 12, "w": 98, "h": 38}},
            ],
            "viewport": {"width": 1280, "height": 720},
        }

        labels = collector.build_labels(observe, include_low_confidence=False)

        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0]["category"], "button")

    def test_low_confidence_text_block_is_filtered_by_default(self) -> None:
        observe = {
            "elements": [
                {"tag": "span", "text": "Plain text", "attrs": {}, "bbox": {"x": 420, "y": 260, "w": 90, "h": 20}},
            ],
            "viewport": {"width": 1280, "height": 720},
        }

        self.assertEqual(collector.build_labels(observe, include_low_confidence=False), [])
        self.assertEqual(len(collector.build_labels(observe, include_low_confidence=True)), 1)

    def test_select_explore_targets_prefers_cards_and_spatial_diversity(self) -> None:
        labels = [
            {"category": "link", "confidence": 0.9, "bbox": {"x": 10, "y": 10, "w": 80, "h": 30}, "center": {"x": 50, "y": 25}},
            {"category": "video_card", "confidence": 0.8, "bbox": {"x": 300, "y": 180, "w": 420, "h": 260}, "center": {"x": 510, "y": 310}},
            {"category": "button", "confidence": 0.9, "bbox": {"x": 320, "y": 220, "w": 100, "h": 38}, "center": {"x": 370, "y": 239}},
            {"category": "image", "confidence": 0.8, "bbox": {"x": 820, "y": 180, "w": 220, "h": 180}, "center": {"x": 930, "y": 270}},
        ]

        selected = collector.select_explore_targets(
            labels,
            categories={"video_card", "image", "link", "button"},
            limit=2,
            viewport={"width": 1280, "height": 720},
        )

        self.assertEqual([item["category"] for item in selected], ["video_card", "image"])


if __name__ == "__main__":
    unittest.main()
