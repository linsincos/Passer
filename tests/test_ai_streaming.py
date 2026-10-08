from __future__ import annotations

import json
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ai_chat


class _Response:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        for event in self.events:
            data = event if isinstance(event, str) else json.dumps(event)
            yield ("data: " + data + "\n").encode("utf-8")


class _Bubble:
    def __init__(self):
        self.text = ""
        self.frames = []

    def set_meta(self, _):
        pass

    def stream_update(self, text):
        self.text = text
        self.frames.append(text)


class StreamResponseTests(unittest.TestCase):
    def test_provider_stream_errors_are_reported_even_after_partial_text(self):
        for kind in ("openai", "anthropic"):
            for partial in (False, True):
                with self.subTest(kind=kind, partial=partial):
                    events = []
                    if partial:
                        events.append(
                            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "部分回复"}}
                            if kind == "anthropic" else
                            {"choices": [{"delta": {"content": "部分回复"}}]}
                        )
                    error = {"error": {"type": "overloaded_error", "message": "服务器繁忙"}}
                    if kind == "anthropic":
                        error["type"] = "error"
                    events.append(error)
                    chunks = []
                    with patch.object(ai_chat.urllib.request, "urlopen", return_value=_Response(events)):
                        with self.assertRaisesRegex(RuntimeError, "服务器繁忙"):
                            ai_chat._stream_llm_response(object(), 1, kind, chunks.append)
                    self.assertEqual(chunks, ["部分回复"] if partial else [])

    def test_stream_error_is_not_recorded_as_successful_call(self):
        with (
            patch.object(ai_chat.urllib.request, "urlopen", return_value=_Response([
                {"error": "上游请求失败"}, "[DONE]",
            ])),
            patch.object(ai_chat, "build_system_instructions", return_value="system"),
            patch.object(ai_chat, "record_token_usage") as record,
        ):
            with self.assertRaisesRegex(RuntimeError, "上游请求失败"):
                ai_chat.call_llm("talentsai", "test-key", [{"role": "user", "content": "你好"}], on_delta=lambda _: None)
        record.assert_not_called()

    def test_successful_stream_still_returns_text_and_usage(self):
        events = [
            {"choices": [{"delta": {"content": "你"}}]},
            {"choices": [{"delta": {"content": "好"}}]},
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}},
            "[DONE]",
        ]
        chunks = []
        with patch.object(ai_chat.urllib.request, "urlopen", return_value=_Response(events)):
            text, usage = ai_chat._stream_llm_response(object(), 1, "openai", chunks.append)
        self.assertEqual(text, "你好")
        self.assertEqual(chunks, ["你", "好"])
        self.assertEqual(usage["tokens"], 9)


class StreamQueueTests(unittest.TestCase):
    def make_bar(self):
        bar = ai_chat.AIChatBar.__new__(ai_chat.AIChatBar)
        bar._result_queue = queue.Queue()
        bar._stream_text = {}
        bar._is_stale_run = lambda _: False
        bar._stop_thinking_timer = lambda: None
        bar._schedule_layout = lambda: None
        bar.app = SimpleNamespace(root=Mock())
        bar.busy = True
        bar._polling = True
        bar.update_bubble = lambda b, text: setattr(b, "text", text)
        return bar

    def drain(self, bar):
        for _ in range(10):
            bar._poll_results()
            if not bar.busy:
                self.assertTrue(bar._result_queue.empty())
                return
        self.fail("result queue did not finish")

    def test_frame_budget_preserves_fifo_and_final_bubble(self):
        for count in (120, 121, 241, 361):
            with self.subTest(count=count):
                bar = self.make_bar()
                bubble = _Bubble()
                chunks = [f"[{i}]" for i in range(count)]
                full = "".join(chunks)
                at_finish = []

                def finish(b, reply, *args):
                    at_finish.append(bar._stream_text.pop(b, ""))
                    b.text = reply
                    bar.busy = False

                bar._finish = Mock(side_effect=finish)
                for chunk in chunks:
                    bar._result_queue.put(("delta", 1, bubble, chunk))
                bar._result_queue.put(("done", 1, bubble, full, None, {}, 0))
                self.drain(bar)
                self.assertEqual(at_finish, [full])
                self.assertEqual(bubble.text, full)
                self.assertTrue(all(full.startswith(frame) for frame in bubble.frames))
                self.assertNotIn(bubble, bar._stream_text)
                bar._finish.assert_called_once()

    def test_over_budget_delta_does_not_cross_reconnect_boundary(self):
        bar = self.make_bar()
        bubble = _Bubble()
        old_chunks = [f"[{i}]" for i in range(121)]
        observed = []
        accumulate = bar._accumulate_delta

        def record_delta(b, chunk):
            observed.append(chunk)
            accumulate(b, chunk)

        def update(b, text):
            observed.append("reconnect")
            b.text = text

        def finish(b, reply, *args):
            self.assertEqual(bar._stream_text.pop(b), reply)
            b.text = reply
            bar.busy = False

        bar._accumulate_delta = record_delta
        bar.update_bubble = update
        bar._finish = finish
        for chunk in old_chunks:
            bar._result_queue.put(("delta", 1, bubble, chunk))
        bar._result_queue.put(("reconnecting", 1, bubble, 1, 2))
        bar._result_queue.put(("delta", 1, bubble, "新回复"))
        bar._result_queue.put(("done", 1, bubble, "新回复", None, {}, 0))
        self.drain(bar)
        self.assertEqual(observed, [*old_chunks, "reconnect", "新回复"])
        self.assertEqual(bubble.text, "新回复")


if __name__ == "__main__":
    unittest.main()
