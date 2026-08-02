from __future__ import annotations

# Loaded into the parent module's global namespace by the parent file.
# Keep this file focused on the extracted feature area.

PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "endpoint": "https://api.deepseek.com/chat/completions",
        "models_endpoint": "https://api.deepseek.com/models",
        "model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "kind": "openai",
    },
    "claude": {
        "name": "Claude",
        "endpoint": "https://api.anthropic.com/v1/messages",
        "models_endpoint": "https://api.anthropic.com/v1/models",
        "model": "claude-sonnet-4-6",
        "models": [
            "claude-opus-4-8",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ],
        "kind": "anthropic",
    },
    "gpt": {
        "name": "GPT",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "models_endpoint": "https://api.openai.com/v1/models",
        "model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini"],
        "kind": "openai",
    },
    "gemini": {
        "name": "Gemini",
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "models_endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/models",
        "model": "gemini-2.5-flash",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"],
        "kind": "openai",
    },
    "grok": {
        "name": "Grok",
        "endpoint": "https://api.x.ai/v1/chat/completions",
        "models_endpoint": "https://api.x.ai/v1/models",
        "model": "grok-3",
        "models": ["grok-4", "grok-3", "grok-3-mini", "grok-3-fast"],
        "kind": "openai",
    },
    "qwen": {
        "name": "通义千问",
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "models_endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
        "model": "qwen-plus",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-long"],
        "kind": "openai",
    },
    "moonshot": {
        "name": "Kimi",
        "endpoint": "https://api.moonshot.cn/v1/chat/completions",
        "models_endpoint": "https://api.moonshot.cn/v1/models",
        "model": "moonshot-v1-8k",
        "models": ["kimi-k2-0711-preview", "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "kind": "openai",
    },
    "glm": {
        "name": "智谱GLM",
        "endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4.6",
        "models": ["glm-4.6", "glm-4.5", "glm-4-plus", "glm-4-air", "glm-4-flash"],
        "kind": "openai",
    },
}
PROVIDER_ORDER = ("deepseek", "claude", "gpt", "gemini", "grok", "qwen", "moonshot", "glm")
NAME_TO_KEY = {cfg["name"]: key for key, cfg in PROVIDERS.items()}


def provider_display(key: str) -> str:
    return PROVIDERS.get(key, PROVIDERS["deepseek"])["name"]


def provider_models(key: str) -> list[str]:
    """返回某服务商在当前 API 下可选的模型列表（默认模型排在首位）。"""
    cfg = PROVIDERS.get(key, PROVIDERS["deepseek"])
    models = list(cfg.get("models") or [])
    if cfg["model"] not in models:
        models.insert(0, cfg["model"])
    return list(dict.fromkeys(models))


def normalize_model(key: str, model: str | None) -> str:
    """把模型名限定在该服务商已支持的模型内；非法时回退默认模型。"""
    cfg = PROVIDERS.get(key, PROVIDERS["deepseek"])
    return model if model in provider_models(key) else cfg["model"]


THINKING_MODES = ("auto", "enabled", "disabled")
THINKING_MODE_LABELS = {"auto": "自动", "enabled": "开启", "disabled": "关闭"}
REASONING_LEVELS = ("auto", "low", "medium", "high", "max")
REASONING_LABELS = {"auto": "自动", "low": "低", "medium": "中", "high": "高", "max": "极高"}
# Anthropic 扩展思考的 thinking budget（tokens），随档位提升。
_THINKING_BUDGET = {"low": 2048, "medium": 8192, "high": 16384, "max": 32768}


def _llm_token_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _extract_llm_usage(usage, kind: str) -> dict:
    if not isinstance(usage, dict):
        return normalize_token_usage(0)
    if kind == "anthropic":
        input_base = _llm_token_int(usage.get("input_tokens"))
        cache_read = _llm_token_int(usage.get("cache_read_input_tokens"))
        cache_write = _llm_token_int(usage.get("cache_creation_input_tokens"))
        output_tokens = _llm_token_int(usage.get("output_tokens"))
        input_tokens = input_base + cache_read + cache_write
        return normalize_token_usage({
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "total_tokens": input_tokens + output_tokens,
        })
    prompt_tokens = _llm_token_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion_tokens = _llm_token_int(usage.get("completion_tokens") or usage.get("output_tokens"))
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached_tokens = 0
    if isinstance(details, dict):
        cached_tokens = _llm_token_int(details.get("cached_tokens"))
    cached_tokens = cached_tokens or _llm_token_int(usage.get("prompt_cache_hit_tokens"))
    cache_miss = _llm_token_int(usage.get("prompt_cache_miss_tokens"))
    if not prompt_tokens and (cached_tokens or cache_miss):
        prompt_tokens = cached_tokens + cache_miss
    total = _llm_token_int(usage.get("total_tokens"))
    if not total:
        total = prompt_tokens + completion_tokens
    return normalize_token_usage({
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "cache_read_tokens": cached_tokens,
        "cache_miss_tokens": cache_miss,
        "total_tokens": total,
    })


def _merge_token_usage(*items) -> dict:
    merged = normalize_token_usage(0)
    for item in items:
        usage = normalize_token_usage(item)
        for key in merged:
            merged[key] += usage.get(key, 0)
    return merged


def _prompt_cache_key(provider_key: str, model_id: str, persona: str | None,
                      permission: str | None) -> str:
    seed = f"passer:{provider_key}:{model_id}:{persona or ''}:{permission or ''}"
    return "passer-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def normalize_reasoning(level: str | None) -> str:
    value = str(level or "auto").strip().lower()
    if value == "off":
        value = "auto"
    return value if value in REASONING_LEVELS else "auto"


def normalize_thinking_mode(mode: str | None) -> str:
    value = str(mode or "auto").strip().lower()
    return value if value in THINKING_MODES else "auto"


def model_supports_reasoning(provider_key: str, model: str | None) -> bool:
    """该模型是否支持可调“思考程度”（OpenAI reasoning_effort / Anthropic 扩展思考）。"""
    name = str(model or "")
    if provider_key == "deepseek":
        return name.startswith("deepseek-")
    if provider_key == "gpt":
        return name.startswith(("o1", "o3", "o4")) or name.startswith("gpt-5")
    if provider_key == "claude":
        return ("opus-4" in name) or ("sonnet-4" in name) or ("3-7-sonnet" in name) or ("3.7" in name)
    if provider_key == "gemini":
        # Gemini 2.5 思考模型，OpenAI 兼容接口支持 reasoning_effort。
        return name.startswith("gemini-2.5")
    # Grok、Qwen3、GLM 等也会思考，但其兼容接口的参数并不统一。
    return False


def model_supports_thinking_mode(provider_key: str, model: str | None) -> bool:
    """是否支持显式开启/关闭思考；目前按 DeepSeek 与 Anthropic 官方接口发送。"""
    return provider_key == "deepseek" or (
        provider_key == "claude" and model_supports_reasoning(provider_key, model)
    )


def _reasoning_effort_for_provider(provider_key: str, effort: str) -> str:
    if provider_key == "deepseek":
        return "max" if effort == "max" else "high"
    return "high" if effort == "max" else effort


def _filter_chat_models(provider_key: str, ids: list[str]) -> list[str]:
    """从在线模型列表里筛掉非对话用模型（向量/语音/图像等），保留聊天模型。"""
    cfg = PROVIDERS.get(provider_key) or {}
    cleaned: list[str] = []
    for raw in ids:
        name = str(raw).strip()
        if name.startswith("models/"):   # Gemini OpenAI 兼容接口返回 "models/gemini-…"
            name = name[len("models/"):]
        if name:
            cleaned.append(name)
    unique = list(dict.fromkeys(cleaned))
    if cfg.get("kind") == "anthropic":
        return [m for m in unique if m.startswith("claude")] or unique
    if provider_key == "deepseek":
        return [m for m in unique if m.startswith("deepseek")] or unique
    if provider_key == "gpt":
        bad = ("instruct", "audio", "realtime", "transcribe", "tts", "search",
               "moderation", "embedding", "image", "dall-e", "whisper", "codex")
        keep = [
            m for m in unique
            if m.startswith(("gpt-", "chatgpt", "o1", "o3", "o4"))
            and not any(token in m for token in bad)
        ]
        return keep or unique
    if provider_key == "gemini":
        bad = ("embedding", "aqa", "imagen", "veo", "tts", "image", "learnlm")
        return [m for m in unique if m.startswith("gemini") and not any(b in m for b in bad)] or unique
    if provider_key == "grok":
        return [m for m in unique if m.startswith("grok") and "image" not in m] or unique
    if provider_key == "qwen":
        bad = ("embedding", "ocr", "audio", "tts", "asr", "vl-ocr", "math")
        return [m for m in unique if m.startswith(("qwen", "qwq", "qvq")) and not any(b in m for b in bad)] or unique
    if provider_key == "moonshot":
        return [m for m in unique if m.startswith(("moonshot", "kimi"))] or unique
    if provider_key == "glm":
        bad = ("embedding", "cogview", "cogvideo", "rerank", "voice", "tts", "asr")
        return [m for m in unique if m.startswith("glm") and not any(b in m for b in bad)] or unique
    return unique


def list_provider_models(provider_key: str, api_key: str, timeout: int = 15) -> list[str]:
    """实时拉取某服务商当前 API 下可用的模型列表（需要该服务商的 API Key）。"""
    cfg = PROVIDERS.get(provider_key) or PROVIDERS["deepseek"]
    if not api_key:
        raise RuntimeError(f"未配置 {cfg['name']} 的 API Key（请在 设置 中填写）。")
    endpoint = cfg.get("models_endpoint")
    if not endpoint:
        raise RuntimeError(f"{cfg['name']} 暂不支持在线模型列表。")
    if cfg["kind"] == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        endpoint = endpoint + "?limit=1000"
    else:
        headers = {"Authorization": f"Bearer {api_key}"}
    req = urllib.request.Request(endpoint, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = (body.get("error") or {}).get("message") or str(body)
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
            detail = ""
        raise RuntimeError(f"HTTP {exc.code} {detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络错误：{exc.reason}") from exc
    data = obj.get("data") if isinstance(obj, dict) else None
    raw_ids = [
        item.get("id") for item in (data or [])
        if isinstance(item, dict) and item.get("id")
    ]
    models = _filter_chat_models(provider_key, [str(i) for i in raw_ids])
    if not models:
        raise RuntimeError("接口未返回可用模型。")
    return models


def _trim_llm_message_content(content, max_chars: int) -> str:
    text = str(content or "")
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head - 80
    return (
        text[:head].rstrip()
        + "\n\n……（为节约 tokens，中间历史已省略；如需原文请重新读取相关文件/结果）……\n\n"
        + text[-max(0, tail):].lstrip()
    )


def _limit_llm_history_chars(messages: list[dict],
                             max_chars: int = AI_CONTEXT_TOTAL_MAX_CHARS) -> list[dict]:
    """从最新消息向前装入总预算，确保工具回填和当前问题优先保留。"""
    remaining = max(AI_CONTEXT_MIN_MESSAGE_CHARS, int(max_chars))
    kept_reversed: list[dict] = []
    for message in reversed(messages):
        content = str(message.get("content") or "")
        if not content:
            continue
        allowed = min(len(content), remaining)
        if allowed < AI_CONTEXT_MIN_MESSAGE_CHARS:
            break
        if len(content) > allowed:
            content = _trim_llm_message_content(content, allowed)
            if len(content) > allowed:
                content = content[:allowed].rstrip()
        kept_reversed.append({"role": message["role"], "content": content})
        remaining -= len(content)
        if remaining < AI_CONTEXT_MIN_MESSAGE_CHARS:
            break
    return list(reversed(kept_reversed))


def _prepare_llm_history(history: list[dict], recall_query: str = "") -> list[dict]:
    """只裁剪发往模型的上下文；界面与本地落盘历史不受影响。"""
    clean = [
        {"role": message["role"], "content": str(message["content"])}
        for message in history
        if isinstance(message, dict) and message.get("role") and message.get("content") is not None
    ]
    if len(clean) > AI_CONTEXT_RECENT_MESSAGE_MAX:
        recent_start = len(clean) - AI_CONTEXT_RECENT_MESSAGE_MAX
        older = list(enumerate(clean[:recent_start]))
        recent = clean[recent_start:]
        query = str(recall_query or "")[-AI_CONTEXT_RECALL_QUERY_CHARS:]
        if not _query_mentions_any(query, _HISTORY_RECALL_TERMS):
            clean = recent
        else:
            terms = _extract_query_terms(query)
            scored: list[tuple[int, int, dict]] = []
            for original_index, message in older:
                score = _score_text_for_query(message.get("content", ""), query, terms)
                if score:
                    scored.append((score, original_index, message))
            if scored:
                selected = sorted(
                    sorted(scored, key=lambda item: (-item[0], -item[1]))[:AI_CONTEXT_RELEVANT_OLD_MAX],
                    key=lambda item: item[1],
                )
                clean = [_format_recalled_history([(index, message) for _score, index, message in selected]), *recent]
            else:
                fallback = older[-AI_CONTEXT_RELEVANT_OLD_MAX:]
                clean = [_format_recalled_history(fallback), *recent] if fallback else recent
    if len(clean) > AI_CONTEXT_HISTORY_MAX:
        clean = clean[-AI_CONTEXT_HISTORY_MAX:]
    old_cutoff = max(0, len(clean) - 8)
    trimmed: list[dict] = []
    for index, message in enumerate(clean):
        limit = AI_CONTEXT_OLD_MESSAGE_MAX_CHARS if index < old_cutoff else AI_CONTEXT_MESSAGE_MAX_CHARS
        trimmed.append({
            "role": message["role"],
            "content": _trim_llm_message_content(message["content"], limit),
        })
    return _limit_llm_history_chars(trimmed)


_AI_INTERNAL_MESSAGE_KINDS = frozenset({"operation", "auto_continue"})
_AI_AGENT_OPERATION_DETAIL_MAX_CHARS = 5200
_AI_AGENT_OLD_OPERATION_SUMMARY_MAX_CHARS = 2600
_AI_AGENT_PROGRESS_MAX_CHARS = 2200
_AI_AGENT_KEEP_DETAILED_OPERATIONS = 2
_AI_AGENT_KEEP_PROGRESS_MESSAGES = 3


def _latest_user_task_index(history: list[dict]) -> int:
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        kind = str(message.get("kind") or "").strip().casefold()
        if kind not in _AI_INTERNAL_MESSAGE_KINDS:
            return index
    return -1


def _compact_agent_chain_history(history: list[dict]) -> tuple[list[dict], bool]:
    """Locally compact repeated agent-loop messages without another model call.

    The current task, the newest operation results and recent assistant decisions
    stay detailed. Older operation rounds become one bounded summary and automatic
    "continue" nudges are removed because the system prompt already carries them.
    """
    messages = [dict(message) for message in history if isinstance(message, dict)]
    task_index = _latest_user_task_index(messages)
    if task_index < 0:
        return messages, False
    active_chain = any(
        str(message.get("kind") or "").strip().casefold() in _AI_INTERNAL_MESSAGE_KINDS
        for message in messages[task_index + 1:]
    )
    if not active_chain:
        return messages, False

    operation_indexes = [
        index for index in range(task_index + 1, len(messages))
        if str(messages[index].get("kind") or "").strip().casefold() == "operation"
    ]
    detailed_operations = set(operation_indexes[-_AI_AGENT_KEEP_DETAILED_OPERATIONS:])
    old_operations = operation_indexes[:-_AI_AGENT_KEEP_DETAILED_OPERATIONS]
    assistant_indexes = [
        index for index in range(task_index + 1, len(messages))
        if messages[index].get("role") == "assistant"
    ]
    kept_assistants = set(assistant_indexes[-_AI_AGENT_KEEP_PROGRESS_MESSAGES:])

    summary_message = None
    if old_operations:
        # Preserve both task setup and the most recent older progress when a chain is long.
        selected = list(dict.fromkeys([*old_operations[:2], *old_operations[-6:]]))
        per_result = max(240, _AI_AGENT_OLD_OPERATION_SUMMARY_MAX_CHARS // max(1, len(selected)))
        lines: list[str] = []
        omitted = max(0, len(old_operations) - len(selected))
        if omitted:
            lines.append(f"- 另有 {omitted} 轮较早操作已省略，必要时可重新读取目标核验。")
        for number, index in enumerate(selected, 1):
            content = re.sub(r"\s+", " ", str(messages[index].get("content") or "")).strip()
            if content:
                lines.append(f"- 早期结果 {number}：{_trim_llm_message_content(content, per_result)}")
        if lines:
            summary_message = {
                "role": "user",
                "kind": "operation_summary",
                "content": "Passer 较早本地操作摘要（最新两轮仍保留完整结果）：\n" + "\n".join(lines),
            }

    compacted: list[dict] = []
    summary_inserted = False
    for index, message in enumerate(messages):
        kind = str(message.get("kind") or "").strip().casefold()
        if index > task_index and kind == "auto_continue":
            continue
        if index in old_operations:
            if summary_message is not None and not summary_inserted:
                compacted.append(summary_message)
                summary_inserted = True
            continue
        if index > task_index and message.get("role") == "assistant" and index not in kept_assistants:
            continue
        copied = dict(message)
        if index in detailed_operations:
            copied["content"] = _trim_llm_message_content(
                copied.get("content", ""), _AI_AGENT_OPERATION_DETAIL_MAX_CHARS)
        elif index > task_index and message.get("role") == "assistant":
            copied["content"] = _trim_llm_message_content(
                copied.get("content", ""), _AI_AGENT_PROGRESS_MAX_CHARS)
        compacted.append(copied)
    return compacted, True


def _latest_user_query(history: list[dict]) -> str:
    index = _latest_user_task_index(history)
    if index >= 0:
        return str(history[index].get("content") or "")[-AI_CONTEXT_RECALL_QUERY_CHARS:]
    return ""


def call_llm(provider_key: str, api_key: str, history: list[dict],
             memory_notes: list[str] | None = None, timeout: int = 60,
             model: str | None = None, reasoning: str | None = None,
             thinking_mode: str | None = None,
             operations: list[str] | None = None,
             persona: str | None = None, on_delta=None,
             permission: str | None = None, prompt_cache: bool = True) -> tuple[str, dict]:
    """调用对应服务商的聊天补全接口，返回（纯文本回复, token 用量明细）。

    传入 ``on_delta(chunk)`` 时改用流式（SSE）：每收到一段正文就回调一次，便于上层把
    回复一点点打到气泡里；未传时为一次性阻塞返回。
    """
    cfg = PROVIDERS.get(provider_key) or PROVIDERS["deepseek"]
    if not api_key:
        raise RuntimeError(f"未配置 {cfg['name']} 的 API Key（请在 设置 中填写）。")
    # 消息可能携带气泡用的附加字段（time/tokens/text/attachments），发往接口前只保留
    # role/content，避免严格校验的服务商因未知字段报 400。
    # 在丢弃气泡附加字段前提取真正的用户请求，确保整个自动任务链目标稳定。
    skill_query = _latest_user_query(history)
    history, active_agent_chain = _compact_agent_chain_history(history)
    raw_history = [
        {"role": message["role"], "content": str(message["content"])}
        for message in history
        if isinstance(message, dict) and message.get("role") and message.get("content") is not None
    ]
    # 用“最后一条用户消息”作为检索/技能/历史操作召回查询。
    # 之前使用最近多条用户消息会把旧主题（如 Simulink/打包）混入当前短问题，
    # 导致模型把新问题误判成旧任务延续。
    history = _prepare_llm_history(raw_history, skill_query)
    # 模型可能来自在线模型列表（不在静态清单内），这里信任调用方传入值，只在空时回退默认。
    model_id = str(model).strip() if model and str(model).strip() else cfg["model"]
    effort = normalize_reasoning(reasoning)
    mode = normalize_thinking_mode(thinking_mode)
    provider_id = provider_key if provider_key in PROVIDERS else "deepseek"
    supports_effort = model_supports_reasoning(provider_id, model_id)
    supports_mode = model_supports_thinking_mode(provider_id, model_id)
    explicit_effort = effort != "auto" and supports_effort
    explicit_mode = mode != "auto" and supports_mode
    default_deepseek_thinking = provider_id == "deepseek" and mode != "disabled"
    if explicit_effort or explicit_mode or default_deepseek_thinking:
        # 思考会显著拉长响应时间，放宽超时，避免高档位被 60s 截断。
        timeout = max(timeout, 240)

    system_instructions = build_system_instructions(
        memory_notes or [], skill_query, ([] if active_agent_chain else (operations or [])), persona=persona,
        permission=permission)
    stream = on_delta is not None
    if cfg["kind"] == "anthropic":
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model_id,
            "max_tokens": AI_MAX_OUTPUT_TOKENS,
            "system": (
                [{"type": "text", "text": system_instructions, "cache_control": {"type": "ephemeral"}}]
                if prompt_cache else system_instructions
            ),
            "messages": history,
            "stream": stream,
        }
        anthropic_thinking = supports_mode and mode != "disabled" and (
            mode == "enabled" or explicit_effort
        )
        if anthropic_thinking:
            # 开启扩展思考：budget 必须小于 max_tokens，故同步抬高 max_tokens。
            budget = _THINKING_BUDGET.get(effort, 8192)
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            payload["max_tokens"] = budget + AI_MAX_OUTPUT_TOKENS
    else:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model_id,
            "messages": [{"role": "system", "content": system_instructions}, *history],
            "max_tokens": AI_MAX_OUTPUT_TOKENS,
            "stream": stream,
        }
        if stream:
            # 让最后一个数据块带上 usage，便于统计本轮 tokens。
            payload["stream_options"] = {"include_usage": True}
        if prompt_cache and provider_key == "gpt":
            payload["prompt_cache_key"] = _prompt_cache_key(provider_key, model_id, persona, permission)
        if provider_id == "deepseek":
            if explicit_mode:
                payload["thinking"] = {"type": mode}
            if mode != "disabled" and explicit_effort:
                payload["reasoning_effort"] = _reasoning_effort_for_provider(provider_id, effort)
        elif explicit_effort:
            payload["reasoning_effort"] = _reasoning_effort_for_provider(provider_id, effort)

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(cfg["endpoint"], data=data, headers=headers, method="POST")
    if stream:
        text, usage = _stream_llm_response(req, timeout, cfg["kind"], on_delta)
        record_token_usage(provider_key, model_id, usage)
        return text, usage
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = (body.get("error") or {}).get("message") or str(body)
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
            detail = ""
        raise RuntimeError(f"HTTP {exc.code} {detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络错误：{exc.reason}") from exc

    usage = _extract_llm_usage(obj.get("usage") if isinstance(obj, dict) else None, cfg["kind"])
    if cfg["kind"] == "anthropic":
        parts = obj.get("content") or []
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    else:
        choices = obj.get("choices") or [{}]
        text = (choices[0].get("message") or {}).get("content") or ""
    text = (text or "").strip()
    record_token_usage(provider_key, model_id, usage)
    return (text or "(空回复)"), usage


def call_deepseek_max_context(
        api_key: str, prompt: str, *, timeout: int = 600,
        model: str | None = None, reasoning: str | None = None,
        thinking_mode: str | None = None,
        max_output_tokens: int | None = None) -> tuple[str, dict]:
    """通过 DeepSeek 最大上下文通道原样提交一条长文本题目。

    该程序化入口不经过 Aira 的消息数、单消息字符数和历史总字符数裁剪，
    也不注入记忆、技能、操作协议或 persona；请求正文中只有用户传入的
    ``prompt``。实际 token 上限由所选 DeepSeek 模型与服务端执行，超限时
    显式返回服务端错误，绝不静默截短或降级重试。
    """
    cfg = PROVIDERS["deepseek"]
    if not api_key:
        raise RuntimeError(f"未配置 {cfg['name']} 的 API Key（请在 设置 中填写）。")
    if not isinstance(prompt, str):
        raise TypeError("prompt 必须是字符串。")
    if not prompt.strip():
        raise ValueError("prompt 不能为空。")

    model_id = str(model).strip() if model and str(model).strip() else cfg["model"]
    output_limit = AI_MAX_OUTPUT_TOKENS if max_output_tokens is None else int(max_output_tokens)
    if output_limit <= 0:
        raise ValueError("max_output_tokens 必须为正整数。")

    effort = normalize_reasoning(reasoning)
    mode = normalize_thinking_mode(thinking_mode)
    supports_effort = model_supports_reasoning("deepseek", model_id)
    supports_mode = model_supports_thinking_mode("deepseek", model_id)
    explicit_effort = effort != "auto" and supports_effort
    explicit_mode = mode != "auto" and supports_mode
    if explicit_effort or explicit_mode or mode != "disabled":
        timeout = max(timeout, 240)

    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": output_limit,
        "stream": False,
    }
    if explicit_mode:
        payload["thinking"] = {"type": mode}
    if mode != "disabled" and explicit_effort:
        payload["reasoning_effort"] = _reasoning_effort_for_provider("deepseek", effort)

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg["endpoint"],
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = (body.get("error") or {}).get("message") or str(body)
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
            detail = ""
        raise RuntimeError(f"HTTP {exc.code} {detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络错误：{exc.reason}") from exc

    usage = _extract_llm_usage(obj.get("usage") if isinstance(obj, dict) else None, cfg["kind"])
    choices = obj.get("choices") or [{}]
    text = (choices[0].get("message") or {}).get("content") or ""
    text = (text or "").strip()
    record_token_usage("deepseek", model_id, usage)
    return (text or "(空回复)"), usage


def _stream_llm_response(req, timeout: int, kind: str, on_delta) -> tuple[str, dict]:
    """逐行读取 SSE 流：累计正文并对每段调用 on_delta，结束后返回（全文, token 用量）。"""
    parts: list[str] = []
    usage_acc = normalize_token_usage(0)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                try:
                    line = raw.decode("utf-8").strip()
                except UnicodeError:
                    continue
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if kind == "anthropic":
                    etype = obj.get("type")
                    if etype == "content_block_delta":
                        delta = obj.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            chunk = delta.get("text") or ""
                            if chunk:
                                parts.append(chunk)
                                on_delta(chunk)
                    elif etype == "message_start":
                        usage_acc = _merge_token_usage(usage_acc, _extract_llm_usage(
                            (obj.get("message") or {}).get("usage") or {}, kind))
                    elif etype == "message_delta":
                        usage_acc = _merge_token_usage(usage_acc, _extract_llm_usage(obj.get("usage") or {}, kind))
                else:
                    choices = obj.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        chunk = delta.get("content") or ""
                        if chunk:
                            parts.append(chunk)
                            on_delta(chunk)
                    usage = obj.get("usage")
                    if isinstance(usage, dict):
                        usage_acc = _extract_llm_usage(usage, kind)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = (body.get("error") or {}).get("message") or str(body)
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
            detail = ""
        raise RuntimeError(f"HTTP {exc.code} {detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络错误：{exc.reason}") from exc
    text = "".join(parts).strip()
    return text, usage_acc
