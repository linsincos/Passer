from __future__ import annotations

import unittest

import ai_chat


class AiraMarkdownRenderingTests(unittest.TestCase):
    def test_code_tables_links_and_hidden_markers_are_detected(self):
        text = (
            "查看 [官网](https://example.com/docs) 和 `inline`。\n\n"
            "| 名称 | 值 |\n| --- | --- |\n| A | 1 |\n\n"
            "```python\nprint('ok')\n```"
        )
        ranges = ai_chat._aira_markdown_ranges(text)
        visible = ai_chat._aira_markdown_visible_text(text, ranges)
        self.assertEqual(ranges["code"][0][2], "print('ok')")
        self.assertTrue(ranges["tables"])
        self.assertEqual(ranges["links"][0][2], "https://example.com/docs")
        self.assertIn("官网", visible)
        self.assertNotIn("```", visible)
        self.assertNotIn("](https://", visible)

    def test_bubble_only_spacing_setter_skips_media_cards(self):
        Bubble = type(
            "Bubble", (ai_chat._RoundedBubble,),
            {"__init__": lambda self: setattr(self, "calls", []),
             "configure": lambda self, **kwargs: self.calls.append(kwargs)},
        )
        Media = type(
            "Media", (),
            {"__init__": lambda self: setattr(self, "calls", []),
             "configure": lambda self, **kwargs: self.calls.append(kwargs)},
        )
        bubble = Bubble()
        media = Media()
        bar = ai_chat.AIChatBar.__new__(ai_chat.AIChatBar)
        bar._bubbles = [(bubble, "assistant"), (media, "assistant")]
        bar._schedule_layout = lambda: None
        bar.set_bubble_line_spacing(5)
        self.assertEqual(bubble.calls, [{"line_spacing": 5}])
        self.assertEqual(media.calls, [])

    def test_browser_panel_tracks_site_steps_and_trust_state(self):
        class Label:
            def __init__(self):
                self.options = {}

            def configure(self, **kwargs):
                self.options.update(kwargs)

        class Bridge:
            @staticmethod
            def is_trusted(url):
                return url.startswith("https://example.com")

        bar = ai_chat.AIChatBar.__new__(ai_chat.AIChatBar)
        bar._browser_activity = []
        bar._browser_current_url = ""
        bar._browser_panel_visible = False
        bar.browser_permission_label = Label()
        bar.browser_site_label = Label()
        bar.browser_steps_label = Label()
        bar.browser_trust_button = Label()
        bar.app = type("App", (), {"browser_bridge": Bridge()})()
        bar._schedule_layout = lambda: None
        bar.update_browser_activity(
            "browser_navigate", {"url": "https://example.com/docs"}, status="完成"
        )
        self.assertTrue(bar._browser_panel_visible)
        self.assertIn("example.com/docs", bar.browser_site_label.options["text"])
        self.assertEqual(bar.browser_trust_button.options["text"], "取消信任")
        self.assertIn("browser_navigate", bar.browser_steps_label.options["text"])


if __name__ == "__main__":
    unittest.main()
