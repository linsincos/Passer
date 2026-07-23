from __future__ import annotations

# Loaded into the parent module's global namespace by the parent file.
# Keep this file focused on the extracted feature area.

def build_system_instructions(notes: list[str], skill_query: str = "",
                              operations: list[str] | None = None,
                              persona: str | None = None,
                              permission: str | None = None) -> str:
    ensure_ai_context_files()
    instructions = COMPACT_RUNTIME_INSTRUCTIONS.strip()
    skills = load_installed_skills(skill_query)
    if skills:
        instructions = f"{instructions}\n\n# 相关 Skills（精简注入）\n\n{skills}"
    blocks = [instructions]
    latest_query = str(skill_query or "").strip()
    if latest_query:
        blocks.append(
            "# 当前请求优先级\n\n"
            f"用户最新请求是：{latest_query[:600]}\n\n"
            "必须优先回答这条最新请求。除非最新请求明确使用“继续、刚才、上次、之前、那个、上述、接着”等指代，"
            "否则不要把较早对话、长期记忆或历史操作记录当成当前任务目标；这些旧内容只能作为弱参考。"
            "如果最新请求很短或含糊，先围绕最新请求澄清，不要擅自继承旧项目。"
        )
        mail_terms = (
            "邮件", "邮箱", "收件箱", "发件箱", "写信", "发信", "收信",
            "mail", "email", "imap", "smtp", "inbox",
        )
        if any(term in latest_query.casefold() for term in mail_terms):
            blocks.append(PASSER_MAIL_INSTRUCTIONS.strip())
        device_control_terms = (
            "手机投屏", "投屏", "无线调试", "无线adb", "无线 adb", "scrcpy",
            "文件共享", "局域网共享", "传输码", "接收文件", "共享文件",
            "phone mirror", "screen mirror", "file share", "lan share",
        )
        if any(term in latest_query.casefold() for term in device_control_terms):
            blocks.append(PASSER_DEVICE_CONTROL_INSTRUCTIONS.strip())
        aira_tool_terms = (
            "aira", "微信总结", "微信消息总结", "微信监听", "总结微信", "新微信消息",
        )
        if any(term in latest_query.casefold() for term in aira_tool_terms):
            blocks.append(PASSER_AIRA_TOOL_INSTRUCTIONS.strip())
    # 操作权限：始终注入，保证即便用户本地的 AI_INSTRUCTIONS.md 未更新也能生效。
    blocks.append(
        "# 操作权限\n\n"
        + permission_prompt(permission)
        + "\n\n你可以通过以下读取类动作请求读取本机内容；在“请求批准”档位，Passer 会先向用户弹出单次授权：\n"
        "- `read_file`：读取本机文本/代码/Office 文件内容，参数 `target` 为绝对路径或 Passer 项目名，"
        "可选 `max_chars` 限制返回长度。\n"
        "- `read_dir` / `list_dir`：列出某个文件夹下的文件与子目录，参数 `target` 为文件夹绝对路径或 Passer 项目名。\n"
        "需要文件真实内容时必须调用上述动作并等待“Passer 本地操作结果”回填，不得凭空臆测文件内容。"
    )
    style = persona_prompt(persona)
    if style:
        blocks.append(
            "# 回应风格\n\n"
            "以下风格只影响你的语气与措辞，绝不改变事实正确性、动作协议或回答的完整性：\n\n"
            f"{style}"
        )
    relevant_operations = _select_relevant_operations(operations or [], skill_query)
    if relevant_operations:
        recent = "\n".join(f"- {op}" for op in relevant_operations)
        blocks.append(
            "# 最近操作记录\n\n"
            "以下是你此前通过 Passer 动作协议执行过的本地操作（最近在前），可据此保持上下文一致、"
            "避免重复或冲突的动作；这些只是历史摘要，不要据此虚构未真实发生的操作。\n\n"
            f"{recent}"
        )
    relevant_notes = _select_relevant_memory_notes(notes, skill_query)
    if relevant_notes:
        memory = "\n".join(f"- {note}" for note in relevant_notes)
        blocks.append(f"# 本机长期记忆\n\n{memory}")
    try:
        extensions = installed_extensions_summary(skill_query)
    except Exception:  # noqa: BLE001 - 扩展清单不可用时不影响主流程
        extensions = ""
    if extensions:
        blocks.append(
            "# 你已安装的扩展\n\n"
            "以下是你此前为 Passer 自建的技能与自写的插件，可继续复用或迭代；"
            "需要插件结果时用 `run_plugin` 调用并等待回填。\n\n"
            f"{extensions}"
        )
    return "\n\n".join(blocks)


_SKILL_ROUTE_RE = re.compile(r"<!--\s*ROUTE:\s*(.*?)\s*-->", re.IGNORECASE)


def _parse_skill_route_terms(text: str) -> tuple[str, ...]:
    """从 SKILL.md 正文里解析自带的路由关键词指令 `<!-- ROUTE: a, b -->`。"""
    terms: list[str] = []
    for match in _SKILL_ROUTE_RE.finditer(text):
        for term in re.split(r"[,，、\s]+", match.group(1)):
            term = term.strip().casefold()
            if term:
                terms.append(term)
    return tuple(dict.fromkeys(terms))


_SKILL_TOKEN_RE = re.compile(r"[A-Za-z0-9_+.#/-]{2,}|[\u4e00-\u9fff]{2,}")
_SKILL_ACTION_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{1,40})`")
_SKILL_STOP_TERMS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "when", "then",
    "user", "passer", "action", "params", "query", "target", "content",
    "用途", "规则", "参数", "示例", "动作", "用户", "使用", "需要", "可以", "如果",
    "结果", "文件路径", "本地操作结果", "passer_action", "passer",
}


def _clean_skill_term(value) -> str:
    term = re.sub(r"\s+", " ", str(value or "").strip().casefold())
    term = term.strip("`'\"“”‘’.,，、;；:：()（）[]【】{}<>《》!?！？#*-_")
    if len(term) < 2 or term in _SKILL_STOP_TERMS:
        return ""
    return term[:60]


def _skill_title(text: str) -> str:
    for line in str(text or "").splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _skill_summary(text: str) -> str:
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("<!--") or line.startswith("[["):
            continue
        if line.startswith("用途"):
            return line[:220]
    for line in str(text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("-") and not line.startswith("```"):
            return line[:220]
    return _skill_title(text)


def _extract_query_terms(text: str, *, limit: int = 180) -> tuple[str, ...]:
    terms: list[str] = []
    for raw in _SKILL_TOKEN_RE.findall(str(text or "").casefold()):
        term = _clean_skill_term(raw)
        if not term:
            continue
        terms.append(term)
        if re.fullmatch(r"[\u4e00-\u9fff]{3,16}", term):
            for size in (2, 3, 4):
                for index in range(0, len(term) - size + 1):
                    piece = _clean_skill_term(term[index:index + size])
                    if piece:
                        terms.append(piece)
                        if len(terms) >= limit:
                            return tuple(dict.fromkeys(terms))
        if len(terms) >= limit:
            break
    return tuple(dict.fromkeys(terms))


_MEMORY_RECALL_TERMS = (
    "记忆", "记得", "偏好", "习惯", "之前", "上次", "我常用", "长期",
    "memory", "remember", "preference",
)
_HISTORY_RECALL_TERMS = (
    "之前", "上次", "刚才", "前面", "历史", "旧消息", "对话", "上下文", "继续",
    "接着", "那个", "上述", "以上", "前文", "history", "context", "previous",
)
_EXTENSION_RECALL_TERMS = (
    "技能", "skill", "插件", "plugin", "mod", "mods", "模组", "扩展", "能力库", "自建", "自写",
    "find_skills", "read_skill", "list_plugins", "run_plugin", "create_plugin", "list_mods", "create_mod",
)


def _query_mentions_any(query: str, terms: tuple[str, ...]) -> bool:
    query_cf = str(query or "").casefold()
    return any(term.casefold() in query_cf for term in terms)


def _score_text_for_query(text: str, query: str,
                          query_terms: tuple[str, ...] | None = None) -> int:
    query_cf = str(query or "").casefold()
    if not query_cf.strip():
        return 0
    terms = query_terms if query_terms is not None else _extract_query_terms(query_cf)
    haystack = str(text or "").casefold()
    score = 0
    for term in terms:
        if len(term) < 2 or term in _SKILL_STOP_TERMS:
            continue
        if term in haystack:
            score += 5 if len(term) >= 4 else 2
    return score


def _select_relevant_memory_notes(notes: list[str], query: str) -> list[str]:
    clean = [str(note).strip() for note in notes if str(note or "").strip()]
    if not clean:
        return []
    query = str(query or "")[-AI_CONTEXT_RECALL_QUERY_CHARS:]
    terms = _extract_query_terms(query)
    scored: list[tuple[int, int, str]] = []
    for index, note in enumerate(clean):
        score = _score_text_for_query(note, query, terms)
        if score:
            scored.append((score, index, note))
    if scored:
        selected = sorted(
            sorted(scored, key=lambda item: (-item[0], -item[1]))[:AI_MEMORY_CONTEXT_MAX],
            key=lambda item: item[1],
        )
        notes_out = [note for _score, _index, note in selected]
    elif _query_mentions_any(query, _MEMORY_RECALL_TERMS):
        notes_out = clean[-AI_MEMORY_CONTEXT_MAX:]
    else:
        return []

    kept: list[str] = []
    total = 0
    for note in notes_out:
        remaining = AI_MEMORY_CONTEXT_MAX_CHARS - total
        if remaining <= 0:
            break
        value = note[:remaining].rstrip()
        if value:
            kept.append(value)
            total += len(value) + 1
    return kept


def _select_relevant_operations(operations: list[str], query: str) -> list[str]:
    """按当前问题召回历史操作，避免每轮固定重发最近操作。"""
    clean = [str(item).strip() for item in operations if str(item).strip()]
    if not clean:
        return []
    query = str(query or "")[-AI_CONTEXT_RECALL_QUERY_CHARS:]
    if not query.strip():
        return []
    # 跨对话操作记录很容易把短问题带偏；只有用户明确指代旧上下文时才注入。
    if not _query_mentions_any(query, _HISTORY_RECALL_TERMS):
        return []
    terms = _extract_query_terms(query)
    scored: list[tuple[int, int, str]] = []
    for index, operation in enumerate(clean):
        score = _score_text_for_query(operation, query, terms)
        if score >= OPERATION_CONTEXT_MIN_SCORE:
            scored.append((score, index, operation))
    if scored:
        selected = sorted(
            sorted(scored, key=lambda item: (-item[0], -item[1]))[:OPERATION_CONTEXT_MAX],
            key=lambda item: item[1],
        )
        return [operation for _score, _index, operation in selected]
    if _query_mentions_any(query, _HISTORY_RECALL_TERMS):
        return clean[-OPERATION_CONTEXT_MAX:]
    return []


def _format_recalled_history(recalled: list[tuple[int, dict]]) -> dict:
    labels = {"user": "用户", "assistant": "AI"}
    lines = ["以下是按当前提及召回的较早对话摘录；未列出的旧消息未发送。"]
    for index, message in recalled:
        role = labels.get(str(message.get("role") or ""), str(message.get("role") or "消息"))
        content = _trim_llm_message_content(
            message.get("content", ""), AI_CONTEXT_RECALLED_MESSAGE_MAX_CHARS)
        content = re.sub(r"\n{3,}", "\n\n", content).strip()
        if content:
            lines.append(f"[{index + 1}] {role}: {content}")
    return {"role": "user", "content": "\n\n".join(lines)}


def _auto_skill_route_terms(name: str, text: str) -> tuple[str, ...]:
    """从技能正文自动提取路由词，避免未写 ROUTE 的随包技能无法被中文问题召回。"""
    seed_lines: list[str] = [str(name or ""), str(name or "").replace("-", " "), _skill_title(text), _skill_summary(text)]
    for line in str(text or "").splitlines()[:80]:
        clean = line.strip()
        if not clean or clean.startswith("[[") or clean.startswith("```"):
            continue
        if clean.startswith("#") or clean.startswith("-") or clean.startswith("用途") or clean.startswith("规则"):
            seed_lines.append(clean)
    seed = "\n".join(seed_lines)
    terms: list[str] = []
    for action_name in _SKILL_ACTION_RE.findall(seed):
        term = _clean_skill_term(action_name)
        if term:
            terms.append(term)
    for ext in re.findall(r"\.[A-Za-z0-9]{1,8}", seed):
        term = _clean_skill_term(ext)
        if term:
            terms.append(term)
    terms.extend(_extract_query_terms(seed, limit=120))
    return tuple(dict.fromkeys(terms[:120]))


def _term_matches_query(term: str, query_cf: str, query_terms: tuple[str, ...]) -> bool:
    term_cf = _clean_skill_term(term)
    if not term_cf:
        return False
    if term_cf in query_cf:
        return True
    compact_term = term_cf.replace(" ", "").replace("-", "_")
    compact_query = query_cf.replace(" ", "").replace("-", "_")
    if compact_term and compact_term in compact_query:
        return True
    for q in query_terms:
        if len(q) < 2:
            continue
        if q == term_cf:
            return True
        if len(q) >= 3 and (q in term_cf or term_cf in q):
            return True
        if re.search(r"[\u4e00-\u9fff]", q + term_cf) and len(q) >= 2 and (q in term_cf or term_cf in q):
            return True
    return False


def _installed_skill_catalog() -> list[tuple[str, str, tuple[str, ...]]]:
    global _SKILL_CACHE_SIGNATURE, _SKILL_CACHE
    ensure_ai_context_files()
    try:
        skill_files = sorted(AI_SKILLS_DIR.glob("*/SKILL.md"))
        signature = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in skill_files)
    except OSError as exc:
        LOGGER.warning("Unable to scan installed skills: %s", exc)
        return []
    if signature == _SKILL_CACHE_SIGNATURE:
        return _SKILL_CACHE

    catalog: list[tuple[str, str, tuple[str, ...]]] = []
    for path in skill_files:
        try:
            if path.stat().st_size > AI_SKILL_MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            LOGGER.warning("Unable to read skill %s: %s", path, exc)
            continue
        if not text:
            continue
        terms = tuple(dict.fromkeys((
            *_parse_skill_route_terms(text),
            *_auto_skill_route_terms(path.parent.name, text),
        )))
        catalog.append((path.parent.name.casefold(), text, terms))
    _SKILL_CACHE_SIGNATURE = signature
    _SKILL_CACHE = catalog
    return catalog


def _rank_installed_skills(query: str = "") -> list[tuple[int, str, str, tuple[str, ...]]]:
    query_cf = str(query or "").casefold()
    if not query_cf.strip():
        return []
    query_terms = _extract_query_terms(query_cf)
    ranked: list[tuple[int, str, str, tuple[str, ...]]] = []
    for name, text, own_terms in _installed_skill_catalog():
        score = 18 if name.replace("-", " ") in query_cf or name in query_cf else 0
        fixed_terms = SKILL_ROUTE_TERMS.get(name, ())
        score += sum(6 for term in fixed_terms if _term_matches_query(term, query_cf, query_terms))
        # 自建技能自带的 ROUTE 关键词、以及从正文自动提取的关键词同样参与检索。
        score += sum(4 for term in own_terms if _term_matches_query(term, query_cf, query_terms))
        haystack = f"{name} {' '.join(fixed_terms)} {' '.join(own_terms)} {_skill_title(text)} {_skill_summary(text)} {text[:5000]}".casefold()
        token_hits = 0
        for term in query_terms:
            if term in _SKILL_STOP_TERMS:
                continue
            if term in haystack:
                score += 2 if len(term) >= 4 else 1
                token_hits += 1
                if token_hits >= 14:
                    break
        if score:
            ranked.append((score, name, text, own_terms))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked


def _select_ranked_skills(query: str = "") -> list[tuple[int, str, str, tuple[str, ...]]]:
    """只注入强相关技能；弱命中会重复基础提示并徒增输入 tokens。"""
    ranked = _rank_installed_skills(query)
    if not ranked or ranked[0][0] < AI_SKILL_MIN_SCORE:
        return []
    selected = [ranked[0]]
    secondary_floor = max(
        AI_SKILL_MIN_SCORE,
        int(ranked[0][0] * AI_SKILL_SECONDARY_SCORE_RATIO),
    )
    for item in ranked[1:]:
        if len(selected) >= AI_SKILL_MAX_SELECTED or item[0] < secondary_floor:
            break
        selected.append(item)
    return selected


def load_installed_skills(query: str = "") -> str:
    ranked = _select_ranked_skills(query)
    if not ranked:
        return ""

    blocks: list[str] = []
    total = 0
    for _score, name, text, _terms in ranked[:AI_SKILL_MAX_SELECTED]:
        block = f"## {name}\n\n{_compact_skill_for_prompt(text, AI_SKILL_INJECT_MAX_CHARS)}"
        if total + len(block) > AI_SKILL_MAX_TOTAL_CHARS:
            break
        blocks.append(block)
        total += len(block)
    return "\n\n---\n\n".join(blocks)


def _compact_skill_for_prompt(text: str, max_chars: int = AI_SKILL_INJECT_MAX_CHARS) -> str:
    """把 Skill.md 压缩成运行时摘要；全文仍可用 read_skill 按需读取。"""
    raw = str(text or "").strip()
    if len(raw) <= max_chars:
        return raw
    keep: list[str] = []
    in_fence = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("[[PASSER_ACTION"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped:
            if keep and keep[-1]:
                keep.append("")
            continue
        if (
            stripped.startswith("#")
            or stripped.startswith("用途")
            or stripped.startswith("规则")
            or stripped.startswith("动作")
            or stripped.startswith("工作流")
            or stripped.startswith("-")
            or stripped.startswith("<!-- ROUTE:")
        ):
            keep.append(line[:260])
        if sum(len(item) + 1 for item in keep) >= max_chars:
            break
    compact = "\n".join(keep).strip()
    if not compact:
        compact = raw[:max_chars].strip()
    if len(compact) > max_chars:
        compact = compact[:max_chars].rstrip()
    return compact + "\n\n（已精简注入；需要完整流程时调用 read_skill。）"


def find_skills(query: str = "", limit: int = AI_SKILL_MAX_SELECTED,
                include_content: bool = False) -> list[dict]:
    cap = max(1, min(int(limit or AI_SKILL_MAX_SELECTED), 12))
    matches: list[dict] = []
    for score, name, text, terms in _rank_installed_skills(query)[:cap]:
        item = {
            "name": name,
            "score": score,
            "title": _skill_title(text),
            "summary": _skill_summary(text),
            "keywords": list(terms[:18]),
        }
        if include_content:
            item["content"] = text[:AI_SKILL_MAX_BYTES]
        matches.append(item)
    return matches


def read_skill(name: str) -> str:
    ensure_ai_context_files()
    raw = str(name or "").strip()
    if not raw:
        raise ValueError("技能名不能为空")
    wanted = {raw.casefold(), _slugify(raw, "skill").casefold()}
    for path in sorted(AI_SKILLS_DIR.glob("*/SKILL.md")):
        if path.parent.name.casefold() in wanted:
            return path.read_text(encoding="utf-8")
    for skill_name, text, _terms in _installed_skill_catalog():
        title = _skill_title(text).casefold()
        if raw.casefold() == title or raw.casefold() in {skill_name, skill_name.replace("-", " ")}:
            return text
    raise ValueError(f"技能不存在：{name}")


# ---------------------------------------------------------------------------
# Aira 自我扩展：自建技能（SKILL.md）+ 自写插件（AITools/*.py，动态导入运行）
# ---------------------------------------------------------------------------
def _slugify(name: str, fallback: str = "item") -> str:
    """把技能/插件名规范成安全的文件/目录名，保留中文与基本字符。"""
    slug = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", str(name or "")).strip().strip("._ ")
    slug = re.sub(r"\s+", "-", slug)
    return slug[:80] or fallback


def list_skill_names() -> list[str]:
    return [name for name, _text, _terms in _installed_skill_catalog()]


def list_skill_catalog() -> list[dict]:
    items: list[dict] = []
    for name, text, terms in _installed_skill_catalog():
        items.append({
            "name": name,
            "title": _skill_title(text),
            "summary": _skill_summary(text),
            "keywords": list(terms[:16]),
        })
    return items


def write_skill(name: str, content: str, keywords=None) -> Path:
    """新建/覆盖一个技能（AISkills/<slug>/SKILL.md）。keywords 写入 ROUTE 指令以便检索。"""
    ensure_ai_context_files()
    slug = _slugify(name, "skill")
    body = str(content or "").strip()
    if not body:
        raise ValueError("技能内容（content）不能为空")
    if len(body.encode("utf-8")) > AI_SKILL_MAX_BYTES:
        raise ValueError(f"技能内容过大（上限 {AI_SKILL_MAX_BYTES} 字节）")
    terms: list[str] = []
    if isinstance(keywords, str):
        terms = [t.strip() for t in re.split(r"[,，、\s]+", keywords) if t.strip()]
    elif isinstance(keywords, (list, tuple)):
        terms = [str(t).strip() for t in keywords if str(t).strip()]
    header = ""
    if terms and "ROUTE:" not in body.upper():
        header = f"<!-- ROUTE: {', '.join(terms)} -->\n\n"
    skill_dir = AI_SKILLS_DIR / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(header + body + "\n", encoding="utf-8")
    return path


def delete_skill(name: str) -> str:
    slug = _slugify(name, "skill")
    skill_dir = AI_SKILLS_DIR / slug
    if not skill_dir.is_dir():
        raise ValueError(f"技能不存在：{name}")
    shutil.rmtree(skill_dir)
    return f"已删除技能：{slug}"


def _plugin_path(name: str) -> Path:
    stem = _slugify(name, "plugin")
    if stem.lower().endswith(".py"):
        stem = stem[:-3]
    return AI_TOOLS_DIR / f"{stem}.py"


def list_plugins() -> list[dict]:
    """列出已安装插件及其 DESCRIPTION（只读，不执行模块顶层代码——用正则提取说明）。"""
    ensure_ai_context_files()
    plugins: list[dict] = []
    for path in sorted(AI_TOOLS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        description = ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"""DESCRIPTION\s*=\s*['"](.+?)['"]""", text, re.S)
            if match:
                description = match.group(1).strip()[:200]
        except OSError:
            pass
        plugins.append({"name": path.stem, "description": description, "path": str(path)})
    return plugins


def _rank_plugins(query: str = "") -> list[tuple[int, dict]]:
    plugins = list_plugins()
    query = str(query or "")[-AI_CONTEXT_RECALL_QUERY_CHARS:]
    if not query.strip():
        return []
    terms = _extract_query_terms(query)
    ranked: list[tuple[int, dict]] = []
    for plugin in plugins:
        haystack = f"{plugin.get('name', '')} {plugin.get('description', '')}".casefold()
        score = _score_text_for_query(haystack, query, terms)
        name = str(plugin.get("name") or "").casefold()
        if name and (name in query.casefold() or name.replace("-", " ") in query.casefold()):
            score += 18
        if score:
            ranked.append((score, plugin))
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("name") or "")))
    return ranked


def list_mod_manifests() -> list[dict]:
    """List external MOD metadata without importing or executing MOD code."""
    mods_dir = AI_DATA_DIR / "Mods"
    records: list[dict] = []
    if not mods_dir.is_dir():
        return records
    for manifest_path in sorted(mods_dir.glob("*/manifest.json")):
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                continue
            records.append({
                "id": str(raw.get("id") or manifest_path.parent.name),
                "title": str(raw.get("title") or raw.get("name") or manifest_path.parent.name),
                "description": str(raw.get("description") or "")[:200],
                "enabled": bool(raw.get("enabled", True)),
                "startup": bool(raw.get("startup", True)),
                "expose_tool": bool(raw.get("expose_tool", True)),
            })
        except (OSError, ValueError, TypeError):
            continue
    return records


def installed_extensions_summary(query: str = "") -> str:
    """供系统提示词使用：按当前提及列出相关技能、插件与 MOD。"""
    lines: list[str] = []
    query = str(query or "")[-AI_CONTEXT_RECALL_QUERY_CHARS:]
    extension_requested = _query_mentions_any(query, _EXTENSION_RECALL_TERMS)

    ranked_skills = _select_ranked_skills(query)
    if ranked_skills:
        names = "、".join(name for _score, name, _text, _terms in ranked_skills[:AI_EXTENSION_SKILL_SUMMARY_MAX])
        lines.append(
            "相关技能：" + names
            + "。系统已按当前任务注入少量摘要；需要完整流程时用 find_skills/read_skill。"
        )

    plugins = [plugin for _score, plugin in _rank_plugins(query)]
    if not plugins and extension_requested:
        plugins = list_plugins()
    if plugins:
        lines.append("插件（run_plugin 可调用）：")
        for plugin in plugins[:AI_EXTENSION_PLUGIN_SUMMARY_MAX]:
            desc = f" — {plugin['description']}" if plugin["description"] else ""
            lines.append(f"- {plugin['name']}{desc}")
        if len(plugins) > AI_EXTENSION_PLUGIN_SUMMARY_MAX:
            lines.append(f"- …另有 {len(plugins) - AI_EXTENSION_PLUGIN_SUMMARY_MAX} 个插件，可用 list_plugins 查看")
    if extension_requested:
        mods = list_mod_manifests()
        if mods:
            lines.append("运行时 MOD（可在 EXE 发布后注册宿主工具、按钮、事件与 Aira 动作）：")
            for mod in mods[:AI_EXTENSION_PLUGIN_SUMMARY_MAX]:
                state = "启用" if mod["enabled"] else "禁用"
                desc = f" — {mod['description']}" if mod["description"] else ""
                lines.append(f"- {mod['id']} / {mod['title']} [{state}]{desc}")
            if len(mods) > AI_EXTENSION_PLUGIN_SUMMARY_MAX:
                lines.append(f"- …另有 {len(mods) - AI_EXTENSION_PLUGIN_SUMMARY_MAX} 个 MOD，可用 list_mods 查看")
    return "\n".join(lines)


def write_plugin(name: str, code: str) -> Path:
    """新建/覆盖一个可执行插件（AITools/<slug>.py）。仅做基本校验，不在此执行。"""
    ensure_ai_context_files()
    source = str(code or "")
    if not source.strip():
        raise ValueError("插件代码（code）不能为空")
    if len(source.encode("utf-8")) > AI_PLUGIN_MAX_BYTES:
        raise ValueError(f"插件代码过大（上限 {AI_PLUGIN_MAX_BYTES} 字节）")
    if not re.search(r"^\s*def\s+run\s*\(", source, re.M):
        raise ValueError("插件必须定义 `def run(params):` 入口函数")
    try:
        compile(source, f"<plugin:{name}>", "exec")
    except SyntaxError as exc:
        raise ValueError(f"插件代码语法错误：{exc}") from exc
    path = _plugin_path(name)
    path.write_text(source, encoding="utf-8")
    return path


def delete_plugin(name: str) -> str:
    path = _plugin_path(name)
    if not path.is_file():
        raise ValueError(f"插件不存在：{name}")
    path.unlink()
    return f"已删除插件：{path.stem}"


_PLUGIN_RUNNER_SCRIPT = r"""
import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import traceback


def _main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    path = pathlib.Path(payload["path"])
    params = payload.get("params") or {}
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            spec = importlib.util.spec_from_file_location("passer_plugin_runtime", path)
            if spec is None or spec.loader is None:
                raise RuntimeError("无法加载插件模块")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            runner = getattr(module, "run", None)
            if not callable(runner):
                raise RuntimeError("插件缺少可调用的 run(params)")
            value = runner(params)
        if value is not None and not isinstance(value, str):
            try:
                value = json.dumps(value, ensure_ascii=False, indent=2, default=str)
            except Exception:
                value = str(value)
        print(json.dumps({"ok": True, "value": value, "stdout": captured.getvalue()}, ensure_ascii=False))
        return 0
    except BaseException:
        print(json.dumps({
            "ok": False,
            "error": traceback.format_exc(),
            "stdout": captured.getvalue(),
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
"""


_RUNNING_PLUGIN_LOCK = threading.Lock()
_RUNNING_PLUGIN_PROCS: set = set()


def _remember_plugin_proc(proc) -> None:
    with _RUNNING_PLUGIN_LOCK:
        _RUNNING_PLUGIN_PROCS.add(proc)


def _forget_plugin_proc(proc) -> None:
    with _RUNNING_PLUGIN_LOCK:
        _RUNNING_PLUGIN_PROCS.discard(proc)


def _terminate_plugin_proc(proc) -> bool:
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.terminate()
        proc.wait(timeout=0.8)
    except Exception:
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=0.8)
        except Exception:
            pass
    return True


def cancel_running_plugins() -> int:
    """终止当前 Aira 插件子进程；供聊天栏暂停按钮调用。"""
    with _RUNNING_PLUGIN_LOCK:
        procs = list(_RUNNING_PLUGIN_PROCS)
    stopped = 0
    for proc in procs:
        if _terminate_plugin_proc(proc):
            stopped += 1
    return stopped


def _plugin_text_clip(text: object, limit: int = 2000) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...（已截断）"


def _plugin_timeout_seconds(timeout: int | None) -> int:
    try:
        value = int(timeout or AI_PLUGIN_RUN_TIMEOUT)
    except (TypeError, ValueError):
        value = AI_PLUGIN_RUN_TIMEOUT
    return max(1, min(300, value))


def run_plugin(name: str, params: dict | None = None, timeout: int = AI_PLUGIN_RUN_TIMEOUT) -> str:
    """在独立 Python 进程里运行插件 run(params)，超时后可直接终止。"""
    path = _plugin_path(name)
    if not path.is_file():
        raise ValueError(f"插件不存在：{name}（请先用 create_plugin 创建）")
    timeout_s = _plugin_timeout_seconds(timeout)
    payload = json.dumps({"path": str(path), "params": params or {}}, ensure_ascii=False, default=str)
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _PLUGIN_RUNNER_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            text=True,
            cwd=str(path.parent),
            creationflags=flags,
            env=env,
        )
        _remember_plugin_proc(proc)
        stdout, stderr = proc.communicate(input=payload, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _terminate_plugin_proc(proc)
        return f"插件 {path.stem} 运行超时（>{timeout_s}s），已终止独立进程。"
    finally:
        if proc is not None:
            _forget_plugin_proc(proc)
    raw = (stdout or "").strip()
    try:
        result = json.loads(raw)
    except (TypeError, ValueError) as exc:
        detail = _plugin_text_clip((stderr or "") + ("\n" if stderr and raw else "") + raw)
        if proc.returncode:
            raise RuntimeError(f"插件 {path.stem} 运行出错：\n{detail}") from exc
        return (raw or f"插件 {path.stem} 运行完成（无返回值）。")[:AI_PLUGIN_RESULT_MAX]
    stdout_text = str(result.get("stdout") or "").strip()
    if not result.get("ok"):
        pieces = [str(result.get("error") or "未知错误").strip()]
        if stdout_text:
            pieces.append("stdout:\n" + stdout_text)
        if stderr:
            pieces.append("stderr:\n" + stderr.strip())
        raise RuntimeError(f"插件 {path.stem} 运行出错：\n{_plugin_text_clip(chr(10).join(pieces))}")
    value = result.get("value")
    value_text = str(value) if value not in (None, "") else f"插件 {path.stem} 运行完成（无返回值）。"
    if stdout_text:
        value_text = f"stdout:\n{stdout_text}\n\nreturn:\n{value_text}"
    if stderr:
        value_text = f"{value_text}\n\nstderr:\n{stderr.strip()}"
    return value_text[:AI_PLUGIN_RESULT_MAX]
