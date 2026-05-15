import unittest

from app.tools.vision.ui_detector import (
    DEFAULT_UI_MODEL_FILENAME,
    DEFAULT_UI_MODEL_URL,
    VisionFrame,
    apply_dom_hint_semantics,
    build_mixed_candidates,
    inference_bbox_to_click_bbox,
    is_usable_dom_element,
    missing_default_model_message,
    normalize_family,
    omniparser_bbox_to_pixel_box,
    omniparser_items_to_candidates,
    refine_candidate_semantics,
    resolve_model_path,
    target_inference_size,
)


class UiDetectorFallbackTests(unittest.TestCase):
    def test_geometry_promotes_unknown_action_box(self) -> None:
        candidate = {
            "id": "vis-001",
            "bbox": {"x": 320, "y": 120, "w": 180, "h": 44},
            "center": {"x": 410, "y": 142},
            "score": 0.31,
            "family": "unknown",
            "label": "unknown",
            "rawLabel": "Others",
            "modelFamily": "unknown",
            "semanticSource": "model",
        }

        refine_candidate_semantics(candidate, width=1280, height=800)

        self.assertEqual(candidate["family"], "button")
        self.assertEqual(candidate["label"], "button")
        self.assertEqual(candidate["modelFamily"], "unknown")
        self.assertEqual(candidate["semanticSource"], "geometry")
        self.assertEqual(candidate["semanticConfidence"], "weak")

    def test_dom_hint_can_promote_unknown_to_control_family(self) -> None:
        candidate = {
            "id": "vis-001",
            "bbox": {"x": 100, "y": 100, "w": 96, "h": 36},
            "center": {"x": 148, "y": 118},
            "score": 0.4,
            "family": "unknown",
            "label": "unknown",
            "semanticSource": "model_unknown",
        }
        hint = {
            "tag": "button",
            "text": "Subscribe",
            "attrs": {"ariaLabel": "Subscribe"},
            "center": {"x": 148, "y": 118},
            "bbox": {"x": 100, "y": 100, "w": 96, "h": 36},
        }

        apply_dom_hint_semantics(candidate, hint)

        self.assertEqual(candidate["family"], "button")
        self.assertEqual(candidate["label"], "button")
        self.assertEqual(candidate["textHint"], "Subscribe")
        self.assertEqual(candidate["semanticSource"], "dom_hint")

    def test_mix_filters_duplicate_button_but_keeps_visual_supplement(self) -> None:
        elements = [
            {
                "tag": "button",
                "text": "Search",
                "attrs": {},
                "x": 145,
                "y": 118,
                "bbox": {"x": 100, "y": 100, "w": 90, "h": 36},
            }
        ]
        vision_candidates = [
            {
                "id": "vis-001",
                "bbox": {"x": 99, "y": 99, "w": 92, "h": 38},
                "center": {"x": 145, "y": 118},
                "score": 0.72,
                "family": "button",
                "label": "button",
                "source": "yolov8_ui",
            },
            {
                "id": "vis-002",
                "bbox": {"x": 220, "y": 180, "w": 320, "h": 180},
                "center": {"x": 380, "y": 270},
                "score": 0.64,
                "family": "visual",
                "label": "visual",
                "source": "yolov8_ui",
            },
        ]

        mixed = build_mixed_candidates(
            elements=elements,
            vision_candidates=vision_candidates,
            vision_groups=[],
            max_candidates=10,
        )

        ids = [item["id"] for item in mixed]
        self.assertIn("dom-001", ids)
        self.assertIn("vis-002", ids)
        self.assertNotIn("vis-001", ids)
        self.assertEqual(next(item for item in mixed if item["id"] == "vis-002")["kind"], "vision_supplement")

    def test_mix_keeps_group_as_visual_context(self) -> None:
        elements = [
            {
                "tag": "a",
                "text": "Video title",
                "attrs": {"href": "/watch"},
                "x": 540,
                "y": 410,
                "bbox": {"x": 420, "y": 390, "w": 240, "h": 40},
            }
        ]
        vision_groups = [
            {
                "id": "group-001",
                "bbox": {"x": 220, "y": 180, "w": 520, "h": 270},
                "center": {"x": 480, "y": 315},
                "score": 0.71,
                "mixScore": 0.83,
                "family": "video_card",
                "label": "video_card",
            }
        ]

        mixed = build_mixed_candidates(
            elements=elements,
            vision_candidates=[],
            vision_groups=vision_groups,
            max_candidates=10,
        )

        self.assertIn("group-001", [item["id"] for item in mixed])
        group = next(item for item in mixed if item["id"] == "group-001")
        self.assertEqual(group["kind"], "vision_supplement_group")
        self.assertEqual(group["supplementReason"], "visual_group")

    def test_degenerate_dom_bbox_is_not_usable(self) -> None:
        base = {
            "tag": "button",
            "text": "Search",
            "attrs": {},
            "x": 145,
            "y": 118,
        }

        self.assertFalse(is_usable_dom_element({**base, "bbox": {"x": 100, "y": 100, "w": 0, "h": 36}}))
        self.assertFalse(is_usable_dom_element({**base, "bbox": {"x": 100, "y": 100, "w": 90, "h": 0}}))
        self.assertTrue(is_usable_dom_element({**base, "bbox": {"x": 100, "y": 100, "w": 90, "h": 36}}))

    def test_omniparser_ratio_bbox_and_semantics_convert_to_candidates(self) -> None:
        items = [
            {
                "type": "text",
                "bbox": [0.1, 0.2, 0.4, 0.26],
                "interactivity": False,
                "content": "Search",
                "source": "box_ocr_content_ocr",
            },
            {
                "type": "icon",
                "bbox": [120, 160, 168, 208],
                "interactivity": True,
                "content": "three dots more menu",
                "source": "box_yolo_content_yolo",
            },
        ]

        candidates = omniparser_items_to_candidates(items, width=1000, height=500, max_candidates=10)

        self.assertEqual(candidates[0]["source"], "omniparser")
        self.assertEqual(candidates[0]["family"], "menu_item")
        self.assertEqual(candidates[0]["semanticSource"], "omniparser_caption")
        self.assertEqual(candidates[1]["family"], "input")
        self.assertEqual(candidates[1]["bbox"], {"x": 100, "y": 100, "w": 300, "h": 30})

    def test_omniparser_bbox_converter_accepts_pixel_and_ratio_boxes(self) -> None:
        self.assertEqual(
            omniparser_bbox_to_pixel_box([0.2, 0.25, 0.4, 0.5], width=1000, height=800),
            {"x": 200, "y": 200, "w": 200, "h": 200},
        )
        self.assertEqual(
            omniparser_bbox_to_pixel_box([20, 30, 120, 80], width=1000, height=800),
            {"x": 20, "y": 30, "w": 100, "h": 50},
        )

    def test_vision_frame_downsamples_and_maps_back_to_click_viewport(self) -> None:
        self.assertEqual(target_inference_size(1920, 1080), (1280, 720))
        frame = VisionFrame(
            raw_base64="raw",
            inference_base64="inference",
            raw_image=None,
            inference_image=None,
            raw_size={"width": 1920, "height": 1080},
            inference_size={"width": 1280, "height": 720},
            click_viewport={"width": 960, "height": 540},
            raw_to_inference_scale={"x": 0.666667, "y": 0.666667},
            inference_to_raw_scale={"x": 1.5, "y": 1.5},
            raw_to_click_scale={"x": 0.5, "y": 0.5},
        )

        self.assertEqual(frame.raw_size, {"width": 1920, "height": 1080})
        self.assertEqual(frame.inference_size, {"width": 1280, "height": 720})
        self.assertEqual(frame.click_viewport, {"width": 960, "height": 540})
        self.assertEqual(
            inference_bbox_to_click_bbox({"x": 640, "y": 360, "w": 160, "h": 80}, frame),
            {"x": 480, "y": 270, "w": 120, "h": 60},
        )

    def test_yolov8_ui_labels_normalize_to_browser_families(self) -> None:
        self.assertEqual(normalize_family("button"), "button")
        self.assertEqual(normalize_family("field"), "input")
        self.assertEqual(normalize_family("heading"), "text")
        self.assertEqual(normalize_family("label"), "text")
        self.assertEqual(normalize_family("image"), "visual")
        self.assertEqual(normalize_family("iframe"), "visual")
        self.assertEqual(normalize_family("link"), "nav_item")

    def test_missing_model_message_points_to_download_url(self) -> None:
        message = missing_default_model_message("missing")

        self.assertIn(DEFAULT_UI_MODEL_URL, message)
        self.assertIn(DEFAULT_UI_MODEL_FILENAME, message)
        self.assertIn("BP_UI_DETECTOR_MODEL", message)

    def test_resolve_model_path_raises_download_hint_for_bad_env_path(self) -> None:
        import os

        old_value = os.environ.get("BP_UI_DETECTOR_MODEL")
        os.environ["BP_UI_DETECTOR_MODEL"] = "/tmp/browser-pilot-missing-yolo.pt"
        try:
            with self.assertRaisesRegex(RuntimeError, "YOLOv8 UI detector weight is not installed"):
                resolve_model_path()
        finally:
            if old_value is None:
                os.environ.pop("BP_UI_DETECTOR_MODEL", None)
            else:
                os.environ["BP_UI_DETECTOR_MODEL"] = old_value


if __name__ == "__main__":
    unittest.main()
