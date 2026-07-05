from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from desktop import qt_compat
from tool import run_render_fps_report


class RenderFpsReportTests(unittest.TestCase):
    def test_normalize_evidence_scope_defaults_to_synthetic(self) -> None:
        self.assertEqual(run_render_fps_report._normalize_evidence_scope(None), "synthetic")
        self.assertEqual(run_render_fps_report._normalize_evidence_scope(""), "synthetic")
        self.assertEqual(run_render_fps_report._normalize_evidence_scope("legacy"), "synthetic")

    def test_normalize_evidence_scope_accepts_formal_onscreen(self) -> None:
        self.assertEqual(
            run_render_fps_report._normalize_evidence_scope("formal_gui_onscreen"),
            "formal_gui_onscreen",
        )
        self.assertEqual(
            run_render_fps_report._normalize_evidence_scope(" FORMAL_GUI_ONSCREEN "),
            "formal_gui_onscreen",
        )

    def test_is_onscreen_session_rejects_offscreen(self) -> None:
        self.assertFalse(
            run_render_fps_report._is_onscreen_session(
                qpa_platform="offscreen",
                screen_count=1,
                widget_visible=True,
            )
        )

    def test_is_onscreen_session_requires_visible_widget_and_screen(self) -> None:
        self.assertFalse(
            run_render_fps_report._is_onscreen_session(
                qpa_platform="windows",
                screen_count=0,
                widget_visible=True,
            )
        )
        self.assertFalse(
            run_render_fps_report._is_onscreen_session(
                qpa_platform="windows",
                screen_count=1,
                widget_visible=False,
            )
        )
        self.assertTrue(
            run_render_fps_report._is_onscreen_session(
                qpa_platform="windows",
                screen_count=1,
                widget_visible=True,
            )
        )

    def test_headless_qpa_detection_falls_back_when_linux_xcb_cursor_missing(self) -> None:
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            with patch.object(qt_compat, "find_library", side_effect=lambda name: None if "cursor" in name else "libxcb.so.1"):
                with patch.object(qt_compat, "sys") as sys_module:
                    sys_module.platform = "linux"
                    self.assertTrue(qt_compat._should_force_offscreen_qpa())


if __name__ == "__main__":
    unittest.main()
