from __future__ import annotations

import unittest

from app.tools.vision.ui_detector import (
    attach_dom_hints,
    build_mixed_candidates,
    normalize_family,
    results_to_candidates,
)


class FakeTensor:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return self._values


class FakeBox:
    def __init__(self, xyxy, conf, cls):
        self.xyxy = [FakeTensor(xyxy)]
        self.conf = [conf]
        self.cls = [cls]


class FakeBoxes(list):
    pass


class FakeResult:
    def __init__(self):
        self.names = {
            0: "Button",
            2: "Input_Elements",
            3: "Navigation",
            7: "Others",
            8: "Unknown",
        }
        self.boxes = FakeBoxes(
            [
                FakeBox([10.2, 20.2, 110.6, 60.7], 0.41, 7),
                FakeBox([200.1, 10.0, 260.0, 40.2], 0.92, 0),
                FakeBox([-5.0, 100.0, 100.0, 160.0], 0.81, 2),
            ]
        )


class UiDetectorTests(unittest.TestCase):
    def test_results_to_candidates_sorts_and_normalizes(self) -> None:
        candidates = results_to_candidates(
            results=[FakeResult()],
            width=300,
            height=200,
            max_candidates=3,
        )

        self.assertEqual([item["id"] for item in candidates], ["vis-001", "vis-002", "vis-003"])
        self.assertEqual([item["family"] for item in candidates], ["button", "input", "unknown"])
        self.assertEqual(candidates[0]["center"], {"x": 230, "y": 25})
        self.assertEqual(candidates[1]["bbox"], {"x": 0, "y": 100, "w": 100, "h": 60})

    def test_normalize_family(self) -> None:
        self.assertEqual(normalize_family("Input_Elements"), "input")
        self.assertEqual(normalize_family("Information_Display"), "text")
        self.assertEqual(normalize_family("Unknown"), "unknown")

    def test_attach_dom_hints_when_center_is_inside_bbox(self) -> None:
        vision = [
            {
                "id": "vis-001",
                "bbox": {"x": 10, "y": 10, "w": 100, "h": 60},
                "center": {"x": 60, "y": 40},
                "score": 0.9,
                "label": "button",
                "family": "button",
                "source": "uitag_yolo11s",
            }
        ]
        elements = [
            {
                "tag": "button",
                "text": "Search",
                "attrs": {"role": "button"},
                "x": 70,
                "y": 42,
                "bbox": {"x": 20, "y": 20, "w": 80, "h": 32},
            }
        ]

        enriched = attach_dom_hints(vision, elements)

        self.assertEqual(enriched[0]["domHint"]["text"], "Search")
        self.assertEqual(enriched[0]["domHint"]["tag"], "button")

    def test_build_mixed_candidates_keeps_dom_first_and_unmatched_vision(self) -> None:
        elements = [
            {
                "tag": "a",
                "text": "Home",
                "attrs": {"role": "link"},
                "x": 20,
                "y": 20,
                "bbox": {"x": 5, "y": 5, "w": 30, "h": 30},
            }
        ]
        vision = [
            {
                "id": "vis-001",
                "bbox": {"x": 200, "y": 200, "w": 80, "h": 40},
                "center": {"x": 240, "y": 220},
                "score": 0.7,
                "label": "unknown",
                "family": "unknown",
                "source": "uitag_yolo11s",
            }
        ]

        mixed = build_mixed_candidates(elements=elements, vision_candidates=vision, max_candidates=10)

        self.assertEqual(mixed[0]["source"], "dom")
        self.assertEqual(mixed[0]["family"], "link")
        self.assertEqual(mixed[1]["source"], "uitag_yolo11s")


if __name__ == "__main__":
    unittest.main()
