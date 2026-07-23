"""把计算器表达式排版成 fx-991 风格的二维数学式，绘制到 Canvas 上。

支持：文本/数字/运算符、分数 a/b、根号 √(...)、上标 ^/²/³、括号、
占位框 □、定积分 ∫_a^b(f)d[x]、不定积分 ∫f d[x]。光标由 Canvas
直接绘制，不依赖字体里的特殊竖线字符，避免显示成乱码方块。
"""
from __future__ import annotations

import re
import tkinter as tk

CURSOR_MARK = "\ue000"
_SPECIAL = "()/^²³√□∫" + CURSOR_MARK


# --- 解析：线性串 -> 排版树 ----------------------------------------------------
def _parse(s: str):
    pos = [0]
    n = len(s)

    def peek():
        return s[pos[0]] if pos[0] < n else ""

    def take_if(ch: str) -> bool:
        if peek() == ch:
            pos[0] += 1
            return True
        return False

    def text_run(stop):
        start = pos[0]
        while pos[0] < n and s[pos[0]] not in _SPECIAL and s[pos[0]] not in stop:
            pos[0] += 1
        if pos[0] == start and pos[0] < n:
            pos[0] += 1
        return ("text", s[start:pos[0]])

    def atom(stop):
        ch = peek()
        if ch in ("^", "²", "³"):
            # 没有底数的幂（如表达式开头或括号内直接出现 ^）：真机会显示成
            # 一个空占位框作底数的上标 □ⁿ，绝不把 "^" 当字面字符画出来。
            if take_if("^"):
                return ("sup", ("box",), super_atom(stop))
            pos[0] += 1
            return ("sup", ("box",), ("text", "2" if ch == "²" else "3"))
        if ch == CURSOR_MARK:
            pos[0] += 1
            node = ("cursor",)
        elif ch == "(":
            pos[0] += 1
            inner = row(")")
            take_if(")")
            node = ("paren", inner)
        elif ch == "√":
            pos[0] += 1
            if take_if("("):
                inner = row(")")
                take_if(")")
            else:
                inner = atom(stop)
            node = ("sqrt", inner)
        elif ch == "∫":
            node = integral()
        elif ch == "□":
            pos[0] += 1
            node = ("box",)
        else:
            node = text_run(stop)
        while peek() in ("^", "²", "³") and peek() not in stop:
            if take_if("^"):
                node = ("sup", node, super_atom(stop))
            else:
                e = peek()
                pos[0] += 1
                node = ("sup", node, ("text", "2" if e == "²" else "3"))
        return node

    def super_atom(stop):
        if take_if("("):
            inner = row(")")
            take_if(")")
            return ("paren", inner)
        start = pos[0]
        while (
            pos[0] < n
            and s[pos[0]] not in _SPECIAL
            and s[pos[0]] not in "+-*×÷ "
            and s[pos[0]] not in stop
        ):
            pos[0] += 1
        if pos[0] == start and pos[0] < n:
            pos[0] += 1
        return ("text", s[start:pos[0]])

    def integral():
        pos[0] += 1
        lower = upper = None
        if take_if("_"):
            lower = row("^(")
        if take_if("^"):
            upper = row("(")
        if take_if("("):
            body = row(")")
            take_if(")")
        else:
            parts = []
            while pos[0] < n and not s.startswith("d[", pos[0]):
                node = atom("")
                while peek() == "/":
                    pos[0] += 1
                    numerator = node
                    if node[0] == "text":
                        match = re.search(r"[0-9A-Za-z.πe]+$", node[1])
                        if match and match.start() > 0:
                            parts.append(("text", node[1][:match.start()]))
                            numerator = ("text", match.group(0))
                    node = ("frac", numerator, atom(""))
                parts.append(node)
            body = ("row", parts)
        var = None
        if s.startswith("d[", pos[0]):
            pos[0] += 2
            start = pos[0]
            while pos[0] < n and s[pos[0]] != "]":
                pos[0] += 1
            var = s[start:pos[0]] or "x"
            take_if("]")
        return ("integral", lower, upper, body, var)

    def row(stop):
        items = []
        while pos[0] < n and peek() not in stop:
            # 安全网：行内出现落单的幂符号（如光标把 base 和 ^ 拆开时），
            # 直接把它作为上标绑到前一个元素，绝不显示成字面的 "^"。
            stray = peek()
            if stray in ("^", "²", "³") and items:
                base = items.pop()
                if take_if("^"):
                    items.append(("sup", base, super_atom(stop)))
                else:
                    pos[0] += 1
                    items.append(("sup", base, ("text", "2" if stray == "²" else "3")))
                continue
            node = atom(stop)
            while peek() == "/" and "/" not in stop:
                pos[0] += 1
                # 分数只取前一个操作数作分子：若上一节点是纯文本（如 "1+2"），
                # 拆出尾部操作数 "2"，把 "1+" 留在分数左侧，贴近真机的 a+b/c 排版。
                numerator = node
                if node[0] == "text":
                    match = re.search(r"[0-9A-Za-z.πe]+$", node[1])
                    if match and match.start() > 0:
                        items.append(("text", node[1][:match.start()]))
                        numerator = ("text", match.group(0))
                node = ("frac", numerator, atom(stop))
            items.append(node)
        return ("row", items)

    return row("")


# --- 排版度量：返回 (width, ascent, descent) ----------------------------------
class _Layout:
    def __init__(self, canvas, base_size=26, fg="#e2e8f0", caret=None):
        self.c = canvas
        self.base = base_size
        self.fg = fg
        # 光标颜色（默认跟随文字颜色）。真机 LCD 上是与笔画同色的黑竖线。
        self.caret = caret or fg

    def font(self, size, weight="normal", math=False):
        # Cambria Math is excellent for large math operators, but normal digits
        # and superscripts can look like garbled glyphs on Tk Canvas. Use Segoe
        # UI for ordinary text and reserve Cambria Math for integral signs.
        family = "Cambria Math" if math else "Segoe UI"
        return (family, size, weight)

    def _text_metrics(self, s, size, math=False):
        if not s:
            return 0, int(size * 0.72), int(size * 0.28)
        fid = self.c.create_text(-1000, -1000, text=s, font=self.font(size, math=math), anchor="nw")
        bbox = self.c.bbox(fid)
        self.c.delete(fid)
        if not bbox:
            return max(1, int(size * 0.45 * len(s))), int(size * 0.72), int(size * 0.30)
        x0, y0, x1, y1 = bbox
        return x1 - x0, int(size * 0.72), int(size * 0.30)

    def measure(self, node, size):
        kind = node[0]
        if kind == "text":
            return self._text_metrics(node[1], size)
        if kind == "cursor":
            return 5, int(size * 0.82), int(size * 0.24)
        if kind == "box":
            h = int(size * 0.58)
            return h + 8, h, int(size * 0.12)
        if kind == "row":
            w = a = d = 0
            for ch in node[1]:
                cw, ca, cd = self.measure(ch, size)
                w += cw
                a = max(a, ca)
                d = max(d, cd)
            if not node[1]:
                a, d = int(size * 0.72), int(size * 0.28)
            return w, a, d
        if kind == "paren":
            iw, ia, idd = self.measure(node[1], size)
            pw, _, _ = self._text_metrics("(", size)
            return iw + 2 * pw + 4, ia, idd
        if kind == "sqrt":
            iw, ia, idd = self.measure(node[1], size)
            clr = max(6, int(size * 0.18))
            return iw + int(size * 0.7) + 8, ia + clr + 2, idd
        if kind == "sup":
            bw, ba, bd = self.measure(node[1], size)
            esz = max(10, int(size * 0.52))
            ew, ea, _ = self.measure(node[2], esz)
            # 上标抬到底数右上角；预留 0.82*ba 的高度避免与上一行/边框重叠。
            return bw + ew + 4, max(ba, int(ba * 0.82) + ea), bd
        if kind == "frac":
            fsz = max(11, int(size * 0.82))
            nw, na, nd = self.measure(node[1], fsz)
            dw, da, dd = self.measure(node[2], fsz)
            w = max(nw, dw) + 10
            mid, gap = int(size * 0.30), max(6, int(size * 0.16))
            return w, mid + gap + na + nd, max(gap + da + dd - mid, int(size * 0.30))
        if kind == "integral":
            _, lower, upper, body, var = node
            isz = int(size * 1.8)
            iw, ia, idd = self._text_metrics("∫", isz, math=True)
            lim_size = max(10, int(size * 0.48))
            lw = la = ld = uw = ua = ud = 0
            if lower:
                lw, la, ld = self.measure(lower, lim_size)
            if upper:
                uw, ua, ud = self.measure(upper, lim_size)
            bw, ba, bd = self.measure(body, size)
            vw, va, vd = self._text_metrics(f"d{var or 'x'}", max(11, int(size * 0.72))) if var else (0, 0, 0)
            left_w = max(iw, lw, uw) + 8
            width = left_w + bw + vw + 8
            upper_gap = int(size * 0.95)
            lower_gap = int(size * 0.85)
            asc = max(ia, ba, upper_gap + ua + ud)
            desc = max(idd, bd, lower_gap + la + ld, vd)
            return width, asc, desc
        return self._text_metrics("?", size)

    def draw(self, node, x, ybase, size):
        kind = node[0]
        if kind == "text":
            self.c.create_text(x, ybase, text=node[1], font=self.font(size), anchor="w", fill=self.fg)
            w, _, _ = self._text_metrics(node[1], size)
            return w
        if kind == "cursor":
            h = int(size * 0.86)
            self.c.create_line(x + 2, ybase - h, x + 2, ybase + int(size * 0.22),
                               fill=self.caret, width=2)
            return 5
        if kind == "box":
            h = int(size * 0.58)
            self.c.create_rectangle(x + 3, ybase - h, x + 3 + h, ybase,
                                    outline=self.fg, width=2)
            return h + 8
        if kind == "row":
            cx = x
            for ch in node[1]:
                cx += self.draw(ch, cx, ybase, size)
            return cx - x
        if kind == "paren":
            pw, _, _ = self._text_metrics("(", size)
            iw, _, _ = self.measure(node[1], size)
            self.c.create_text(x, ybase, text="(", font=self.font(int(size * 1.05)), anchor="w", fill=self.fg)
            self.draw(node[1], x + pw + 2, ybase, size)
            self.c.create_text(x + pw + 2 + iw + 2, ybase, text=")", font=self.font(int(size * 1.05)),
                               anchor="w", fill=self.fg)
            return iw + 2 * pw + 4
        if kind == "sqrt":
            iw, ia, idd = self.measure(node[1], size)
            hook = int(size * 0.7)
            clr = max(6, int(size * 0.18))
            top = ybase - ia - clr
            self.c.create_line(x + hook * 0.25, ybase - idd, x + hook * 0.5, ybase + idd,
                               x + hook * 0.9, top, x + hook + iw + 6, top,
                               fill=self.fg, width=2, joinstyle="round")
            self.draw(node[1], x + hook + 4, ybase, size)
            return iw + hook + 8
        if kind == "sup":
            bw = self.draw(node[1], x, ybase, size)
            esz = max(10, int(size * 0.52))
            _, ba, _ = self.measure(node[1], size)
            # 抬到底数顶部右上角，明显高于基线，像真机的次方。
            self.draw(node[2], x + bw + 2, ybase - int(ba * 0.82), esz)
            ew, _, _ = self.measure(node[2], esz)
            return bw + ew + 4
        if kind == "frac":
            fsz = max(11, int(size * 0.82))
            nw, _, nd = self.measure(node[1], fsz)
            dw, da, _ = self.measure(node[2], fsz)
            w = max(nw, dw) + 10
            mid, gap = int(size * 0.30), max(6, int(size * 0.16))
            bar_y = ybase - mid
            self.draw(node[1], x + (w - nw) / 2, bar_y - gap - nd, fsz)
            self.draw(node[2], x + (w - dw) / 2, bar_y + gap + da, fsz)
            self.c.create_line(x + 2, bar_y, x + w - 2, bar_y, fill=self.fg, width=2)
            return w
        if kind == "integral":
            _, lower, upper, body, var = node
            isz = int(size * 1.8)
            iw, _, _ = self._text_metrics("∫", isz, math=True)
            lim_size = max(10, int(size * 0.48))
            lw = uw = 0
            if lower:
                lw, _, _ = self.measure(lower, lim_size)
            if upper:
                uw, _, _ = self.measure(upper, lim_size)
            left_w = max(iw, lw, uw) + 8
            ix = x + (left_w - iw) / 2
            self.c.create_text(ix, ybase, text="∫", font=self.font(isz, math=True), anchor="w", fill=self.fg)
            if upper:
                self.draw(upper, x + (left_w - uw) / 2, ybase - int(size * 0.95), lim_size)
            if lower:
                self.draw(lower, x + (left_w - lw) / 2, ybase + int(size * 0.85), lim_size)
            bw = self.draw(body, x + left_w, ybase, size)
            if var:
                dsize = max(11, int(size * 0.72))
                self.c.create_text(x + left_w + bw + 6, ybase, text=f"d{var}",
                                   font=self.font(dsize), anchor="w", fill=self.fg)
                vw, _, _ = self._text_metrics(f"d{var}", dsize)
                return left_w + bw + vw + 8
            return left_w + bw
        return 0

    def locate(self, node, x, size):
        """与 draw 同样的排版几何，但只返回光标节点的绝对 x（找不到返回 None）。"""
        kind = node[0]
        if kind == "cursor":
            return x + 2
        if kind in ("text", "box"):
            return None
        if kind == "row":
            cx = x
            for ch in node[1]:
                found = self.locate(ch, cx, size)
                if found is not None:
                    return found
                cx += self.measure(ch, size)[0]
            return None
        if kind == "paren":
            pw, _, _ = self._text_metrics("(", size)
            return self.locate(node[1], x + pw + 2, size)
        if kind == "sqrt":
            hook = int(size * 0.7)
            return self.locate(node[1], x + hook + 4, size)
        if kind == "sup":
            found = self.locate(node[1], x, size)
            if found is not None:
                return found
            bw = self.measure(node[1], size)[0]
            esz = max(10, int(size * 0.52))
            return self.locate(node[2], x + bw + 2, esz)
        if kind == "frac":
            fsz = max(11, int(size * 0.82))
            nw, _, _ = self.measure(node[1], fsz)
            dw, _, _ = self.measure(node[2], fsz)
            w = max(nw, dw) + 10
            found = self.locate(node[1], x + (w - nw) / 2, fsz)
            if found is not None:
                return found
            return self.locate(node[2], x + (w - dw) / 2, fsz)
        if kind == "integral":
            _, lower, upper, body, var = node
            isz = int(size * 1.8)
            iw, _, _ = self._text_metrics("∫", isz, math=True)
            lim_size = max(10, int(size * 0.48))
            lw = uw = 0
            if lower:
                lw, _, _ = self.measure(lower, lim_size)
            if upper:
                uw, _, _ = self.measure(upper, lim_size)
            left_w = max(iw, lw, uw) + 8
            if upper:
                found = self.locate(upper, x + (left_w - uw) / 2, lim_size)
                if found is not None:
                    return found
            if lower:
                found = self.locate(lower, x + (left_w - lw) / 2, lim_size)
                if found is not None:
                    return found
            return self.locate(body, x + left_w, size)
        return None


def render(canvas: tk.Canvas, text: str, *, base_size: int = 26,
           pad: int = 12, fg: str = "#e2e8f0", placeholder: str = "0",
           caret: str | None = None) -> None:
    """清空 canvas 并把 text 以二维数学式右对齐绘制。"""
    canvas.delete("all")
    shown = text if text.strip() else placeholder
    shown = shown.replace("*", "×")   # 乘号按 991 显示为 ×，不显示星号
    tree = _parse(shown)
    cw = max(canvas.winfo_width(), 1)
    ch = max(canvas.winfo_height(), 1)

    size = base_size
    min_size = 16
    while True:
        lay = _Layout(canvas, size, fg, caret)
        w, asc, desc = lay.measure(tree, size)
        fits_w = w <= max(1, cw - pad * 2)
        fits_h = asc + desc <= max(1, ch - pad * 2)
        if (fits_w and fits_h) or size <= min_size:
            break
        size -= 2

    x = max(pad, cw - w - pad)
    ideal = (ch + asc - desc) / 2
    ybase = min(max(ideal, pad + asc), max(pad + asc, ch - pad - desc))
    lay.draw(tree, x, ybase, size)


def caret_index_at(canvas: tk.Canvas, text: str, click_x: float, *,
                   base_size: int = 26, pad: int = 12, placeholder: str = "0") -> int:
    """根据鼠标点击的横坐标，返回最接近的光标插入位置（0..len(text)）。"""
    if not text:
        return 0
    if len(text) > 80:  # 超长表达式退化为比例估算，避免逐位重排卡顿
        ratio = max(0.0, min(1.0, click_x / max(1, canvas.winfo_width())))
        return round(ratio * len(text))
    base = (text if text.strip() else placeholder).replace("*", "×")
    tree = _parse(base)
    cw = max(canvas.winfo_width(), 1)
    chh = max(canvas.winfo_height(), 1)
    size, min_size = base_size, 16
    lay = _Layout(canvas, size)
    while True:
        lay = _Layout(canvas, size)
        w, asc, desc = lay.measure(tree, size)
        if (w <= max(1, cw - pad * 2) and asc + desc <= max(1, chh - pad * 2)) or size <= min_size:
            break
        size -= 2
    x0 = max(pad, cw - w - pad)
    best_i, best_d = 0, None
    for i in range(len(text) + 1):
        shown = (text[:i] + CURSOR_MARK + text[i:]).replace("*", "×")
        cx = lay.locate(_parse(shown), x0, size)
        if cx is None:
            continue
        distance = abs(cx - click_x)
        if best_d is None or distance < best_d:
            best_d, best_i = distance, i
    return best_i
