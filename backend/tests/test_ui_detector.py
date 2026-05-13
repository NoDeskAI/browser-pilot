from __future__ import annotations

import unittest

from app.tools.vision.ui_detector import (
    apply_ocr_hints,
    attach_dom_hints,
    build_vision_groups,
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
        self.assertEqual(candidates[2]["rawLabel"], "Others")
        self.assertEqual(candidates[2]["modelFamily"], "unknown")
        self.assertEqual(candidates[2]["semanticSource"], "model_unknown")
        self.assertEqual(candidates[2]["geometryHint"], "logo")

    def test_normalize_family(self) -> None:
        self.assertEqual(normalize_family("Input_Elements"), "input")
        self.assertEqual(normalize_family("Information_Display"), "text")
        self.assertEqual(normalize_family("Unknown"), "unknown")

    def test_unknown_geometry_refines_common_web_ui_families(self) -> None:
        class FakeGeometryResult:
            names = {8: "Unknown"}
            boxes = FakeBoxes(
                [
                    FakeBox([16, 18, 116, 54], 0.71, 8),
                    FakeBox([430, 24, 850, 64], 0.7, 8),
                    FakeBox([1180, 28, 1220, 68], 0.69, 8),
                    FakeBox([460, 320, 760, 390], 0.68, 8),
                    FakeBox([410, 430, 910, 690], 0.67, 8),
                    FakeBox([460, 710, 760, 728], 0.66, 8),
                ]
            )

        candidates = results_to_candidates(
            results=[FakeGeometryResult()],
            width=1280,
            height=800,
            max_candidates=10,
        )

        self.assertEqual(
            [candidate["family"] for candidate in candidates],
            ["unknown", "unknown", "unknown", "unknown", "unknown", "unknown"],
        )
        self.assertEqual(
            [candidate["geometryHint"] for candidate in candidates],
            ["logo", "input", "icon", "button", "visual", "text"],
        )
        self.assertTrue(all(candidate["semanticSource"] == "model_unknown" for candidate in candidates))

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

    def test_build_vision_groups_detects_video_card_toolbar_and_nav_cluster(self) -> None:
        candidates = [
            {
                "id": "vis-001",
                "bbox": {"x": 120, "y": 150, "w": 320, "h": 180},
                "center": {"x": 280, "y": 240},
                "score": 0.8,
                "label": "visual",
                "family": "visual",
                "source": "uitag_yolo11s",
            },
            {
                "id": "vis-002",
                "bbox": {"x": 470, "y": 160, "w": 420, "h": 34},
                "center": {"x": 680, "y": 177},
                "score": 0.76,
                "label": "text",
                "family": "text",
                "source": "uitag_yolo11s",
            },
            {
                "id": "vis-003",
                "bbox": {"x": 470, "y": 210, "w": 120, "h": 32},
                "center": {"x": 530, "y": 226},
                "score": 0.72,
                "label": "button",
                "family": "button",
                "source": "uitag_yolo11s",
            },
            {
                "id": "vis-004",
                "bbox": {"x": 20, "y": 18, "w": 80, "h": 34},
                "center": {"x": 60, "y": 35},
                "score": 0.7,
                "label": "logo",
                "family": "logo",
                "source": "uitag_yolo11s",
            },
            {
                "id": "vis-005",
                "bbox": {"x": 220, "y": 18, "w": 360, "h": 40},
                "center": {"x": 400, "y": 38},
                "score": 0.7,
                "label": "input",
                "family": "input",
                "source": "uitag_yolo11s",
            },
            {
                "id": "vis-006",
                "bbox": {"x": 600, "y": 18, "w": 90, "h": 36},
                "center": {"x": 645, "y": 36},
                "score": 0.7,
                "label": "button",
                "family": "button",
                "source": "uitag_yolo11s",
            },
            {
                "id": "vis-007",
                "bbox": {"x": 20, "y": 90, "w": 44, "h": 44},
                "center": {"x": 42, "y": 112},
                "score": 0.7,
                "label": "icon",
                "family": "icon",
                "source": "uitag_yolo11s",
            },
            {
                "id": "vis-008",
                "bbox": {"x": 20, "y": 150, "w": 44, "h": 44},
                "center": {"x": 42, "y": 172},
                "score": 0.7,
                "label": "icon",
                "family": "icon",
                "source": "uitag_yolo11s",
            },
        ]

        groups = build_vision_groups(candidates, width=1280, height=800, max_groups=10)
        families = {group["family"] for group in groups}

        self.assertIn("video_card", families)
        self.assertIn("toolbar", families)
        self.assertIn("nav_cluster", families)
        self.assertTrue(any(candidate.get("parentId") for candidate in candidates))

    def test_apply_ocr_hints_adds_text_hint_and_refines_semantics(self) -> None:
        candidates = [
            {
                "id": "vis-001",
                "bbox": {"x": 10, "y": 10, "w": 200, "h": 40},
                "center": {"x": 110, "y": 30},
                "score": 0.5,
                "label": "unknown",
                "family": "unknown",
                "semanticSource": "model_unknown",
            }
        ]
        ocr_items = [
            {"text": "Search cats", "score": 0.98, "bbox": {"x": 20, "y": 16, "w": 150, "h": 24}}
        ]

        apply_ocr_hints(candidates, ocr_items)

        self.assertEqual(candidates[0]["textHint"], "Search cats")
        self.assertEqual(candidates[0]["family"], "input")
        self.assertEqual(candidates[0]["semanticSource"], "ocr")

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

        groups = [
            {
                "id": "group-001",
                "bbox": {"x": 190, "y": 190, "w": 120, "h": 70},
                "center": {"x": 250, "y": 225},
                "score": 0.8,
                "mixScore": 0.9,
                "label": "video_card",
                "family": "video_card",
                "children": ["vis-001"],
            }
        ]

        mixed = build_mixed_candidates(
            elements=elements,
            vision_candidates=vision,
            vision_groups=groups,
            max_candidates=10,
        )

        self.assertEqual(mixed[0]["source"], "vision_group")
        self.assertEqual(mixed[0]["family"], "video_card")
        self.assertIn("dom", {candidate["source"] for candidate in mixed})
        self.assertIn("vision_group", {candidate["source"] for candidate in mixed})
        self.assertIn("uitag_yolo11s", {candidate["source"] for candidate in mixed})

    def test_build_mixed_candidates_reserves_slots_for_vision(self) -> None:
        elements = [
            {
                "tag": "button",
                "text": f"Button {index}",
                "attrs": {"role": "button"},
                "x": 20 + index,
                "y": 20,
                "bbox": {"x": 5 + index, "y": 5, "w": 30, "h": 30},
            }
            for index in range(20)
        ]
        vision = [
            {
                "id": f"vis-{index:03d}",
                "bbox": {"x": 200 + index, "y": 200, "w": 80, "h": 40},
                "center": {"x": 240 + index, "y": 220},
                "score": 0.65,
                "label": "visual",
                "family": "visual",
                "source": "uitag_yolo11s",
            }
            for index in range(5)
        ]

        mixed = build_mixed_candidates(elements=elements, vision_candidates=vision, max_candidates=10)

        self.assertEqual(len(mixed), 10)
        self.assertGreaterEqual(
            len([candidate for candidate in mixed if candidate["source"] == "uitag_yolo11s"]),
            4,
        )

    def test_build_mixed_candidates_filters_zero_bbox_dom(self) -> None:
        elements = [
            {
                "tag": "button",
                "text": "Skip navigation",
                "attrs": {"role": "button"},
                "x": 0,
                "y": 0,
                "bbox": {"x": 0, "y": 0, "w": 0, "h": 0},
            },
            {
                "tag": "button",
                "text": "Search",
                "attrs": {"role": "button"},
                "x": 120,
                "y": 40,
                "bbox": {"x": 80, "y": 20, "w": 80, "h": 40},
            },
        ]

        mixed = build_mixed_candidates(elements=elements, vision_candidates=[], max_candidates=5)

        self.assertEqual([candidate["label"] for candidate in mixed], ["Search"])


if __name__ == "__main__":
    unittest.main()
