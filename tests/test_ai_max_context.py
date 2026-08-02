from __future__ import annotations

import json
import unittest
from unittest import mock

import ai_chat


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(
            {
                "choices": [{"message": {"content": "测试回答"}}],
                "usage": {
                    "prompt_tokens": 123,
                    "completion_tokens": 7,
                    "total_tokens": 130,
                },
            }
        ).encode("utf-8")


class DeepSeekMaxContextTests(unittest.TestCase):
    def test_long_prompt_is_sent_verbatim_without_passer_system_message(self):
        prompt = "  开始\n" + ("供热数据甲乙丙🙂" * 30_000) + "\n结束  "
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _FakeResponse()

        with mock.patch.object(ai_chat.urllib.request, "urlopen", side_effect=fake_urlopen), \
                mock.patch.object(ai_chat, "record_token_usage") as record_usage:
            reply, usage = ai_chat.call_deepseek_max_context(
                "test-key",
                prompt,
                model="deepseek-v4-pro",
                reasoning="max",
                thinking_mode="enabled",
            )

        payload = json.loads(captured["request"].data.decode("utf-8"))
        self.assertEqual(payload["messages"], [{"role": "user", "content": prompt}])
        self.assertNotIn("system", {message["role"] for message in payload["messages"]})
        self.assertNotIn("为节约 tokens", payload["messages"][0]["content"])
        self.assertEqual(reply, "测试回答")
        self.assertEqual(usage["input_tokens"], 123)
        record_usage.assert_called_once()

    def test_non_string_and_blank_prompts_are_rejected_before_network(self):
        with mock.patch.object(ai_chat.urllib.request, "urlopen") as urlopen:
            with self.assertRaises(TypeError):
                ai_chat.call_deepseek_max_context("test-key", None)
            with self.assertRaises(ValueError):
                ai_chat.call_deepseek_max_context("test-key", " \n\t ")
        urlopen.assert_not_called()

    def test_normal_chat_path_keeps_existing_six_thousand_character_limit(self):
        original = "x" * 70_000
        prepared = ai_chat._prepare_llm_history([{"role": "user", "content": original}])
        self.assertEqual(len(prepared), 1)
        self.assertLessEqual(len(prepared[0]["content"]), ai_chat.AI_CONTEXT_MESSAGE_MAX_CHARS)
        self.assertIn("为节约 tokens", prepared[0]["content"])


if __name__ == "__main__":
    unittest.main()
