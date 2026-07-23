from __future__ import annotations

# Loaded into the parent module's global namespace by the parent file.
# Keep this file focused on the extracted feature area.

def web_search_preview(query: str, limit: int = 5) -> str:
    query = str(query or "").strip()
    if not query:
        raise ValueError("缺少搜索关键词")
    limit = max(1, min(int(limit or 5), 8))
    errors = []
    for source in (_web_search_bing, _web_search_duckduckgo):
        try:
            items, source_name = source(query, limit)
            if items:
                lines = [f"联网搜索：{query}（{source_name}）"]
                for i, (title, href, snippet) in enumerate(items[:limit], start=1):
                    lines.append(f"{i}. {title}\n   {href}\n   {snippet}")
                return "\n".join(lines)
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("联网搜索失败：" + "；".join(errors[-2:]))


def _web_search_duckduckgo(query: str, limit: int) -> tuple[list[tuple[str, str, str]], str]:
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PasserAI/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        html_text = resp.read().decode("utf-8", errors="replace")
    if "complete the following challenge" in html_text.lower():
        raise RuntimeError("DuckDuckGo 返回验证页")
    items = []
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        re.I | re.S,
    )
    for match in pattern.finditer(html_text):
        href = html_lib.unescape(re.sub(r"^//duckduckgo\.com/l/\?uddg=", "", match.group(1)))
        if "uddg=" in href:
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [""])[0]
            href = urllib.parse.unquote(parsed) or href
        title = _strip_html(match.group(2))
        snippet = _strip_html(match.group(3))
        if title:
            items.append((title, href, snippet))
        if len(items) >= limit:
            break
    return items, "DuckDuckGo"


def _web_search_bing(query: str, limit: int) -> tuple[list[tuple[str, str, str]], str]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "setlang": "zh-CN"})
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PasserAI/1.0",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        html_text = resp.read().decode("utf-8", errors="replace")
    items = []
    block_re = re.compile(r'<li class="b_algo"[^>]*>(.*?)</li>', re.I | re.S)
    for block in block_re.findall(html_text):
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h2>', block, re.I | re.S)
        if not m:
            continue
        snippet_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.I | re.S)
        href = _clean_bing_url(html_lib.unescape(m.group(1)))
        title = _strip_html(m.group(2))
        snippet = _strip_html(snippet_m.group(1)) if snippet_m else ""
        if title:
            items.append((title, href, snippet))
        if len(items) >= limit:
            break
    return items, "Bing"


def _clean_bing_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    encoded = (query.get("u") or [""])[0]
    if encoded:
        token = encoded[2:] if encoded.startswith("a1") else encoded
        try:
            padding = "=" * (-len(token) % 4)
            decoded = base64.urlsafe_b64decode((token + padding).encode("ascii")).decode("utf-8", errors="replace")
            if decoded.startswith(("http://", "https://")):
                return decoded
        except tk.TclError:
            pass
    return url


def _strip_html(text: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


# 学术文献搜索：用免 Key 的开放 API（Crossref 主、Semantic Scholar 备），返回结构化
# 论文条目（标题/作者/期刊/年份/DOI 链接/摘要）。比抓 Bing/DuckDuckGo HTML 稳得多，
# 不会撞验证码，也更贴合“查 Nature 上某主题最新论文”这类学术检索需求。
FETCH_URL_MAX_CHARS = 6000


def scholar_search_preview(query: str, limit: int = 6) -> str:
    query = str(query or "").strip()
    if not query:
        raise ValueError("缺少检索关键词")
    limit = max(1, min(int(limit or 6), 12))
    errors = []
    for source in (_scholar_crossref, _scholar_semantic):
        try:
            items, source_name = source(query, limit)
            if items:
                lines = [f"学术检索：{query}（{source_name}）"]
                for i, it in enumerate(items[:limit], start=1):
                    head = it["title"]
                    meta = "；".join(p for p in (it.get("venue"), it.get("year"), it.get("authors")) if p)
                    lines.append(f"{i}. {head}")
                    if meta:
                        lines.append(f"   {meta}")
                    if it.get("url"):
                        lines.append(f"   {it['url']}")
                    if it.get("abstract"):
                        lines.append(f"   摘要：{it['abstract']}")
                return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    raise RuntimeError("学术检索失败：" + "；".join(errors[-2:] or ["未返回结果"]))


def _scholar_crossref(query: str, limit: int) -> tuple[list[dict], str]:
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode({
        "query": query, "rows": limit, "select": "title,author,container-title,issued,DOI,URL,abstract",
        # mailto 是 Crossref 的礼貌池约定，可走更稳的速率通道。
        "mailto": "passer-ai@example.com",
    })
    req = urllib.request.Request(url, headers={"User-Agent": "PasserAI/1.0 (academic search)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    items = []
    for work in (data.get("message", {}) or {}).get("items", []):
        title_list = work.get("title") or []
        title = _strip_html(title_list[0]) if title_list else ""
        if not title:
            continue
        authors = []
        for a in (work.get("author") or [])[:4]:
            name = " ".join(p for p in (a.get("given"), a.get("family")) if p).strip()
            if name:
                authors.append(name)
        venue = (work.get("container-title") or [""])[0]
        parts = (work.get("issued", {}) or {}).get("date-parts") or [[]]
        year = str(parts[0][0]) if parts and parts[0] else ""
        doi = work.get("DOI") or ""
        link = work.get("URL") or (f"https://doi.org/{doi}" if doi else "")
        abstract = _strip_html(work.get("abstract") or "")[:240]
        items.append({"title": title, "venue": venue, "year": year,
                      "authors": ", ".join(authors), "url": link, "abstract": abstract})
        if len(items) >= limit:
            break
    return items, "Crossref"


def _scholar_semantic(query: str, limit: int) -> tuple[list[dict], str]:
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode({
        "query": query, "limit": limit,
        "fields": "title,abstract,year,venue,authors,url,externalIds",
    })
    req = urllib.request.Request(url, headers={"User-Agent": "PasserAI/1.0 (academic search)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    items = []
    for paper in data.get("data", []) or []:
        title = _strip_html(paper.get("title") or "")
        if not title:
            continue
        authors = ", ".join(a.get("name", "") for a in (paper.get("authors") or [])[:4] if a.get("name"))
        doi = (paper.get("externalIds") or {}).get("DOI")
        link = paper.get("url") or (f"https://doi.org/{doi}" if doi else "")
        items.append({"title": title, "venue": paper.get("venue") or "",
                      "year": str(paper.get("year") or ""), "authors": authors,
                      "url": link, "abstract": _strip_html(paper.get("abstract") or "")[:240]})
        if len(items) >= limit:
            break
    return items, "Semantic Scholar"


def fetch_url_text(url: str, max_chars: int = FETCH_URL_MAX_CHARS) -> str:
    """抓取一个网页并提取正文纯文本（供模型“浏览/阅读”网页内容）。仅允许 http(s)。"""
    url = str(url or "").strip()
    if not url:
        raise ValueError("缺少网址")
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("仅支持 http/https 网址")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PasserAI/1.0",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read(2 * 1024 * 1024)  # 最多读 2MB，避免超大页面
        charset = resp.headers.get_content_charset() or "utf-8"
    html_text = raw.decode(charset, errors="replace")
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
    title = _strip_html(title_m.group(1)) if title_m else ""
    body = _strip_html(html_text)
    cap = max(500, min(int(max_chars or FETCH_URL_MAX_CHARS), 20000))
    truncated = len(body) > cap
    head = f"网页：{title}\n{url}\n\n" if title else f"网页：{url}\n\n"
    return head + body[:cap] + ("…（已截断）" if truncated else "")
