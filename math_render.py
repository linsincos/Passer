"""把计算器表达式排版成 fx-991CN X 风格的自然书写二维数学式。

支持文本、分数、根式、上下标、括号、占位框、定积分与不定积分。
输入区使用真机式的固定字号和横向视窗；分子/分母及底数/指数拥有各自
的二维光标坐标，因此鼠标和方向键都能在数学结构中定位，而不只是在线性
字符串上移动。光标由 Canvas 直接绘制，避免依赖字体里的特殊字符。
"""
from __future__ import annotations

import re
import tkinter as tk

CURSOR_MARK = "\ue000"
_SPECIAL = "()/^²³√□∫|" + CURSOR_MARK
_NATURAL_PREFIXES = ("summation(", "product(", "root(", "log_", "d/d[")


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

    def balanced_text() -> str:
        """Consume one parenthesized source group and return its inner text."""
        if not take_if("("):
            return ""
        start = pos[0]
        depth = 1
        while pos[0] < n:
            ch = s[pos[0]]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    value = s[start:pos[0]]
                    pos[0] += 1
                    return value
            pos[0] += 1
        return s[start:pos[0]]

    def split_args(value: str) -> list[str]:
        parts: list[str] = []
        start = depth = 0
        for index, ch in enumerate(value):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                parts.append(value[start:index].strip())
                start = index + 1
        parts.append(value[start:].strip())
        return parts

    def ensure_parameter_node(node):
        if node[0] == "row" and not node[1]:
            return ("row", [("box",)])
        if node[0] == "row" and node[1] == [("cursor",)]:
            return ("slot", ("cursor",))
        return node

    def parameter_node(value: str):
        """Keep an unfinished required parameter visible as a ClassWiz box."""
        return ensure_parameter_node(_parse(value))

    def natural_call(name: str):
        """Parse renderer-only natural forms while keeping evaluator syntax."""
        pos[0] += len(name)
        args = split_args(balanced_text())
        if name == "root":
            index = parameter_node(args[0] if args else "")
            radicand = parameter_node(args[1] if len(args) > 1 else "")
            return ("nroot", index, radicand)

        body = parameter_node(args[0] if args else "")
        variable, lower, upper = "n", parameter_node(""), parameter_node("")
        if len(args) > 1:
            bounds = args[1]
            if bounds.startswith("(") and bounds.endswith(")"):
                bounds = bounds[1:-1]
            values = split_args(bounds)
            if values:
                variable = values[0] or "n"
            if len(values) > 1:
                lower = parameter_node(values[1])
            if len(values) > 2:
                upper = parameter_node(values[2])
        return ("bigop", "Σ" if name == "summation" else "Π",
                body, variable, lower, upper)

    def logarithm():
        pos[0] += len("log_")
        start = pos[0]
        while pos[0] < n and s[pos[0]] != "(":
            pos[0] += 1
        base = parameter_node(s[start:pos[0]])
        argument = parameter_node(balanced_text())
        return ("logbase", base, argument)

    def derivative():
        pos[0] += len("d/d[")
        start = pos[0]
        while pos[0] < n and s[pos[0]] != "]":
            pos[0] += 1
        variable = s[start:pos[0]] or "x"
        take_if("]")
        body = parameter_node(balanced_text())
        point = None
        marker = f"|{variable}="
        if s.startswith(marker, pos[0]):
            pos[0] += len(marker)
            point_start = pos[0]
            while pos[0] < n and s[pos[0]] not in "+−*/×÷":
                pos[0] += 1
            point = parameter_node(s[point_start:pos[0]])
        return ("derivative", variable, body, point)

    def text_run(stop):
        start = pos[0]
        while pos[0] < n and s[pos[0]] not in _SPECIAL and s[pos[0]] not in stop:
            # A natural-display construct may follow an ordinary operator in
            # the same row (for example ``2+log_2(8)``). Stop the text token at
            # that boundary so the next atom receives the dedicated 2D node.
            if pos[0] > start and any(s.startswith(prefix, pos[0]) for prefix in _NATURAL_PREFIXES):
                break
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
        if s.startswith("summation(", pos[0]):
            node = natural_call("summation")
        elif s.startswith("product(", pos[0]):
            node = natural_call("product")
        elif s.startswith("root(", pos[0]):
            node = natural_call("root")
        elif s.startswith("log_", pos[0]):
            node = logarithm()
        elif s.startswith("d/d[", pos[0]):
            node = derivative()
        elif ch == CURSOR_MARK:
            pos[0] += 1
            node = ("cursor",)
        elif ch == "(":
            pos[0] += 1
            inner = row(")")
            take_if(")")
            node = ("paren", ensure_parameter_node(inner))
        elif ch == "√":
            pos[0] += 1
            if take_if("("):
                inner = row(")")
                take_if(")")
            else:
                inner = atom(stop)
            node = ("sqrt", ensure_parameter_node(inner))
        elif ch == "∫":
            node = integral()
        elif ch == "|":
            pos[0] += 1
            inner = row("|")
            take_if("|")
            node = ("abs", ensure_parameter_node(inner))
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
        if peek() == CURSOR_MARK:
            pos[0] += 1
            return ("slot", ("cursor",))
        if not peek():
            return ("box",)
        if take_if("("):
            inner = row(")")
            take_if(")")
            # ``^(...)`` 中的括号只是线性存储格式的结构边界；ClassWiz
            # 自然书写屏不会把它们画在指数周围。
            return ensure_parameter_node(inner)
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
        value = s[start:pos[0]]
        return ("text", value) if value else ("box",)

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
                    denominator = atom("")
                    if denominator == ("text", ""):
                        denominator = ("box",)
                    elif denominator == ("cursor",):
                        denominator = ("slot", denominator)
                    if numerator == ("text", ""):
                        numerator = ("box",)
                    elif numerator == ("cursor",):
                        numerator = ("slot", numerator)
                    if numerator[0] == "paren":
                        numerator = numerator[1]
                    if denominator[0] == "paren":
                        denominator = denominator[1]
                    node = ("frac", numerator, denominator)
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
        if lower is not None:
            lower = ensure_parameter_node(lower)
        if upper is not None:
            upper = ensure_parameter_node(upper)
        body = ensure_parameter_node(body)
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
                denominator = atom(stop)
                if denominator == ("text", ""):
                    denominator = ("box",)
                elif denominator == ("cursor",):
                    denominator = ("slot", denominator)
                if numerator == ("text", ""):
                    numerator = ("box",)
                elif numerator == ("cursor",):
                    numerator = ("slot", numerator)
                # ``(□)/(□)`` 用普通括号在底层字符串中保护分子和分母，
                # 但自然书写显示只呈现分数线，不呈现这对结构括号。
                if numerator[0] == "paren":
                    numerator = numerator[1]
                if denominator[0] == "paren":
                    denominator = denominator[1]
                node = ("frac", numerator, denominator)
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
        if kind == "slot":
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
            psize = max(size, int((ia + idd) * 0.94))
            pw, pa, pd = self._text_metrics("(", psize, math=True)
            return iw + 2 * pw + 4, max(ia, pa), max(idd, pd)
        if kind == "abs":
            iw, ia, idd = self.measure(node[1], size)
            return iw + 14, ia + 2, idd + 2
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
        if kind == "nroot":
            _, index, radicand = node
            iw, ia, idd = self.measure(index, max(9, int(size * 0.46)))
            rw, ra, rd = self.measure(radicand, size)
            hook = int(size * 0.72)
            left = max(int(iw * 0.72), int(hook * 0.35))
            clear = max(6, int(size * 0.18))
            return (left + hook + rw + 8,
                    max(ra + clear + 2, int(ra * 0.70) + ia + idd), rd)
        if kind == "logbase":
            _, base, argument = node
            logw, loga, logd = self._text_metrics("log", size)
            bsz = max(9, int(size * 0.48))
            bw, ba, bd = self.measure(base, bsz)
            aw, aa, ad = self.measure(("paren", argument), size)
            return logw + bw + aw + 4, max(loga, aa), max(logd + ba + bd, ad)
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
            limit_x = int(iw * 0.62)
            left_w = max(iw, limit_x + lw, limit_x + uw) + 8
            width = left_w + bw + vw + 8
            upper_gap = int(size * 0.95)
            lower_gap = int(size * 0.85)
            asc = max(ia, ba, upper_gap + ua + ud)
            desc = max(idd, bd, lower_gap + la + ld, vd)
            return width, asc, desc
        if kind == "bigop":
            _, symbol, body, variable, lower, upper = node
            osz = int(size * 1.34)
            ow, oa, od = self._text_metrics(symbol, osz, math=True)
            lsz = max(9, int(size * 0.46))
            lower_node = ("row", [("text", f"{variable}="), lower])
            lw, la, ld = self.measure(lower_node, lsz)
            uw, ua, ud = self.measure(upper, lsz)
            bw, ba, bd = self.measure(body, size)
            left = max(ow, lw, uw) + 8
            return (left + bw + 4,
                    max(oa, ba, int(size * 0.84) + ua + ud),
                    max(od, bd, int(size * 0.70) + la + ld))
        if kind == "derivative":
            _, variable, body, point = node
            frac = ("frac", ("text", "d"), ("text", f"d{variable}"))
            fw, fa, fd = self.measure(frac, max(14, int(size * 0.86)))
            bw, ba, bd = self.measure(body, size)
            width = fw + bw + 8
            asc, desc = max(fa, ba), max(fd, bd)
            if point is not None:
                psz = max(9, int(size * 0.48))
                point_node = ("row", [("text", f"{variable}="), point])
                pw, pa, pd = self.measure(point_node, psz)
                width += pw + 8
                desc = max(desc, pa + pd)
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
        if kind == "slot":
            h = int(size * 0.58)
            self.c.create_rectangle(x + 3, ybase - h, x + 3 + h, ybase,
                                    outline=self.fg, width=2)
            self.draw(node[1], x + 3 + h / 2 - 2, ybase, size)
            return h + 8
        if kind == "row":
            cx = x
            for ch in node[1]:
                cx += self.draw(ch, cx, ybase, size)
            return cx - x
        if kind == "paren":
            iw, ia, idd = self.measure(node[1], size)
            psize = max(size, int((ia + idd) * 0.94))
            pw, _, _ = self._text_metrics("(", psize, math=True)
            self.c.create_text(x, ybase, text="(", font=self.font(psize, math=True), anchor="w", fill=self.fg)
            self.draw(node[1], x + pw + 2, ybase, size)
            self.c.create_text(x + pw + 2 + iw + 2, ybase, text=")", font=self.font(psize, math=True),
                               anchor="w", fill=self.fg)
            return iw + 2 * pw + 4
        if kind == "abs":
            iw, ia, idd = self.measure(node[1], size)
            top, bottom = ybase - ia - 2, ybase + idd + 2
            self.c.create_line(x + 3, top, x + 3, bottom, fill=self.fg, width=2)
            self.draw(node[1], x + 7, ybase, size)
            self.c.create_line(x + 10 + iw, top, x + 10 + iw, bottom, fill=self.fg, width=2)
            return iw + 14
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
        if kind == "nroot":
            _, index, radicand = node
            isz = max(9, int(size * 0.46))
            iw, _, _ = self.measure(index, isz)
            rw, ra, rd = self.measure(radicand, size)
            hook = int(size * 0.72)
            left = max(int(iw * 0.72), int(hook * 0.35))
            clear = max(6, int(size * 0.18))
            radical_x = x + left
            top = ybase - ra - clear
            self.draw(index, x, ybase - int(ra * 0.70), isz)
            self.c.create_line(radical_x + hook * 0.20, ybase - rd,
                               radical_x + hook * 0.46, ybase + rd,
                               radical_x + hook * 0.88, top,
                               radical_x + hook + rw + 6, top,
                               fill=self.fg, width=2, joinstyle="round")
            self.draw(radicand, radical_x + hook + 4, ybase, size)
            return left + hook + rw + 8
        if kind == "logbase":
            _, base, argument = node
            logw, _, logd = self._text_metrics("log", size)
            self.c.create_text(x, ybase, text="log", font=self.font(size), anchor="w", fill=self.fg)
            bsz = max(9, int(size * 0.48))
            bw = self.draw(base, x + logw, ybase + logd + int(size * 0.18), bsz)
            aw = self.draw(("paren", argument), x + logw + bw + 2, ybase, size)
            return logw + bw + aw + 4
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
            limit_x = int(iw * 0.62)
            left_w = max(iw, limit_x + lw, limit_x + uw) + 8
            self.c.create_text(x, ybase, text="∫", font=self.font(isz, math=True), anchor="w", fill=self.fg)
            if upper:
                self.draw(upper, x + limit_x, ybase - int(size * 0.95), lim_size)
            if lower:
                self.draw(lower, x + limit_x, ybase + int(size * 0.85), lim_size)
            bw = self.draw(body, x + left_w, ybase, size)
            if var:
                dsize = max(11, int(size * 0.72))
                self.c.create_text(x + left_w + bw + 6, ybase, text=f"d{var}",
                                   font=self.font(dsize), anchor="w", fill=self.fg)
                vw, _, _ = self._text_metrics(f"d{var}", dsize)
                return left_w + bw + vw + 8
            return left_w + bw
        if kind == "bigop":
            _, symbol, body, variable, lower, upper = node
            osz = int(size * 1.34)
            ow, _, _ = self._text_metrics(symbol, osz, math=True)
            lsz = max(9, int(size * 0.46))
            lower_node = ("row", [("text", f"{variable}="), lower])
            lw, _, _ = self.measure(lower_node, lsz)
            uw, _, _ = self.measure(upper, lsz)
            left = max(ow, lw, uw) + 8
            ox = x + (left - ow) / 2
            self.c.create_text(ox, ybase, text=symbol, font=self.font(osz, math=True),
                               anchor="w", fill=self.fg)
            self.draw(upper, x + (left - uw) / 2, ybase - int(size * 0.84), lsz)
            self.draw(lower_node, x + (left - lw) / 2, ybase + int(size * 0.70), lsz)
            bw = self.draw(body, x + left, ybase, size)
            return left + bw + 4
        if kind == "derivative":
            _, variable, body, point = node
            fsize = max(14, int(size * 0.86))
            frac = ("frac", ("text", "d"), ("text", f"d{variable}"))
            fw = self.draw(frac, x, ybase, fsize)
            bw = self.draw(body, x + fw + 5, ybase, size)
            width = fw + bw + 8
            if point is not None:
                psz = max(9, int(size * 0.48))
                point_node = ("row", [("text", f"{variable}="), point])
                pw, pa, pd = self.measure(point_node, psz)
                bar_x = x + width
                self.c.create_line(bar_x, ybase - int(size * 0.72), bar_x,
                                   ybase + int(size * 0.44), fill=self.fg, width=2)
                self.draw(point_node, bar_x + 4, ybase + pa + pd, psz)
                width += pw + 8
            return width
        return 0

    def locate(self, node, x, ybase, size):
        """Return the absolute ``(x, y)`` of the embedded cursor node."""
        kind = node[0]
        if kind == "cursor":
            return x + 2, ybase
        if kind in ("text", "box"):
            return None
        if kind == "slot":
            h = int(size * 0.58)
            return self.locate(node[1], x + 3 + h / 2 - 2, ybase, size)
        if kind == "row":
            cx = x
            for ch in node[1]:
                found = self.locate(ch, cx, ybase, size)
                if found is not None:
                    return found
                cx += self.measure(ch, size)[0]
            return None
        if kind == "paren":
            _, ia, idd = self.measure(node[1], size)
            psize = max(size, int((ia + idd) * 0.94))
            pw, _, _ = self._text_metrics("(", psize, math=True)
            return self.locate(node[1], x + pw + 2, ybase, size)
        if kind == "abs":
            return self.locate(node[1], x + 7, ybase, size)
        if kind == "sqrt":
            hook = int(size * 0.7)
            return self.locate(node[1], x + hook + 4, ybase, size)
        if kind == "sup":
            found = self.locate(node[1], x, ybase, size)
            if found is not None:
                return found
            bw, ba, _ = self.measure(node[1], size)
            esz = max(10, int(size * 0.52))
            return self.locate(node[2], x + bw + 2, ybase - int(ba * 0.82), esz)
        if kind == "nroot":
            _, index, radicand = node
            isz = max(9, int(size * 0.46))
            iw, _, _ = self.measure(index, isz)
            _, ra, _ = self.measure(radicand, size)
            hook = int(size * 0.72)
            left = max(int(iw * 0.72), int(hook * 0.35))
            found = self.locate(index, x, ybase - int(ra * 0.70), isz)
            if found is not None:
                return found
            return self.locate(radicand, x + left + hook + 4, ybase, size)
        if kind == "logbase":
            _, base, argument = node
            logw, _, logd = self._text_metrics("log", size)
            bsz = max(9, int(size * 0.48))
            bw, _, _ = self.measure(base, bsz)
            found = self.locate(base, x + logw, ybase + logd + int(size * 0.18), bsz)
            if found is not None:
                return found
            return self.locate(("paren", argument), x + logw + bw + 2, ybase, size)
        if kind == "frac":
            fsz = max(11, int(size * 0.82))
            nw, _, nd = self.measure(node[1], fsz)
            dw, da, _ = self.measure(node[2], fsz)
            w = max(nw, dw) + 10
            mid, gap = int(size * 0.30), max(6, int(size * 0.16))
            bar_y = ybase - mid
            found = self.locate(node[1], x + (w - nw) / 2, bar_y - gap - nd, fsz)
            if found is not None:
                return found
            return self.locate(node[2], x + (w - dw) / 2, bar_y + gap + da, fsz)
        if kind == "integral":
            _, lower, upper, body, var = node
            isz = int(size * 1.8)
            iw, _, _ = self._text_metrics("∫", isz, math=True)
            lim_size = max(10, int(size * 0.48))
            lw = self.measure(lower, lim_size)[0] if lower else 0
            uw = self.measure(upper, lim_size)[0] if upper else 0
            limit_x = int(iw * 0.62)
            left_w = max(iw, limit_x + lw, limit_x + uw) + 8
            if upper:
                found = self.locate(upper, x + limit_x,
                                    ybase - int(size * 0.95), lim_size)
                if found is not None:
                    return found
            if lower:
                found = self.locate(lower, x + limit_x,
                                    ybase + int(size * 0.85), lim_size)
                if found is not None:
                    return found
            return self.locate(body, x + left_w, ybase, size)
        if kind == "bigop":
            _, symbol, body, variable, lower, upper = node
            osz = int(size * 1.34)
            ow, _, _ = self._text_metrics(symbol, osz, math=True)
            lsz = max(9, int(size * 0.46))
            lower_node = ("row", [("text", f"{variable}="), lower])
            lw = self.measure(lower_node, lsz)[0]
            uw = self.measure(upper, lsz)[0]
            left = max(ow, lw, uw) + 8
            found = self.locate(upper, x + (left - uw) / 2,
                                ybase - int(size * 0.84), lsz)
            if found is not None:
                return found
            found = self.locate(lower_node, x + (left - lw) / 2,
                                ybase + int(size * 0.70), lsz)
            if found is not None:
                return found
            return self.locate(body, x + left, ybase, size)
        if kind == "derivative":
            _, variable, body, point = node
            fsize = max(14, int(size * 0.86))
            frac = ("frac", ("text", "d"), ("text", f"d{variable}"))
            fw = self.measure(frac, fsize)[0]
            found = self.locate(frac, x, ybase, fsize)
            if found is not None:
                return found
            found = self.locate(body, x + fw + 5, ybase, size)
            if found is not None or point is None:
                return found
            bw = self.measure(body, size)[0]
            psz = max(9, int(size * 0.48))
            point_node = ("row", [("text", f"{variable}="), point])
            _, pa, pd = self.measure(point_node, psz)
            return self.locate(point_node, x + fw + bw + 12, ybase + pa + pd, psz)
        return None


def _layout_geometry(canvas: tk.Canvas, tree, *, base_size: int, pad: int,
                     fg: str = "#17221a", caret: str | None = None,
                     fit_width: bool = True):
    cw = max(canvas.winfo_width(), 1)
    ch = max(canvas.winfo_height(), 1)
    size, min_size = base_size, 14
    while True:
        lay = _Layout(canvas, size, fg, caret)
        width, asc, desc = lay.measure(tree, size)
        fits_h = asc + desc <= max(1, ch - pad * 2)
        fits_w = width <= max(1, cw - pad * 2)
        if (fits_h and (fits_w or not fit_width)) or size <= min_size:
            break
        size -= 2
    ideal = (ch + asc - desc) / 2
    ybase = min(max(ideal, pad + asc), max(pad + asc, ch - pad - desc))
    return lay, size, width, asc, desc, ybase, cw, ch


def render(canvas: tk.Canvas, text: str, *, base_size: int = 26,
           pad: int = 12, fg: str = "#e2e8f0", placeholder: str = "0",
           caret: str | None = None, align: str = "right",
           fit_width: bool = True, scroll_x: float = 0.0,
           focus_caret: bool = False, show_overflow: bool = False) -> float:
    """Draw a natural-display expression and return its horizontal scroll.

    The fx-991CN X editor keeps a stable glyph size and scrolls the expression
    horizontally. Results, on the other hand, are right aligned and may shrink
    slightly to fit. ``fit_width=False`` selects the former behaviour.
    """
    canvas.delete("all")
    shown = text if text.strip() else placeholder
    shown = shown.replace("*", "×")
    tree = _parse(shown)
    lay, size, width, _asc, _desc, ybase, cw, ch = _layout_geometry(
        canvas, tree, base_size=base_size, pad=pad, fg=fg, caret=caret,
        fit_width=fit_width,
    )
    viewport = max(1.0, cw - pad * 2.0)
    max_scroll = max(0.0, width - viewport)

    if align == "left":
        scroll = max(0.0, min(float(scroll_x), max_scroll))
        if focus_caret:
            cursor = lay.locate(tree, 0.0, ybase, size)
            if cursor is not None:
                cursor_x = cursor[0]
                margin = max(9.0, size * 0.62)
                if cursor_x - scroll > viewport - margin:
                    scroll = min(max_scroll, cursor_x - viewport + margin)
                elif cursor_x - scroll < margin:
                    scroll = max(0.0, cursor_x - margin)
        x = pad - scroll
    elif align == "center":
        scroll = 0.0
        x = (cw - width) / 2.0
    else:
        scroll = 0.0
        x = cw - width - pad

    lay.draw(tree, x, ybase, size)
    if show_overflow and max_scroll > 0:
        cy = ch / 2.0
        if scroll > 0.5:
            canvas.create_polygon(pad - 1, cy, pad + 5, cy - 5, pad + 5, cy + 5,
                                  fill=fg, outline="")
        if scroll < max_scroll - 0.5:
            rx = cw - pad + 1
            canvas.create_polygon(rx, cy, rx - 6, cy - 5, rx - 6, cy + 5,
                                  fill=fg, outline="")
    return scroll


def _caret_positions(canvas: tk.Canvas, text: str, *, base_size: int, pad: int,
                     placeholder: str, align: str, scroll_x: float,
                     fit_width: bool) -> list[tuple[int, float, float]]:
    if not text:
        return [(0, float(pad), canvas.winfo_height() / 2.0)]
    base = (text if text.strip() else placeholder).replace("*", "×")
    tree = _parse(base)
    lay, size, width, _asc, _desc, ybase, cw, _ch = _layout_geometry(
        canvas, tree, base_size=base_size, pad=pad, fit_width=fit_width,
    )
    if align == "left":
        x0 = pad - max(0.0, float(scroll_x))
    elif align == "center":
        x0 = (cw - width) / 2.0
    else:
        x0 = cw - width - pad

    positions: list[tuple[int, float, float]] = []
    for index in range(len(text) + 1):
        marked = (text[:index] + CURSOR_MARK + text[index:]).replace("*", "×")
        located = lay.locate(_parse(marked), x0, ybase, size)
        if located is not None:
            positions.append((index, float(located[0]), float(located[1])))
    return positions


def caret_index_at(canvas: tk.Canvas, text: str, click_x: float, click_y: float | None = None, *,
                   base_size: int = 26, pad: int = 12, placeholder: str = "0",
                   align: str = "left", scroll_x: float = 0.0,
                   fit_width: bool = False) -> int:
    """Return the nearest two-dimensional insertion point to a mouse click."""
    if not text:
        return 0
    if len(text) > 160:
        ratio = max(0.0, min(1.0, click_x / max(1, canvas.winfo_width())))
        return round(ratio * len(text))
    positions = _caret_positions(
        canvas, text, base_size=base_size, pad=pad, placeholder=placeholder,
        align=align, scroll_x=scroll_x, fit_width=fit_width,
    )
    if not positions:
        return len(text)
    if click_y is None:
        return min(positions, key=lambda value: abs(value[1] - click_x))[0]
    # Vertical distance is weighted strongly enough to distinguish numerator,
    # denominator, exponent and integral limits at the same horizontal x.
    return min(positions, key=lambda value:
               (value[1] - click_x) ** 2 + (value[2] - click_y) ** 2 * 1.45)[0]


def move_caret_2d(canvas: tk.Canvas, text: str, index: int, direction: int, *,
                  base_size: int = 26, pad: int = 12, placeholder: str = "0",
                  align: str = "left", scroll_x: float = 0.0,
                  fit_width: bool = False) -> int:
    """Move the caret vertically between natural-display substructures.

    ``direction`` is negative for up and positive for down. If no fraction,
    exponent, root index, integral limit, sum/product limit or derivative point
    exists in that direction, the original index is returned.
    """
    positions = _caret_positions(
        canvas, text, base_size=base_size, pad=pad, placeholder=placeholder,
        align=align, scroll_x=scroll_x, fit_width=fit_width,
    )
    if not positions:
        return index
    current = min(positions, key=lambda value: abs(value[0] - index))
    _, cx, cy = current
    threshold = max(3.0, base_size * 0.12)
    if direction < 0:
        candidates = [value for value in positions if value[2] < cy - threshold]
    else:
        candidates = [value for value in positions if value[2] > cy + threshold]
    if not candidates:
        return index
    target = min(candidates, key=lambda value:
                 abs(value[1] - cx) * 1.8 + abs(value[2] - cy) * 0.35)
    return target[0]
