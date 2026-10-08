from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import ai_chat


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class TalentsAIProviderTests(unittest.TestCase):
    def test_provider_uses_documented_openai_compatible_endpoints(self):
        cfg = ai_chat.PROVIDERS["talentsai"]
        self.assertEqual(cfg["endpoint"], "https://llm-cn.humanlaya.com/v1/chat/completions")
        self.assertEqual(cfg["models_endpoint"], "https://llm-cn.humanlaya.com/v1/models")
        self.assertEqual(cfg["kind"], "openai")
        self.assertIn("talentsai", ai_chat.PROVIDER_ORDER)

    def test_online_models_use_bearer_key(self):
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            return _Response({"data": [
                {"id": "external-gpt-5.6-sol"},
                {"id": "external-claude-opus-4-8"},
            ]})

        with patch.object(ai_chat.urllib.request, "urlopen", fake_urlopen):
            models = ai_chat.list_provider_models("talentsai", "sk-private-test")

        self.assertEqual(captured["url"], "https://llm-cn.humanlaya.com/v1/models")
        self.assertEqual(captured["authorization"], "Bearer sk-private-test")
        self.assertEqual(models, ["external-gpt-5.6-sol", "external-claude-opus-4-8"])

    def test_chat_request_uses_selected_model_and_reasoning_effort(self):
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _Response({
                "choices": [{"message": {"content": "已连接"}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
            })

        with (
            patch.object(ai_chat.urllib.request, "urlopen", fake_urlopen),
            patch.object(ai_chat, "record_token_usage", lambda *args, **kwargs: None),
            patch.object(ai_chat, "build_system_instructions", lambda *args, **kwargs: "system"),
        ):
            text, usage = ai_chat.call_llm(
                "talentsai",
                "sk-private-test",
                [{"role": "user", "content": "你好"}],
                model="external-gpt-5.6-sol",
                reasoning="max",
                thinking_mode="auto",
            )

        self.assertEqual(text, "已连接")
        self.assertEqual(usage["tokens"], 10)
        self.assertEqual(captured["url"], "https://llm-cn.humanlaya.com/v1/chat/completions")
        self.assertEqual(captured["authorization"], "Bearer sk-private-test")
        self.assertEqual(captured["payload"]["model"], "external-gpt-5.6-sol")
        self.assertEqual(captured["payload"]["reasoning_effort"], "xhigh")
        self.assertEqual(
            captured["payload"]["messages"],
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "你好"},
            ],
        )

    def test_max_reasoning_stays_high_for_gateway_claude_models(self):
        self.assertEqual(
            ai_chat._reasoning_effort_for_provider(
                "talentsai", "max", "external-claude-opus-4-8"
            ),
            "high",
        )


if __name__ == "__main__":
    unittest.main()
