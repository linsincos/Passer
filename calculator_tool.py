from __future__ import annotations

import ast
import json
import math
import operator
import random
import re
import threading
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request

from clicker_tool import ClickerTheme
import math_render


# --- Fast numeric evaluator (used for the live preview) ------------------------

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _angle_to_radians(value: float, mode: str) -> float:
    if mode == "DEG":
        return math.radians(value)
    if mode == "GRA":
        return value * math.pi / 200.0
    return value


def _radians_to_angle(value: float, mode: str) -> float:
    if mode == "DEG":
        return math.degrees(value)
    if mode == "GRA":
        return value * 200.0 / math.pi
    return value


def _numeric_names(mode: str) -> dict:
    def trig(fn):
        return lambda a: fn(_angle_to_radians(a, mode))

    def inv(fn):
        return lambda a: _radians_to_angle(fn(a), mode)

    return {
        "pi": math.pi, "e": math.e, "tau": math.tau,
        "sin": trig(math.sin), "cos": trig(math.cos), "tan": trig(math.tan),
        "asin": inv(math.asin), "acos": inv(math.acos), "atan": inv(math.atan),
        "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
        "ln": math.log, "log": math.log10, "log2": math.log2,
        "sqrt": math.sqrt, "exp": math.exp, "abs": abs, "fact": math.factorial,
        "gcd": math.gcd, "round": round, "pow": pow, "min": min, "max": max,
    }


class _NumericEval(ast.NodeVisitor):
    def __init__(self, mode: str = "RAD"):
        self.names = _numeric_names(mode)

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_Constant(self, node):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("只支持数字表达式")

    def visit_BinOp(self, node):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError("不支持的运算符")
        return op(self.visit(node.left), self.visit(node.right))

    def visit_UnaryOp(self, node):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError("不支持的运算符")
        return op(self.visit(node.operand))

    def visit_Name(self, node):
        value = self.names.get(node.id)
        if isinstance(value, (int, float)):
            return value
        raise ValueError(f"未知名称：{node.id}")

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name) or node.func.id not in self.names:
            raise ValueError("不支持的函数")
        fn = self.names[node.func.id]
        if not callable(fn) or node.keywords:
            raise ValueError("不支持的调用")
        return fn(*[self.visit(arg) for arg in node.args])

    def generic_visit(self, node):
        raise ValueError("表达式包含不允许的内容")


def _expand_natural(text: str) -> str:
    """把 991 自然书写模板还原为函数调用。

    支持：定积分 ``∫_a^b(f)d[x]``、不定积分 ``∫f d[x]``、以 a 为底的对数
    ``log_a(b)``、求导 ``d/d[x](f)`` 及在某点求导 ``d/d[x](f)|x=p``。
    """
    # 定积分：∫_a^b(f)d[x] → integrate((f),(x,a,b))。须在不定积分之前处理。
    text = re.sub(r"∫_(.+?)\^(.+?)\((.+?)\)d\[([a-zA-Z])\]",
                  r"integrate((\3),(\4,\1,\2))", text)
    # 不定积分：∫f d[x] → integrate((f),x)（以 d[var] 作右界，无需括号配对）。
    text = re.sub(r"∫(.+?)d\[([a-zA-Z])\]", r"integrate((\1),\2)", text)
    # 以 a 为底的对数：log_a(b) → log((b),(a))。
    text = re.sub(r"log_(.+?)\((.+?)\)", r"log((\2),(\1))", text)
    # 求导：d/d[var](EXPR) 按括号深度提取，可选求值点 |var=point（取到行尾）。
    pat = re.compile(r"d/d\[([a-zA-Z])\]\(")
    out, i = [], 0
    while i < len(text):
        m = pat.match(text, i)
        if not m:
            out.append(text[i]); i += 1
            continue
        var, j, depth = m.group(1), m.end(), 1
        start = j
        while j < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[j], 0)
            j += 1
        inner = text[start:j - 1] if depth == 0 else text[start:j]
        if text[j:j + 1] == "|":  # 在某点求导
            point = re.sub(rf"^{re.escape(var)}", "", text[j + 1:]).lstrip("=").strip()
            out.append(f"diff(({inner}),{var}).subs({var},({point}))")
            i = len(text)
        else:
            out.append(f"diff(({inner}),{var})")
            i = j
    return "".join(out)


def _desugar(text: str) -> str:
    """把计算器显示的数学符号还原为可计算的表达式。

    显示层使用 √、sin⁻¹、|x|、π、²、∫□d[x] 等 991 风格记号，求值前在此翻译回
    sqrt(/asin(/abs(/pi/**2/integrate(... 等，使数值与符号引擎都能解析。
    """
    text = _expand_natural(text)
    text = (
        text.replace("sin⁻¹", "asin").replace("cos⁻¹", "acos").replace("tan⁻¹", "atan")
        .replace("×", "*").replace("÷", "/").replace("−", "-")
        .replace("√", "sqrt").replace("π", "pi").replace("∞", "oo")
        .replace("²", "**2").replace("³", "**3")
    )
    # |x| → abs(x)，支持多组（非嵌套）。
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\|([^|]+)\|", r"abs(\1)", text)
    return text.replace("^", "**").strip()


def _preprocess(text: str) -> str:
    return _desugar(text)


def safe_eval_expression(text: str, mode: str = "RAD") -> float:
    cleaned = _preprocess(text)
    if not cleaned:
        raise ValueError("请输入表达式")
    return _NumericEval(mode).visit(ast.parse(cleaned, mode="eval"))


def _format_number(value) -> str:
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e16:
            return str(int(value))
        return f"{value:.10g}"
    return str(value)


# --- Symbolic engine (sympy, lazy-imported) -----------------------------------

_sympy_cache: dict = {}


def _load_sympy():
    """Import sympy on first use; cache the module + parser transforms."""
    if "sp" in _sympy_cache:
        return _sympy_cache["sp"], _sympy_cache.get("transforms")
    try:
        import sympy as sp
        from sympy.parsing.sympy_parser import (
            parse_expr, standard_transformations,
            implicit_multiplication_application, convert_xor, factorial_notation,
        )
        transforms = standard_transformations + (
            convert_xor, implicit_multiplication_application, factorial_notation,
        )
        _sympy_cache["sp"] = sp
        _sympy_cache["parse_expr"] = parse_expr
        _sympy_cache["transforms"] = transforms
    except Exception:
        _sympy_cache["sp"] = None
        _sympy_cache["transforms"] = None
    return _sympy_cache["sp"], _sympy_cache.get("transforms")


def _stat_list(args):
    """Flatten call args (numbers or a single list) into a plain list."""
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        return list(args[0])
    return list(args)


def _sympy_names(sp, mode: str, variables: dict | None = None) -> dict:
    x, y, z, t, n = sp.symbols("x y z t n")

    def _mean(*a):
        data = _stat_list(a)
        return sp.Add(*data) / len(data)

    def _median(*a):
        data = sorted(_stat_list(a), key=lambda v: float(v))
        m = len(data)
        if m % 2:
            return data[m // 2]
        return (data[m // 2 - 1] + data[m // 2]) / 2

    def _variance(*a, sample=True):
        data = _stat_list(a)
        mu = sp.Add(*data) / len(data)
        ss = sp.Add(*[(v - mu) ** 2 for v in data])
        return ss / (len(data) - 1 if sample and len(data) > 1 else len(data))

    def _mode_val(*a):
        data = _stat_list(a)
        counts: dict = {}
        for v in data:
            counts[v] = counts.get(v, 0) + 1
        return max(counts, key=counts.get)

    def _normal_pdf(value, mu=0, sigma=1):
        return sp.exp(-((value - mu) / sigma) ** 2 / 2) / (sp.sqrt(2 * sp.pi) * sigma)

    def _normal_cdf(value, mu=0, sigma=1):
        return (1 + sp.erf((value - mu) / (sigma * sp.sqrt(2)))) / 2

    def _inv_normal(probability, mu=0, sigma=1):
        return mu + sigma * sp.sqrt(2) * sp.erfinv(2 * probability - 1)

    def _binom_pdf(k, count, probability):
        return sp.binomial(count, k) * probability ** k * (1 - probability) ** (count - k)

    def _binom_cdf(k, count, probability):
        return sp.Add(*[_binom_pdf(i, count, probability) for i in range(int(k) + 1)])

    def _poisson_pdf(k, mean_value):
        return sp.exp(-mean_value) * mean_value ** k / sp.factorial(k)

    def _poisson_cdf(k, mean_value):
        return sp.Add(*[_poisson_pdf(i, mean_value) for i in range(int(k) + 1)])

    k = sp.pi / 180 if mode == "DEG" else (sp.pi / 200 if mode == "GRA" else sp.Integer(1))

    def _polar(x_value, y_value):
        return (sp.sqrt(x_value ** 2 + y_value ** 2), sp.atan2(y_value, x_value) / k)

    def _rect(radius, angle):
        return (radius * sp.cos(angle * k), radius * sp.sin(angle * k))

    def _ratio_left(a, b, c):
        """A:B = X:C."""
        return sp.simplify(a * c / b)

    def _ratio_right(a, b, c):
        """A:B = C:X."""
        return sp.simplify(b * c / a)

    names = {
        "x": x, "y": y, "z": z, "t": t, "n": n,
        "pi": sp.pi, "e": sp.E, "E": sp.E, "I": sp.I, "i": sp.I, "oo": sp.oo,
        "sqrt": sp.sqrt, "exp": sp.exp, "ln": sp.log,
        "log": lambda a, b=10: sp.log(a, b), "log2": lambda a: sp.log(a, 2),
        "abs": sp.Abs, "Abs": sp.Abs, "factorial": sp.factorial,
        "gcd": sp.gcd, "lcm": sp.lcm, "binomial": sp.binomial, "nCr": sp.binomial,
        "nPr": lambda a, r: sp.factorial(a) / sp.factorial(a - r),
        "diff": sp.diff, "integrate": sp.integrate, "solve": sp.solve,
        "linsolve": sp.linsolve, "roots": lambda e, *s: list(sp.roots(e, *s).keys()),
        "simplify": sp.simplify, "expand": sp.expand, "factor": sp.factor,
        "apart": sp.apart, "together": sp.together, "Eq": sp.Eq,
        "summation": sp.summation, "Sum": sp.Sum, "product": sp.product,
        "asinh": sp.asinh, "acosh": sp.acosh, "atanh": sp.atanh,
        "floor": sp.floor, "ceil": sp.ceiling, "ceiling": sp.ceiling,
        "mod": sp.Mod, "sign": sp.sign,
        # --- 复数 CMPLX ---
        "conj": sp.conjugate, "conjugate": sp.conjugate,
        "re": sp.re, "im": sp.im, "arg": sp.arg,
        # --- 矩阵 / 向量 MAT / VEC ---
        "Matrix": sp.Matrix, "matrix": sp.Matrix,
        "det": lambda m: sp.Matrix(m).det(), "inv": lambda m: sp.Matrix(m).inv(),
        "transpose": lambda m: sp.Matrix(m).T, "rank": lambda m: sp.Matrix(m).rank(),
        "eye": sp.eye, "zeros": sp.zeros, "ones": sp.ones,
        "dot": lambda a, b: sp.Matrix(a).dot(sp.Matrix(b)),
        "cross": lambda a, b: sp.Matrix(a).cross(sp.Matrix(b)),
        "norm": lambda a: sp.Matrix(a).norm(),
        # --- 统计 STAT ---
        "mean": _mean, "median": _median, "mode": _mode_val,
        "var": lambda *a: _variance(*a, sample=True),
        "pvar": lambda *a: _variance(*a, sample=False),
        "std": lambda *a: sp.sqrt(_variance(*a, sample=True)),
        "pstd": lambda *a: sp.sqrt(_variance(*a, sample=False)),
        "total": lambda *a: sp.Add(*_stat_list(a)),
        # --- 分布 DIST / Math Box ---
        "normalpdf": _normal_pdf, "normalcdf": _normal_cdf, "invnorm": _inv_normal,
        "binompdf": _binom_pdf, "binomcdf": _binom_cdf,
        "poissonpdf": _poisson_pdf, "poissoncdf": _poisson_cdf,
        "rand": lambda: sp.Float(random.random(), 15),
        "randint": lambda a, b: sp.Integer(random.randint(int(a), int(b))),
        "Pol": _polar, "polar": _polar, "Rec": _rect, "rect": _rect,
        "ineq": lambda expression, variable=x: sp.solve_univariate_inequality(expression, variable),
        "ratioL": _ratio_left, "ratioR": _ratio_right,
    }
    if variables:
        for key, value in variables.items():
            if re.fullmatch(r"[A-FM]", str(key)):
                try:
                    names[str(key)] = sp.sympify(value)
                except Exception:
                    names[str(key)] = sp.Integer(0)
    if k == 1:
        names.update(sin=sp.sin, cos=sp.cos, tan=sp.tan,
                     asin=sp.asin, acos=sp.acos, atan=sp.atan,
                     sinh=sp.sinh, cosh=sp.cosh, tanh=sp.tanh)
    else:
        names.update(
            sin=lambda a: sp.sin(a * k), cos=lambda a: sp.cos(a * k), tan=lambda a: sp.tan(a * k),
            asin=lambda a: sp.asin(a) / k, acos=lambda a: sp.acos(a) / k, atan=lambda a: sp.atan(a) / k,
            sinh=sp.sinh, cosh=sp.cosh, tanh=sp.tanh,
        )
    return names


def _pretty(text: str) -> str:
    """Turn sympy output into 991-style math notation."""
    text = text.replace("**", "^")
    text = re.sub(r"Abs\(([^()]*)\)", r"|\1|", text)
    text = re.sub(r"\blog\(", "ln(", text)
    text = re.sub(r"\basin\(", "sin⁻¹(", text)
    text = re.sub(r"\bacos\(", "cos⁻¹(", text)
    text = re.sub(r"\batan\(", "tan⁻¹(", text)
    text = re.sub(r"\bsqrt\(", "√(", text)
    text = re.sub(r"\bpi\b", "π", text)
    text = re.sub(r"\boo\b", "∞", text)
    text = re.sub(r"\bE\b", "e", text)
    text = re.sub(r"\bI\b", "i", text)
    return text


def _format_symbolic_number(sp, expr) -> str:
    """Render an exact sympy number with a decimal approximation when useful."""
    expr = sp.expand(expr)
    if expr.is_real is False:  # complex result → a + b i
        re_p = sp.simplify(sp.re(expr))
        im_p = sp.simplify(sp.im(expr))
        tok = lambda v: _pretty(sp.sstr(v))
        mag = sp.Abs(im_p)
        mag_str = "" if mag == 1 else tok(mag)
        if re_p == 0:
            body = ("-" if im_p.is_negative else "") + (mag_str or "") + "i"
        else:
            sign = "-" if im_p.is_negative else "+"
            body = f"{tok(re_p)} {sign} {mag_str}i"
        try:
            cval = complex(sp.N(expr, 18))
            return f"{body} ≈ {cval.real:.10g}{'+' if cval.imag >= 0 else '-'}{abs(cval.imag):.10g}i"
        except Exception:
            return body
    try:
        f = float(sp.N(expr, 18))
        approx = f"{f:.12g}"
    except Exception:
        return _pretty(sp.sstr(expr))
    if expr.is_Integer:
        return str(expr)
    if expr.is_Float:
        return approx
    return f"{_pretty(sp.sstr(expr))} ≈ {approx}"


def symbolic_calculate(text: str, mode: str = "RAD", variables: dict | None = None) -> str:
    """Evaluate with sympy; supports diff/integrate/solve and exact arithmetic."""
    sp, transforms = _load_sympy()
    if sp is None:
        raise RuntimeError("当前环境缺少 sympy，无法进行符号计算。")
    cleaned = _desugar(text)
    if not cleaned:
        raise ValueError("请输入表达式")
    expr = _sympy_cache["parse_expr"](
        cleaned, transformations=transforms, local_dict=_sympy_names(sp, mode, variables),
    )
    if isinstance(expr, (list, tuple)):
        if not expr:
            return "无解"
        return "解：" + ", ".join(_pretty(sp.sstr(item)) for item in expr)
    if isinstance(expr, dict):
        return "解：" + ", ".join(f"{k}={_pretty(sp.sstr(v))}" for k, v in expr.items())
    if getattr(expr, "free_symbols", set()):
        return _pretty(sp.sstr(expr))
    return _format_symbolic_number(sp, expr)


# --- Base-N (进制) -------------------------------------------------------------

_BASE_RADIX = {"DEC": 10, "HEX": 16, "BIN": 2, "OCT": 8}
_BASE_DIGITS = {"DEC": "0-9", "HEX": "0-9A-Fa-f", "BIN": "0-1", "OCT": "0-7"}


def base_calculate(text: str, base_in: str = "DEC") -> str:
    """Evaluate an integer/bitwise expression in the given base, show all bases."""
    radix = _BASE_RADIX.get(base_in, 10)
    s = text.strip()
    if not s:
        raise ValueError("请输入表达式")
    s = re.sub(r"\bnot\b", "~", s, flags=re.I)
    s = re.sub(r"\band\b", "&", s, flags=re.I)
    s = re.sub(r"\bxor\b", "^", s, flags=re.I)
    s = re.sub(r"\bor\b", "|", s, flags=re.I)
    s = s.replace("×", "*").replace("÷", "//").replace("−", "-").replace("/", "//")
    digits = _BASE_DIGITS.get(base_in, "0-9")

    def _convert(match: re.Match) -> str:
        return str(int(match.group(0), radix))

    s = re.sub(rf"[{digits}]+", _convert, s)
    if not re.fullmatch(r"[0-9+\-*/&|^~<>() ]+", s):
        raise ValueError("进制模式只支持整数与位运算")
    try:
        value = int(eval(s, {"__builtins__": {}}, {}))  # noqa: S307 - sanitized above
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"无法计算：{exc}") from exc
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    return (
        f"DEC {sign}{magnitude}\n"
        f"HEX {sign}{format(magnitude, 'X')}\n"
        f"OCT {sign}{format(magnitude, 'o')}\n"
        f"BIN {sign}{format(magnitude, 'b')}"
    )


# --- Function table (函数表) ---------------------------------------------------

def _split_top_level(text: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return [p.strip() for p in parts]


def table_calculate(text: str, mode: str = "RAD") -> str:
    """`table(f(x), start, end, step)` → a value table for x over the range."""
    sp, transforms = _load_sympy()
    if sp is None:
        raise RuntimeError("当前环境缺少 sympy，无法生成函数表。")
    match = re.match(r"\s*table\((.*)\)\s*$", text, re.S)
    if not match:
        raise ValueError("请使用 table(f(x), 起点, 终点, 步长)")
    args = _split_top_level(match.group(1))
    if len(args) < 3:
        raise ValueError("请使用 table(f(x), 起点, 终点, 步长)")
    names = _sympy_names(sp, mode)
    prep = lambda s: s.replace("×", "*").replace("÷", "/").replace("−", "-").replace("√", "sqrt").replace("π", "pi")
    fexpr = _sympy_cache["parse_expr"](prep(args[0]), transformations=transforms, local_dict=names)
    a = float(sp.N(_sympy_cache["parse_expr"](prep(args[1]), transformations=transforms, local_dict=names)))
    b = float(sp.N(_sympy_cache["parse_expr"](prep(args[2]), transformations=transforms, local_dict=names)))
    step = float(sp.N(_sympy_cache["parse_expr"](prep(args[3]), transformations=transforms, local_dict=names))) if len(args) > 3 else 1.0
    if step == 0:
        raise ValueError("步长不能为 0")
    x = sp.Symbol("x")
    rows, value, count = [], a, 0
    while (value <= b + 1e-9 if step > 0 else value >= b - 1e-9) and count < 200:
        try:
            fv = float(sp.N(fexpr.subs(x, value)))
            rows.append(f"x={value:.6g}\tf={fv:.6g}")
        except Exception:
            rows.append(f"x={value:.6g}\tf=—")
        value += step
        count += 1
    return "\n".join(rows) if rows else "无数据"


# --- Currency conversion (货币换算) -------------------------------------------

_CURRENCY_FALLBACK_USD = {
    # Built-in reference rates are used only when live lookup is unavailable.
    "USD": 1.0,
    "CNY": 7.25,
    "EUR": 0.92,
    "GBP": 0.79,
    "JPY": 157.0,
    "HKD": 7.80,
    "TWD": 32.3,
    "KRW": 1380.0,
    "AUD": 1.52,
    "CAD": 1.37,
    "CHF": 0.89,
    "SGD": 1.35,
}
_CURRENCY_ALIASES = {
    "人民币": "CNY", "元": "CNY", "美元": "USD", "美金": "USD",
    "欧元": "EUR", "英镑": "GBP", "日元": "JPY", "港币": "HKD",
    "台币": "TWD", "韩元": "KRW", "澳元": "AUD", "加元": "CAD",
    "瑞郎": "CHF", "新币": "SGD",
}
_CURRENCY_CACHE: dict[tuple[str, str, str], tuple[float, str]] = {}


def _currency_code(text: str) -> str:
    code = text.strip().strip("'\"").upper()
    return _CURRENCY_ALIASES.get(code, code)


def _fetch_frankfurter_rate(src: str, dst: str) -> tuple[float, str]:
    params = urllib.parse.urlencode({"from": src, "to": dst})
    url = f"https://api.frankfurter.app/latest?{params}"
    with urllib.request.urlopen(url, timeout=6) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rate = float(payload["rates"][dst])
    date = str(payload.get("date") or "latest")
    return rate, f"联网汇率 Frankfurter {date}"


def _fetch_open_er_rate(src: str, dst: str) -> tuple[float, str]:
    url = f"https://open.er-api.com/v6/latest/{urllib.parse.quote(src)}"
    with urllib.request.urlopen(url, timeout=6) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if str(payload.get("result", "")).lower() not in ("success", ""):
        raise ValueError(payload.get("error-type") or "online rate request failed")
    rate = float(payload["rates"][dst])
    date = str(payload.get("time_last_update_utc") or payload.get("time_last_update_unix") or "latest")
    return rate, f"联网汇率 OpenER {date}"


def _fetch_online_currency_rate(src: str, dst: str, refresh: bool = False) -> tuple[float, str]:
    key = ("online", src, dst)
    if not refresh and key in _CURRENCY_CACHE:
        return _CURRENCY_CACHE[key]
    errors = []
    for fetcher in (_fetch_frankfurter_rate, _fetch_open_er_rate):
        try:
            result = fetcher(src, dst)
            _CURRENCY_CACHE[key] = result
            return result
        except Exception as exc:  # noqa: BLE001 - try the next public endpoint.
            errors.append(str(exc))
    raise ValueError("联网汇率获取失败：" + "；".join(errors[-2:]))


def _fetch_currency_rate(src: str, dst: str, *, online_required: bool = False,
                         refresh: bool = False) -> tuple[float, str]:
    key = ("auto", src, dst)
    if online_required:
        return _fetch_online_currency_rate(src, dst, refresh=refresh)
    if key in _CURRENCY_CACHE:
        return _CURRENCY_CACHE[key]
    try:
        result = _fetch_online_currency_rate(src, dst, refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - offline fallback keeps the calculator useful.
        if src not in _CURRENCY_FALLBACK_USD or dst not in _CURRENCY_FALLBACK_USD:
            raise ValueError(f"暂不支持币种：{src} 或 {dst}")
        rate = _CURRENCY_FALLBACK_USD[dst] / _CURRENCY_FALLBACK_USD[src]
        result = (rate, f"内置参考汇率（联网失败：{exc}）")
    _CURRENCY_CACHE[key] = result
    return result


def currency_calculate(text: str, *, online_required: bool = False,
                       refresh: bool = False) -> str:
    """Convert currency: fx(100, USD, CNY), netfx(100, USD, CNY), or '100 USD CNY'."""
    raw = text.strip()
    if not raw:
        raise ValueError("请输入货币换算，例如 fx(100, USD, CNY)")
    match = re.match(r"\s*(netfx|onlinefx|联网|fx|currency|货币)\((.*)\)\s*$", raw, re.I | re.S)
    if match:
        fn = match.group(1).lower()
        online_required = online_required or fn in ("netfx", "onlinefx", "联网")
        args = _split_top_level(match.group(2))
        if len(args) != 3:
            raise ValueError("请使用 fx(金额, 源币种, 目标币种)")
        amount_text, src_text, dst_text = args
    else:
        parts = re.sub(r"\bto\b", " ", raw.replace("→", " "), flags=re.I).split()
        if len(parts) == 2:
            amount_text, src_text, dst_text = "1", parts[0], parts[1]
        elif len(parts) == 3:
            amount_text, src_text, dst_text = parts
        else:
            raise ValueError("请使用 fx(100, USD, CNY) 或 100 USD CNY")
    amount = safe_eval_expression(amount_text, "RAD")
    src = _currency_code(src_text)
    dst = _currency_code(dst_text)
    if src == dst:
        return f"{_format_number(amount)} {src} = {_format_number(amount)} {dst}"
    rate, source = _fetch_currency_rate(src, dst, online_required=online_required, refresh=refresh)
    value = amount * rate
    return (
        f"{_format_number(amount)} {src} = {_format_number(value)} {dst}\n"
        f"1 {src} = {_format_number(rate)} {dst}\n"
        f"{source}"
    )


# --- Calculator window ---------------------------------------------------------

class _CanvasExpressionEditor:
    """Entry-like adapter that lets the 2D math canvas be the only input surface."""

    def __init__(self, owner, canvas: tk.Canvas):
        self.owner = owner
        self.canvas = canvas

    def focus_set(self) -> None:
        self.canvas.focus_set()
        self.owner._wake_caret()

    def selection_present(self) -> bool:
        sel = self.owner._selection
        return bool(sel and sel[0] < sel[1])

    def _resolve_index(self, index) -> int:
        text_len = len(self.owner.expression_var.get())
        if index in (tk.END, "end"):
            return text_len
        if index in (tk.INSERT, "insert"):
            return self.owner._cursor
        if index == "sel.first":
            return self.owner._selection[0] if self.selection_present() else self.owner._cursor
        if index == "sel.last":
            return self.owner._selection[1] if self.selection_present() else self.owner._cursor
        return max(0, min(text_len, int(index)))

    def index(self, index) -> int:
        return self._resolve_index(index)

    def icursor(self, index) -> None:
        self.owner._cursor = self._resolve_index(index)
        self.owner._wake_caret()

    def selection_range(self, start, end) -> None:
        a = self._resolve_index(start)
        b = self._resolve_index(end)
        if a == b:
            self.owner._selection = None
        else:
            self.owner._selection = (min(a, b), max(a, b))
        self.owner._wake_caret()

    def delete(self, start, end=None) -> None:
        text = self.owner.expression_var.get()
        a = self._resolve_index(start)
        b = self._resolve_index(end) if end is not None else min(len(text), a + 1)
        if a > b:
            a, b = b, a
        self.owner._cursor = a
        self.owner._selection = None
        self.owner._caret_on = True
        self.owner.expression_var.set(text[:a] + text[b:])
        self.owner._wake_caret()

    def insert(self, index, text: str) -> None:
        s = self.owner.expression_var.get()
        i = self._resolve_index(index)
        self.owner._cursor = i + len(text)
        self.owner._selection = None
        self.owner._caret_on = True
        self.owner.expression_var.set(s[:i] + text + s[i:])
        self.owner._wake_caret()


class CalculatorWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    # fx-991CN X / ClassWiz 窄长机身比例。
    MIN_W = 520
    MIN_H = 920
    COLUMNS = 6
    DISPLAY_H = 118
    DISPLAY_BASE_SIZE = 28

    # 统一的单色 LCD 调色板——状态栏 / 表达式 / 结果 / 屏内菜单全部用同一套颜色，
    # 与真机单色点阵屏一致（不再用黄/红等彩色，所有呈现一模一样）。
    LCD_BG = "#b9c4ad"          # 点阵屏底色
    LCD_FG = "#17221a"          # 点阵“墨色”——所有文字/图标统一用它
    LCD_FRAME = "#0b0d12"       # 屏幕外框
    LCD_HEADER = "#9fab96"      # 菜单标题条底色（同色系深一档）
    LCD_SELECT = "#2f3b28"      # 菜单高亮底色（反白）
    LCD_SELECT_FG = "#cdd8c2"   # 菜单高亮文字（反白后的浅墨）

    ANGLE_MODES = ("DEG", "RAD", "GRA")
    # 卡西欧 fx-991 风格模式：(代号, 中文名)
    MODES = (
        ("COMP", "计算"), ("CMPLX", "复数"), ("BASE", "进制"), ("MAT", "矩阵"),
        ("VEC", "向量"), ("STAT", "统计"), ("EQN", "方程"), ("TABLE", "函数表"),
        ("DIST", "分布"), ("INEQ", "不等式"), ("RATIO", "比例"), ("CURR", "货币"),
    )

    # 通用数字/运算键盘，所有模式共用（8 列）。
    NUMERIC_ROWS = [
        [("7", "num", "7"), ("8", "num", "8"), ("9", "num", "9"), ("(", "ins", "("),
         (")", "ins", ")"), ("÷", "op", "/"), ("x²", "ins", "²"), ("√", "tmplsel", "√(□)")],
        [("4", "num", "4"), ("5", "num", "5"), ("6", "num", "6"), ("x", "ins", "x"),
         ("xⁿ", "ins", "^"), ("×", "op", "*"), ("x!", "ins", "!"), ("|x|", "tmplsel", "|□|")],
        [("1", "num", "1"), ("2", "num", "2"), ("3", "num", "3"), ("π", "ins", "π"),
         ("e", "ins", "e"), ("−", "op", "-"), (",", "ins", ","), ("⌫", "back", None)],
        [("0", "num", "0"), (".", "num", "."), ("(-)", "ins", "-"), ("EXP", "ins", "*10^"),
         ("Ans", "ans", None), ("+", "op", "+"), ("AC", "clear", None), ("=", "equals", None)],
    ]

    # 每个模式上方专属的功能键（8 列）。
    FUNCTION_ROWS = {
        "COMP": [
            # 自然书写模板（占位框 □，按 Tab 跳到下一个框）：分数/根号/对数/定积分/求导。
            [("▢/▢", "tmplsel", "□/□"), ("ⁿ√▢", "tmplsel", "(□)^(1/(□))"),
             ("∛▢", "tmplsel", "(□)^(1/3)"), ("logₐb", "tmplsel", "log_□(□)"),
             ("∫ₐᵇ", "tmplsel", "∫_□^□(□)d[x]"), ("d/dx|ₐ", "tmplsel", "d/d[x](□)|x=□"),
             ("√▢", "tmplsel", "√(□)"), ("|▢|", "tmplsel", "|□|")],
            [("sin", "ins", "sin("), ("cos", "ins", "cos("), ("tan", "ins", "tan("),
             ("sin⁻¹", "ins", "sin⁻¹("), ("cos⁻¹", "ins", "cos⁻¹("), ("tan⁻¹", "ins", "tan⁻¹("),
             ("d/dx", "tmplsel", "d/d[x](□)"), ("∫dx", "tmplsel", "∫□d[x]")],
            [("ln", "ins", "ln("), ("log", "ins", "log("), ("log₂", "ins", "log2("),
             ("eˣ", "ins", "e^("), ("sinh", "ins", "sinh("), ("cosh", "ins", "cosh("),
             ("tanh", "ins", "tanh("), ("解方程", "diffwrap", "solve")],
            [("nPr", "ins", "nPr("), ("nCr", "ins", "nCr("), ("gcd", "ins", "gcd("),
             ("lcm", "ins", "lcm("), ("mod", "ins", "mod("), ("floor", "ins", "floor("),
             ("ceil", "ins", "ceil("), ("abs", "ins", "abs(")],
            [("Ran#", "tmpl", "rand()"), ("RanInt", "tmplsel", "randint(□,□)"),
             ("求和 Σ", "tmplsel", "summation(□,(n,□,□))"),
             ("求积 Π", "tmplsel", "product(□,(n,□,□))"),
             ("化简", "fnwrap", "simplify"), ("展开", "fnwrap", "expand"),
             ("因式", "fnwrap", "factor"), ("部分分式", "fnwrap", "apart")],
        ],
        "CMPLX": [
            [("i", "ins", "i"), ("conj", "ins", "conj("), ("re", "ins", "re("),
             ("im", "ins", "im("), ("arg", "ins", "arg("), ("|z|", "ins", "|"),
             ("sin", "ins", "sin("), ("cos", "ins", "cos(")],
            [("ln", "ins", "ln("), ("eˣ", "ins", "e^("), ("√", "ins", "√("),
             ("化简", "fnwrap", "simplify"), ("展开", "fnwrap", "expand"),
             ("π", "ins", "π"), ("(", "ins", "("), (")", "ins", ")")],
        ],
        "BASE": [
            [("DEC", "base", "DEC"), ("HEX", "base", "HEX"), ("BIN", "base", "BIN"),
             ("OCT", "base", "OCT"), ("and", "ins", " and "), ("or", "ins", " or "),
             ("xor", "ins", " xor "), ("not", "ins", "not ")],
            [("A", "ins", "A"), ("B", "ins", "B"), ("C", "ins", "C"), ("D", "ins", "D"),
             ("E", "ins", "E"), ("F", "ins", "F"), ("<<", "ins", "<<"), (">>", "ins", ">>")],
        ],
        "MAT": [
            [("2×2", "tmpl", "Matrix([[1,2],[3,4]])"), ("3×3", "tmpl", "Matrix([[1,2,3],[4,5,6],[7,8,10]])"),
             ("det", "fnwrap", "det"), ("inv", "fnwrap", "inv"), ("Tᵀ", "fnwrap", "transpose"),
             ("rank", "fnwrap", "rank"), ("[", "ins", "["), ("]", "ins", "]")],
            [("新行 ],[", "ins", "],["), ("eye", "ins", "eye("), ("×", "op", "*"),
             ("+", "op", "+"), ("−", "op", "-"), (",", "ins", ","), ("(", "ins", "("), (")", "ins", ")")],
        ],
        "VEC": [
            [("向量", "tmpl", "Matrix([1,2,3])"), ("dot", "ins", "dot("), ("cross", "ins", "cross("),
             ("norm", "fnwrap", "norm"), ("×", "op", "*"), ("+", "op", "+"),
             ("−", "op", "-"), (",", "ins", ",")],
        ],
        "STAT": [
            [("mean", "ins", "mean("), ("median", "ins", "median("), ("mode", "ins", "mode("),
             ("var", "ins", "var("), ("std", "ins", "std("), ("总和", "ins", "total("),
             ("(", "ins", "("), (")", "ins", ")")],
        ],
        "EQN": [
            [("解方程", "diffwrap", "solve"), ("方程组", "tmpl", "solve([x+y-3, x-y-1], [x,y])"),
             ("因式", "fnwrap", "factor"), ("展开", "fnwrap", "expand"), ("化简", "fnwrap", "simplify"),
             ("roots", "fnwrap", "roots"), ("apart", "fnwrap", "apart"), ("together", "fnwrap", "together")],
        ],
        "TABLE": [
            [("f(x)表", "tablewrap", None), ("x", "ins", "x"), ("xⁿ", "ins", "^"),
             ("√", "ins", "√("), ("sin", "ins", "sin("), ("cos", "ins", "cos("),
             ("ln", "ins", "ln("), ("eˣ", "ins", "e^(")],
        ],
        "DIST": [
            [("Normal PDF", "tmplsel", "normalpdf(□,0,1)"),
             ("Normal CDF", "tmplsel", "normalcdf(□,0,1)"),
             ("Inv Normal", "tmplsel", "invnorm(□,0,1)"),
             ("Binomial PD", "tmplsel", "binompdf(□,□,□)"),
             ("Binomial CD", "tmplsel", "binomcdf(□,□,□)"),
             ("Poisson PD", "tmplsel", "poissonpdf(□,□)"),
             ("Poisson CD", "tmplsel", "poissoncdf(□,□)"),
             ("Ran#", "tmpl", "rand()")],
        ],
        "INEQ": [
            [("一次不等式", "tmplsel", "ineq(□,x)"),
             ("二次不等式", "tmplsel", "ineq(□*x^2+□*x+□,x)"),
             ("<", "ins", "<"), (">", "ins", ">"), ("≤", "ins", "<="), ("≥", "ins", ">="),
             ("x", "ins", "x"), ("解", "fnwrap", "ineq")],
        ],
        "RATIO": [
            [("A:B=X:C", "tmplsel", "ratioL(□,□,□)"),
             ("A:B=C:X", "tmplsel", "ratioR(□,□,□)"),
             (":", "ins", ","), ("x", "ins", "x"), ("%", "tmplsel", "(□)/100"),
             ("分数", "tmplsel", "□/□"), ("S⇔D", "ins", ""), ("Ans", "ans", None)],
        ],
        "CURR": [
            [("USD→CNY", "currency", ("USD", "CNY")), ("CNY→USD", "currency", ("CNY", "USD")),
             ("EUR→CNY", "currency", ("EUR", "CNY")), ("JPY→CNY", "currency", ("JPY", "CNY")),
             ("HKD→CNY", "currency", ("HKD", "CNY")), ("GBP→CNY", "currency", ("GBP", "CNY")),
             ("AUD→CNY", "currency", ("AUD", "CNY")), ("CAD→CNY", "currency", ("CAD", "CNY"))],
            [("联网USD→CNY", "onlinecurrency", ("USD", "CNY")), ("联网CNY→USD", "onlinecurrency", ("CNY", "USD")),
             ("联网EUR→CNY", "onlinecurrency", ("EUR", "CNY")), ("联网JPY→CNY", "onlinecurrency", ("JPY", "CNY")),
             ("联网HKD→CNY", "onlinecurrency", ("HKD", "CNY")), ("联网GBP→CNY", "onlinecurrency", ("GBP", "CNY")),
             ("联网AUD→CNY", "onlinecurrency", ("AUD", "CNY")), ("联网CAD→CNY", "onlinecurrency", ("CAD", "CNY"))],
            [("USD", "ins", "USD"), ("CNY", "ins", "CNY"), ("EUR", "ins", "EUR"),
             ("JPY", "ins", "JPY"), ("HKD", "ins", "HKD"), ("GBP", "ins", "GBP"),
             ("TWD", "ins", "TWD"), ("KRW", "ins", "KRW")],
            [("AUD", "ins", "AUD"), ("CAD", "ins", "CAD"), ("CHF", "ins", "CHF"),
             ("SGD", "ins", "SGD"), ("→", "ins", " "), ("fx", "tmplsel", "fx(□, USD, CNY)"),
             ("netfx", "tmplsel", "netfx(□, USD, CNY)"), ("联网换算", "onlineconvert", None)],
        ],
    }

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self.resize_start = None
        self.angle_mode = "RAD"
        self.mode = "COMP"
        self.base_in = "DEC"
        self.ans_text = "0"
        self.memories = {key: "0" for key in "ABCDEFM"}
        self.memory_key = "A"
        self.history: list[dict] = []
        self._history_index = 0
        self._last_result_raw = "0"
        self._decimal_view = False
        self.shift_active = False
        self.alpha_active = False
        self._eval_seq = 0
        self._pending_result = None
        self._mode_buttons: dict = {}
        self._cursor = 0
        self._selection = None
        self._caret_on = True          # 闪动光标当前是否可见
        self._blink_after = None
        self.expression_var = tk.StringVar(value="")
        self.result_var = tk.StringVar(value="0")

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=theme.border)
        self.window.minsize(self.MIN_W, self.MIN_H)
        self.shell = tk.Frame(self.window, bg=theme.app_bg, highlightthickness=1, highlightbackground=theme.border)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        self._build_chrome()
        self._build_body()
        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        x, y = theme.center_over_root(app.root, self.MIN_W, self.MIN_H)
        theme.place_toplevel_absolute(self.window, self.MIN_W, self.MIN_H, x, y)
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()
        self.display.focus_set()
        self._blink_caret()

    CARET_BLINK_MS = 530

    def _blink_caret(self) -> None:
        """让 2D 显示区的黑色编辑光标周期性闪动，贴近真机手感。"""
        if self.closed:
            return
        self._caret_on = not self._caret_on
        if not self._selection:
            self._render_math()
        self._blink_after = self.window.after(self.CARET_BLINK_MS, self._blink_caret)

    def _wake_caret(self) -> None:
        """编辑/移动光标后立即让光标可见、重绘并重置闪动相位。"""
        self._caret_on = True
        self._render_math()
        if self._blink_after is not None:
            try:
                self.window.after_cancel(self._blink_after)
            except Exception:
                pass
        if not self.closed:
            self._blink_after = self.window.after(self.CARET_BLINK_MS, self._blink_caret)

    def _font(self, size: int = 9, weight: str = "normal"):
        return self.theme.app_font(size, weight)

    # --- chrome ----------------------------------------------------------------
    def _build_chrome(self) -> None:
        t = self.theme
        bar = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_TOP)
        bar.pack(side=tk.TOP, fill=tk.X)
        bar.pack_propagate(False)
        title = tk.Label(bar, text=t.title, bg=t.title_bg, fg="#dbe7ff", anchor=tk.W, font=self._font(10, "bold"))
        title.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(14, 8))
        self._chrome_button(bar, "×", self.close, True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)
        for widget in (bar, title):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

    def _chrome_button(self, parent, text, command, close=False):
        hover = "#ef4444" if close else self.theme.title_button_hover
        button = tk.Button(parent, text=text, command=command, bd=0, padx=11, pady=5,
                           bg=self.theme.title_button_bg, fg="#e7eefc", activebackground=hover,
                           activeforeground="#ffffff", font=self._font(10), cursor="hand2")
        button.bind("<Enter>", lambda _event: button.configure(bg=hover))
        button.bind("<Leave>", lambda _event: button.configure(bg=self.theme.title_button_bg))
        return button

    # --- body ------------------------------------------------------------------
    def _build_body(self) -> None:
        body = tk.Frame(self.shell, bg="#171b25")
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 点阵 LCD：模式指示 + 自然书写式 + 结果。
        display_box = tk.Frame(body, bg=self.LCD_BG, highlightthickness=5,
                               highlightbackground=self.LCD_FRAME)
        display_box.pack(fill=tk.X, padx=24, pady=(12, 10))
        self.mode_status_var = tk.StringVar(value="COMP   RAD   Math")  # 保留兼容旧代码
        # 真机顶部状态指示条：⇧ / Ⓐ / M 等指示符 + 角度 + Math + 滚动箭头。
        self.status_canvas = tk.Canvas(display_box, bg=self.LCD_BG, height=18,
                                       highlightthickness=0, bd=0)
        self.status_canvas.pack(fill=tk.X, padx=8, pady=(4, 0))
        self.status_canvas.bind("<Configure>", lambda _e: self._render_status_bar())
        self.math_canvas = tk.Canvas(display_box, bg=self.LCD_BG, height=self.DISPLAY_H,
                                     highlightthickness=0, bd=0, takefocus=True, cursor="xterm")
        self.math_canvas.pack(fill=tk.X, padx=7, pady=(0, 0))
        self.math_canvas.bind("<Configure>", lambda _e: self._render_math())
        self.math_canvas.bind("<Button-1>", self._activate_math_panel)
        self.display = _CanvasExpressionEditor(self, self.math_canvas)
        self.result_canvas = tk.Canvas(display_box, bg=self.LCD_BG, height=56,
                                       highlightthickness=0, bd=0)
        self.result_canvas.pack(fill=tk.X, padx=7, pady=(0, 4))
        self.result_canvas.bind("<Configure>", lambda _e: self._render_result_math())
        self.expression_var.trace_add("write", lambda *_: (self._update_preview(), self._render_math(), self._render_status_bar()))
        self.result_var.trace_add("write", lambda *_: self._render_result_math())

        keys = tk.Frame(body, bg="#171b25")
        keys.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 12))

        # SHIFT / ALPHA / REPLAY / MENU / ON——与真机同样的顶部控制区。
        top = tk.Frame(keys, bg="#171b25")
        top.pack(fill=tk.X, pady=(0, 5))
        self.shift_button = self._physical_key(top, "SHIFT", self.toggle_shift, fg="#f4c542", compact=True)
        self.shift_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3)
        self.alpha_button = self._physical_key(top, "ALPHA", self.toggle_alpha, fg="#ef6b76", compact=True)
        self.alpha_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3)
        self._physical_key(top, "REPLAY\n◀ ▲ ▼ ▶", lambda: self.recall_history(-1), compact=True).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=3)
        self._physical_key(top, "MENU", self.open_menu_key, compact=True).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=3)
        self._physical_key(top, "ON", self._power_on, compact=True).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=3)

        sci = tk.Frame(keys, bg="#171b25")
        sci.pack(fill=tk.BOTH, expand=True)
        scientific_rows = [
            [("OPTN", self.open_option_menu, ""), ("CALC", self.evaluate, "SOLVE"),
             ("∫", lambda: self._press_key("tmplsel", "∫□d[x]"), "d/dx"),
             ("□/□", lambda: self._press_key("tmplsel", "□/□"), "a b/c"),
             ("√", lambda: self._press_key("tmplsel", "√(□)"), "x²"),
             ("x²", lambda: self._press_key("ins", "²"), "√")],
            [("x□", lambda: self._power_key("F"), "ⁿ√"),
             ("log", lambda: self._scientific_key("log", "M"), "10ˣ"),
             ("ln", lambda: self._scientific_key("ln", "E"), "eˣ"),
             ("(−)", lambda: self._press_key("ins", "-"), ""),
             ("°′″", lambda: self.cycle_angle_mode(), "DRG▶"),
             ("S⇔D", self.toggle_exact_decimal, "")],
            [("sin", lambda: self._scientific_key("sin", "A"), "sin⁻¹"),
             ("cos", lambda: self._scientific_key("cos", "B"), "cos⁻¹"),
             ("tan", lambda: self._scientific_key("tan", "C"), "tan⁻¹"),
             ("RCL", lambda: self._memory_key_action("recall", "D"), "STO"),
             ("STO", lambda: self._memory_key_action("store", "E"), "M−"),
             ("ENG", lambda: self._memory_key_action("eng", "F"), "←")],
            [("sinh", lambda: self._press_key("ins", "sinh("), "sinh⁻¹"),
             ("cosh", lambda: self._press_key("ins", "cosh("), "cosh⁻¹"),
             ("tanh", lambda: self._press_key("ins", "tanh("), "tanh⁻¹"),
             ("Pol", lambda: self._press_key("tmplsel", "Pol(□,□)"), "Rec"),
             ("Rec", lambda: self._press_key("tmplsel", "Rec(□,□)"), "Pol"),
             ("x!", lambda: self._press_key("ins", "!"), "Abs")],
            [("nPr", lambda: self._press_key("tmplsel", "nPr(□,□)"), ""),
             ("nCr", lambda: self._press_key("tmplsel", "nCr(□,□)"), ""),
             ("Ran#", lambda: self._press_key("tmpl", "rand()"), ""),
             ("RanInt", lambda: self._press_key("tmplsel", "randint(□,□)"), ""),
             ("Abs", lambda: self._press_key("tmplsel", "|□|"), ""),
             ("M+", lambda: self.adjust_memory(1), "M−")],
        ]
        for r, row in enumerate(scientific_rows):
            sci.grid_rowconfigure(r, weight=1, uniform="scirow")
            for c in range(6):
                sci.grid_columnconfigure(c, weight=1, uniform="scicol")
            for c, (primary, command, secondary) in enumerate(row):
                self._dual_key(sci, r, c, primary, command, secondary)

        numeric = tk.Frame(keys, bg="#171b25")
        numeric.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        numeric_rows = [
            [("7", "num", "7"), ("8", "num", "8"), ("9", "num", "9"),
             ("DEL", "back", None), ("AC", "clear", None)],
            [("4", "num", "4"), ("5", "num", "5"), ("6", "num", "6"),
             ("×", "op", "*"), ("÷", "op", "/")],
            [("1", "num", "1"), ("2", "num", "2"), ("3", "num", "3"),
             ("+", "op", "+"), ("−", "op", "-")],
            [("0", "num", "0"), (".", "num", "."), ("×10ˣ", "ins", "*10^"),
             ("Ans", "ans", None), ("=", "equals", None)],
        ]
        for r, row in enumerate(numeric_rows):
            numeric.grid_rowconfigure(r, weight=1, uniform="numrow")
            for c in range(5):
                numeric.grid_columnconfigure(c, weight=1, uniform="numcol")
            for c, (label, kind, payload) in enumerate(row):
                color = "#4d8bb7" if label in ("DEL", "AC") else "#e9edf2"
                fg = "#ffffff" if label in ("DEL", "AC") else "#111827"
                self._physical_key(
                    numeric, label, lambda k=kind, p=payload: self._on_button(k, p),
                    bg=color, fg=fg, numeric=True,
                ).grid(row=r, column=c, sticky="nsew", padx=4, pady=4)

        # 旧字段保留兼容模式方法。
        self.grid = tk.Frame(body, bg="#171b25")
        self.memory_button = self.shift_button
        self.memory_var = tk.StringVar(value="A = 0")
        self.mode_button = self.shift_button
        self._mode_buttons = {}
        self._update_status_line()
        self.window.bind("<Key>", self._handle_display_key)

        bottom = tk.Frame(self.shell, bg=self.theme.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)
        grip = tk.Label(bottom, text="◢", bg=self.theme.title_bg, fg="#8aa0c0", cursor="size_nw_se")
        grip.pack(side=tk.RIGHT, padx=(0, 6))
        grip.bind("<ButtonPress-1>", self.start_resize)
        grip.bind("<B1-Motion>", self.do_resize)

    def _physical_key(self, parent, text, command, *, bg="#343b48", fg="#f8fafc",
                      compact=False, numeric=False):
        return tk.Button(
            parent, text=text, command=command, bd=0, relief=tk.RAISED,
            bg=bg, fg=fg, activebackground="#566170", activeforeground="#ffffff",
            cursor="hand2", font=self._font(11 if numeric else (8 if compact else 10), "bold"),
            padx=5, pady=(3 if compact else 7), highlightthickness=1,
            highlightbackground="#0b0d12",
        )

    def _dual_key(self, parent, row, column, primary, command, secondary="") -> None:
        cell = tk.Frame(parent, bg="#171b25")
        cell.grid(row=row, column=column, sticky="nsew", padx=3, pady=2)
        tk.Label(cell, text=secondary or " ", bg="#171b25", fg="#f4c542",
                 font=self._font(7, "bold"), height=1).pack(fill=tk.X)
        self._physical_key(cell, primary, command).pack(fill=tk.BOTH, expand=True)

    def _update_status_line(self) -> None:
        flags = []
        if self.shift_active:
            flags.append("S")
        if self.alpha_active:
            flags.append("A")
        if any(value != "0" for value in self.memories.values()):
            flags.append("M")
        mode_label = dict(self.MODES).get(self.mode, self.mode)
        suffix = ("   " + " ".join(flags)) if flags else ""
        if hasattr(self, "mode_status_var"):
            self.mode_status_var.set(f"{mode_label}   {self.angle_mode}   Math{suffix}")
        self._render_status_bar()

    def _render_status_bar(self) -> None:
        """像真机 LCD 顶行那样绘制状态指示符。"""
        canvas = getattr(self, "status_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        try:
            width = int(canvas.winfo_width()) or 1
        except Exception:
            width = 1
        fnt = self._font(8, "bold")
        fg = self.LCD_FG          # 单色点阵屏：所有指示符同一墨色
        cy = 9
        # 左侧：激活的修饰指示符（⇧ SHIFT、Ⓐ ALPHA、M 独立存储）。
        x = 3
        if self.shift_active:
            canvas.create_text(x, cy, text="⇧", anchor="w", fill=fg, font=self._font(10, "bold")); x += 16
        if self.alpha_active:
            canvas.create_text(x, cy, text="Ⓐ", anchor="w", fill=fg, font=fnt); x += 16
        if getattr(self, "_sto_pending", False):
            canvas.create_text(x, cy, text="STO", anchor="w", fill=fg, font=fnt); x += 26
        if any(value != "0" for value in self.memories.values()):
            canvas.create_text(x, cy, text="M", anchor="w", fill=fg, font=fnt); x += 14
        # 右侧（从右往左排）：Math、角度 D/R/G、滚动箭头。
        rx = width - 4
        for token in ("Math", {"DEG": "D", "RAD": "R", "GRA": "G"}.get(self.angle_mode, "R")):
            tid = canvas.create_text(rx, cy, text=token, anchor="e", fill=fg, font=fnt)
            bbox = canvas.bbox(tid)
            rx -= (bbox[2] - bbox[0]) + 9 if bbox else 22
        if len(self.expression_var.get()) > 16:
            canvas.create_text(rx, cy, text="◀▶", anchor="e", fill=fg, font=fnt)
        # 中部：当前模式名。
        mode_label = dict(self.MODES).get(self.mode, self.mode)
        canvas.create_text(max(x + 8, width * 0.40), cy, text=mode_label,
                           anchor="w", fill=fg, font=fnt)

    def _clear_modifiers(self) -> None:
        self.shift_active = False
        self.alpha_active = False
        if hasattr(self, "shift_button"):
            self.shift_button.configure(bg="#343b48")
            self.alpha_button.configure(bg="#343b48")
        self._update_status_line()

    def toggle_shift(self) -> None:
        self.shift_active = not self.shift_active
        self.alpha_active = False
        self.shift_button.configure(bg="#7a6420" if self.shift_active else "#343b48")
        self.alpha_button.configure(bg="#343b48")
        self._update_status_line()
        self.display.focus_set()

    def toggle_alpha(self) -> None:
        self.alpha_active = not self.alpha_active
        self.shift_active = False
        self.alpha_button.configure(bg="#77333c" if self.alpha_active else "#343b48")
        self.shift_button.configure(bg="#343b48")
        self._update_status_line()
        self.display.focus_set()

    def _press_key(self, kind: str, payload) -> None:
        self._on_button(kind, payload)
        self._clear_modifiers()

    def _scientific_key(self, name: str, alpha_key: str | None = None) -> None:
        if self.alpha_active and alpha_key:
            self.memory_key = alpha_key
            self._insert(alpha_key)
        elif self.shift_active:
            shifted = {
                "sin": "sin⁻¹(", "cos": "cos⁻¹(", "tan": "tan⁻¹(",
                "log": "10^(", "ln": "e^(",
            }.get(name, name + "(")
            self._insert(shifted)
        else:
            self._insert(name + "(")
        self._clear_modifiers()

    def _power_key(self, alpha_key: str = "F") -> None:
        if self.alpha_active:
            self.memory_key = alpha_key
            self._insert(alpha_key)
        elif self.shift_active:
            self._insert_template("(□)^(1/(□))")
        else:
            self._insert("^")
        self._clear_modifiers()

    def _memory_key_action(self, action: str, alpha_key: str) -> None:
        if self.alpha_active:
            self.memory_key = alpha_key
            self._insert(alpha_key)
        elif action == "recall":
            self.recall_memory()
        elif action == "store":
            self.store_memory()
        else:
            self._insert("*10^3")
        self._clear_modifiers()

    def _power_on(self) -> None:
        self.expression_var.set("")
        self.result_var.set("0")
        self._clear_modifiers()
        self.display.focus_set()

    def _open_lcd_menu(self, title: str, items: list[tuple[str, callable]],
                       columns: int | None = None) -> tk.Toplevel:
        """真机风格的屏内菜单：数字键直选、方向键移动高亮、=/Enter 确认、AC/Esc 退出。"""
        dialog = tk.Toplevel(self.window)
        dialog.withdraw()
        dialog.overrideredirect(True)
        dialog.transient(self.window)
        dialog.configure(bg=self.LCD_FRAME)
        panel = tk.Frame(dialog, bg=self.LCD_BG, highlightthickness=4, highlightbackground=self.LCD_FRAME)
        panel.pack(fill=tk.BOTH, expand=True)
        tk.Label(panel, text=title, bg=self.LCD_HEADER, fg=self.LCD_FG, anchor=tk.W,
                 font=self._font(10, "bold")).pack(fill=tk.X, padx=4, pady=4)
        grid = tk.Frame(panel, bg=self.LCD_BG)
        grid.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))
        if columns is None:
            columns = 4 if len(items) > 12 else (3 if len(items) > 6 else 2)
        rows = max(1, math.ceil(len(items) / columns))
        buttons: list[tk.Button] = []
        state = {"sel": 0}

        def choose(index: int) -> None:
            if 0 <= index < len(items):
                dialog.destroy()
                items[index][1]()

        def highlight() -> None:
            for j, button in enumerate(buttons):
                if j == state["sel"]:
                    button.configure(bg=self.LCD_SELECT, fg=self.LCD_SELECT_FG)
                else:
                    button.configure(bg=self.LCD_BG, fg=self.LCD_FG)

        def move(delta: int) -> None:
            state["sel"] = max(0, min(len(items) - 1, state["sel"] + delta))
            highlight()

        for index, (label, _command) in enumerate(items):
            row, column = divmod(index, columns)
            grid.grid_rowconfigure(row, weight=1)
            grid.grid_columnconfigure(column, weight=1)
            button = tk.Button(grid, text=f"{index + 1}: {label}", command=lambda i=index: choose(i),
                               bd=0, bg=self.LCD_BG, fg=self.LCD_FG, activebackground=self.LCD_HEADER,
                               font=self._font(9, "bold"), anchor=tk.W, padx=6)
            button.grid(row=row, column=column, sticky="nsew", padx=2, pady=2)
            buttons.append(button)
        highlight()

        def on_key(event):
            keysym = event.keysym
            if keysym in ("Escape",):
                dialog.destroy(); return "break"
            if keysym in ("Return", "KP_Enter", "equal"):
                choose(state["sel"]); return "break"
            if keysym == "Right":
                move(1); return "break"
            if keysym == "Left":
                move(-1); return "break"
            if keysym == "Down":
                move(columns); return "break"
            if keysym == "Up":
                move(-columns); return "break"
            char = event.char
            if char and char.isdigit():
                number = int(char)
                if 1 <= number <= len(items):
                    choose(number - 1)
                return "break"
            return None

        dialog.bind("<Key>", on_key)
        width = max(360, self.window.winfo_width() - 54)
        height = min(560, max(180, 42 + rows * 46))
        x = self.window.winfo_rootx() + (self.window.winfo_width() - width) // 2
        y = self.window.winfo_rooty() + 145
        dialog.geometry(f"{width}x{height}{x:+d}{y:+d}")
        dialog.attributes("-topmost", bool(self.app.topmost_var.get()))
        dialog.deiconify()
        dialog.lift(self.window)
        dialog.focus_force()
        return dialog

    def open_menu_key(self) -> None:
        """MENU 键：直接打开模式菜单；SHIFT+MENU 打开 SETUP 设置。"""
        go_setup = self.shift_active
        self._clear_modifiers()
        if go_setup:
            self.open_setup_menu()
        else:
            self.open_mode_menu()

    def open_mode_menu(self) -> tk.Toplevel:
        items = [(f"{code}  {label}", lambda c=code: self.set_mode(c)) for code, label in self.MODES]
        return self._open_lcd_menu("MENU  请选择计算模式", items, columns=3)

    def open_setup_menu(self) -> tk.Toplevel:
        items = [
            ("角度单位 Degree（度）", lambda: self._set_angle("DEG")),
            ("角度单位 Radian（弧度）", lambda: self._set_angle("RAD")),
            ("角度单位 Gradian（百分度）", lambda: self._set_angle("GRA")),
            ("显示 MathIO（精确/自然书写）", lambda: self._set_display(False)),
            ("显示 LineIO（小数结果）", lambda: self._set_display(True)),
            ("S⇔D 精确⇔小数 切换", self.toggle_exact_decimal),
            ("清除历史 Clear History", self._clear_history),
            ("全部重置 Reset All", self._reset_all),
        ]
        return self._open_lcd_menu("SETUP  设置", items, columns=2)

    def _set_angle(self, mode: str) -> None:
        self.angle_mode = mode
        self._update_status_line()
        self._update_preview()
        self.display.focus_set()

    def _set_display(self, decimal: bool) -> None:
        self._decimal_view = decimal
        self.result_var.set(self._format_result_view(self._last_result_raw))
        self._update_status_line()
        self.display.focus_set()

    def _clear_history(self) -> None:
        self.history.clear()
        self._history_index = 0
        self.app.write_status("计算器：历史记录已清除。")
        self.display.focus_set()

    def _reset_all(self) -> None:
        self.expression_var.set("")
        self.result_var.set("0")
        self._last_result_raw = "0"
        self.memories = {key: "0" for key in "ABCDEFM"}
        self.ans_text = "0"
        self.angle_mode = "RAD"
        self._decimal_view = False
        self._clear_modifiers()
        self._update_status_line()
        self.display.focus_set()

    def open_option_menu(self) -> None:
        rows = self.FUNCTION_ROWS.get(self.mode, [])
        items = []
        for row in rows:
            for label, kind, payload in row:
                items.append((label, lambda k=kind, p=payload: self._on_button(k, p)))
        if not items:
            items = [("无可用选项", lambda: None)]
        self._open_lcd_menu(f"OPTN  {dict(self.MODES).get(self.mode, self.mode)}", items)

    def _button_style(self, kind: str) -> dict:
        if kind == "equals":
            return dict(bg=self.theme.accent, fg="#ffffff", activebackground=self.theme.accent_hover,
                        activeforeground="#ffffff", font=self._font(13, "bold"))
        if kind in ("num", "ans"):
            return dict(bg="#ffffff", fg="#0f172a", activebackground="#e2e8f4",
                        activeforeground="#0f172a", font=self._font(13, "bold"))
        if kind == "op":
            return dict(bg=self.theme.accent_soft, fg=self.theme.accent,
                        activebackground=self.theme.accent_soft_hover,
                        activeforeground=self.theme.accent, font=self._font(13, "bold"))
        if kind in ("clear", "back"):
            return dict(bg="#fee2e2", fg="#b91c1c", activebackground="#fecaca",
                        activeforeground="#b91c1c", font=self._font(12, "bold"))
        if kind in ("diffwrap", "fnwrap", "tablewrap", "tmpl", "tmplsel", "currency", "onlinecurrency", "onlineconvert"):
            return dict(bg="#e0f2fe", fg="#0369a1", activebackground="#bae6fd",
                        activeforeground="#0369a1", font=self._font(10, "bold"))
        if kind == "base":
            return dict(bg="#eef2f9", fg="#334155", activebackground="#e2e8f4",
                        activeforeground="#111827", font=self._font(10, "bold"))
        return dict(bg="#eef2f9", fg="#334155", activebackground="#e2e8f4",
                    activeforeground="#111827", font=self._font(10))

    # --- keypad / modes --------------------------------------------------------
    def _render_keypad(self) -> None:
        for child in self.grid.winfo_children():
            child.destroy()
        self._base_buttons = {}
        rows = self.FUNCTION_ROWS.get(self.mode, []) + self.NUMERIC_ROWS
        for c in range(self.COLUMNS):
            self.grid.grid_columnconfigure(c, weight=1, uniform="calc")
        for r in range(len(rows)):
            self.grid.grid_rowconfigure(r, weight=1, uniform="calcrow")
        for r, row in enumerate(rows):
            for c, (label, kind, payload) in enumerate(row):
                btn = tk.Button(
                    self.grid, text=label, bd=0, cursor="hand2",
                    command=lambda k=kind, p=payload: self._on_button(k, p),
                    **self._button_style(kind),
                )
                btn.grid(row=r, column=c, sticky="nsew", padx=3, pady=3, ipady=4)
                if kind == "base":
                    self._base_buttons[payload] = btn
        self._highlight_base()

    def set_mode(self, code: str) -> None:
        if code == self.mode:
            return
        self.mode = code
        self._update_preview()
        self._update_status_line()
        self.display.focus_set()

    def _highlight_mode(self) -> None:
        for code, btn in self._mode_buttons.items():
            if code == self.mode:
                btn.configure(bg=self.theme.accent, fg="#ffffff", activebackground=self.theme.accent_hover)
            else:
                btn.configure(bg="#e2e8f4", fg="#334155", activebackground="#d3ddec")

    def set_base(self, code: str) -> None:
        self.base_in = code
        self._highlight_base()
        self._update_preview()
        self.display.focus_set()

    def _highlight_base(self) -> None:
        for code, btn in getattr(self, "_base_buttons", {}).items():
            if code == self.base_in:
                btn.configure(bg=self.theme.accent, fg="#ffffff", activebackground=self.theme.accent_hover)
            else:
                btn.configure(bg="#eef2f9", fg="#334155", activebackground="#e2e8f4")

    # --- behaviour -------------------------------------------------------------
    def _on_button(self, kind: str, payload) -> None:
        if kind == "equals":
            self.evaluate()
        elif kind == "clear":
            self.expression_var.set("")
            self.result_var.set("0")
            self._clear_modifiers()
            self.display.focus_set()
        elif kind == "back":
            self._backspace()
        elif kind == "ans":
            self._insert(self.ans_text)
        elif kind == "base":
            self.set_base(payload)
        elif kind in ("currency", "onlinecurrency"):
            src, dst = payload
            current = self.expression_var.get().strip()
            amount = current or "100"
            func = "netfx" if kind == "onlinecurrency" else "fx"
            self.expression_var.set(f"{func}({amount}, {src}, {dst})")
            self.display.icursor(tk.END)
            self.display.focus_set()
        elif kind == "onlineconvert":
            self._set_online_currency_expression()
        elif kind == "tmpl":
            self.expression_var.set(payload)
            self.display.icursor(tk.END)
            self.display.focus_set()
        elif kind == "diffwrap":
            self._wrap(payload, with_var=True)
        elif kind == "tmplsel":
            self._insert_template(payload)
        elif kind == "fnwrap":
            self._wrap(payload, with_var=False)
        elif kind == "tablewrap":
            current = self.expression_var.get().strip()
            self.expression_var.set(f"table({current}, 1, 5, 1)" if current else "table(")
            self.display.icursor(tk.END)
            self.display.focus_set()
        else:  # num / op / ins
            self._insert(payload)

    def _set_online_currency_expression(self) -> None:
        current = self.expression_var.get().strip()
        match = re.match(r"\s*(?:fx|currency|货币|netfx|onlinefx|联网)\((.*)\)\s*$", current, re.I | re.S)
        if match:
            self.expression_var.set(f"netfx({match.group(1)})")
        else:
            parts = re.sub(r"\bto\b", " ", current.replace("→", " "), flags=re.I).split()
            if len(parts) == 3:
                self.expression_var.set(f"netfx({parts[0]}, {parts[1]}, {parts[2]})")
            elif len(parts) == 2:
                self.expression_var.set(f"netfx(1, {parts[0]}, {parts[1]})")
            else:
                self.expression_var.set(f"netfx({current or '100'}, USD, CNY)")
        self.display.icursor(tk.END)
        self.display.focus_set()

    def _activate_math_panel(self, event=None):
        text = self.expression_var.get()
        if event is not None and text:
            index = math_render.caret_index_at(
                self.math_canvas, text, event.x,
                base_size=self.DISPLAY_BASE_SIZE, pad=12, placeholder=" ")
            self.display.icursor(index)
        else:
            self.display.icursor(tk.END)
        self.display.focus_set()
        return "break"

    def _handle_display_key(self, event):
        ctrl = bool(event.state & 0x4)
        shift = bool(event.state & 0x1)
        key = event.keysym

        if key in ("Return", "KP_Enter"):
            self.evaluate()
            return "break"
        if key == "Tab":
            return self._next_box(event)
        if key == "BackSpace":
            self._backspace()
            return "break"
        if key == "Delete":
            self._delete_forward()
            return "break"
        if key in ("Left", "Right", "Up", "Down"):
            # 已取消方向键移动光标：改用鼠标点击 LCD 定位光标。
            return "break"
        if key == "Home":
            self._set_cursor(0, select=shift)
            return "break"
        if key == "End":
            self._set_cursor(len(self.expression_var.get()), select=shift)
            return "break"
        if ctrl and key.lower() == "a":
            self.display.selection_range(0, tk.END)
            self.display.icursor(tk.END)
            return "break"
        if ctrl:
            return None
        if event.char and len(event.char) == 1 and ord(event.char) >= 32:
            self._insert(event.char)
            return "break"
        return None

    def _set_cursor(self, index: int, select: bool = False) -> None:
        old = self._cursor
        text_len = len(self.expression_var.get())
        self._cursor = max(0, min(text_len, index))
        if select and old != self._cursor:
            self._selection = (min(old, self._cursor), max(old, self._cursor))
        elif not select:
            self._selection = None
        self._wake_caret()

    def _move_cursor(self, delta: int, select: bool = False) -> None:
        if self.display.selection_present() and not select:
            start, end = self._selection
            self._set_cursor(start if delta < 0 else end)
            return
        self._set_cursor(self._cursor + delta, select=select)

    def _insert(self, text: str) -> None:
        try:
            if self.display.selection_present():  # 直接覆盖占位框 □ 等选中内容
                self.display.delete("sel.first", "sel.last")
            self.display.insert(tk.INSERT, text)
        except Exception:
            self.expression_var.set(self.expression_var.get() + text)
        self.display.focus_set()

    def _insert_template(self, tmpl: str) -> None:
        """插入 991 自然书写模板（如 ∫□d[x]），并选中占位框 □ 方便直接输入。"""
        try:
            if self.display.selection_present():
                self.display.delete("sel.first", "sel.last")
            idx = int(self.display.index(tk.INSERT))
            self.display.insert(idx, tmpl)
            box = tmpl.find("□")
            if box >= 0:
                self.display.selection_range(idx + box, idx + box + 1)
                self.display.icursor(idx + box + 1)
            else:
                self.display.icursor(idx + len(tmpl))
        except Exception:
            self.expression_var.set(self.expression_var.get() + tmpl)
        self.display.focus_set()

    def _next_box(self, _event=None):
        """按 Tab 跳到下一个占位框 □ 并选中，方便依次填写多框模板。"""
        self._move_template_box(1, wrap=True)
        return "break"

    def _move_template_box(self, direction: int, wrap: bool = False) -> bool:
        """在自然书写的分子/分母/上下限占位框之间导航。"""
        try:
            text = self.expression_var.get()
            boxes = [index for index, char in enumerate(text) if char == "□"]
            if not boxes:
                return False
            current = int(self.display.index(tk.INSERT))
            selected_box = bool(
                self._selection and text[self._selection[0]:self._selection[1]] == "□"
            )
            if selected_box:
                current = self._selection[0]
            if direction > 0:
                candidates = [index for index in boxes if index > current or (not selected_box and index >= current)]
                target = candidates[0] if candidates else (boxes[0] if wrap else None)
            else:
                candidates = [index for index in boxes if index < current]
                target = candidates[-1] if candidates else (boxes[-1] if wrap else None)
            if target is None:
                return False
            self.display.selection_range(target, target + 1)
            self.display.icursor(target + 1)
            self.display.focus_set()
            return True
        except Exception:
            return False

    def _wrap(self, func: str, with_var: bool = True) -> None:
        current = self.expression_var.get().strip()
        if current:
            self.expression_var.set(f"{func}({current}, x)" if with_var else f"{func}({current})")
            self.display.icursor(tk.END)
        else:
            self._insert(f"{func}(")
        self.display.focus_set()

    def _backspace(self) -> None:
        try:
            if self.display.selection_present():
                self.display.delete("sel.first", "sel.last")
            else:
                index = self.display.index(tk.INSERT)
                if index > 0:
                    self.display.delete(index - 1)
        except Exception:
            self.expression_var.set(self.expression_var.get()[:-1])
        self.display.focus_set()

    def _delete_forward(self) -> None:
        try:
            if self.display.selection_present():
                self.display.delete("sel.first", "sel.last")
            else:
                self.display.delete(tk.INSERT)
        except Exception:
            pass
        self.display.focus_set()

    def cycle_angle_mode(self) -> None:
        i = self.ANGLE_MODES.index(self.angle_mode)
        self.angle_mode = self.ANGLE_MODES[(i + 1) % len(self.ANGLE_MODES)]
        self._update_status_line()
        self._update_preview()
        self.display.focus_set()

    # --- 991 memory / replay / S<=>D -----------------------------------------
    def _format_result_view(self, raw: str) -> str:
        if "≈" not in raw:
            return raw
        exact, decimal = raw.rsplit("≈", 1)
        if self._decimal_view:
            return "≈ " + decimal.strip()
        return exact.rstrip()

    def toggle_exact_decimal(self) -> None:
        self._decimal_view = not self._decimal_view
        self.result_var.set(self._format_result_view(self._last_result_raw))
        self._update_status_line()
        self.display.focus_set()

    def cycle_memory(self) -> None:
        keys = "ABCDEFM"
        self.memory_key = keys[(keys.index(self.memory_key) + 1) % len(keys)]
        self._update_memory_display()
        self.display.focus_set()

    def _update_memory_display(self) -> None:
        value = self.memories.get(self.memory_key, "0")
        self.memory_var.set(f"{self.memory_key} = {value}")
        self._update_status_line()

    def store_memory(self) -> None:
        self.memories[self.memory_key] = self.ans_text
        self._update_memory_display()
        self.app.write_status(f"计算器：已将 {self.ans_text} 存入 {self.memory_key}。")
        self.display.focus_set()

    def recall_memory(self) -> None:
        # 插入存储器名称而非展开数值，后续修改存储器时表达式仍可复用。
        self._insert(self.memory_key)

    def adjust_memory(self, direction: int) -> None:
        try:
            current = float(self.memories.get(self.memory_key, "0"))
            delta = float(self.ans_text)
            self.memories[self.memory_key] = _format_number(current + direction * delta)
            self._update_memory_display()
            self.app.write_status(
                f"计算器：{self.memory_key}{'+' if direction > 0 else '−'}={delta:g}")
        except (TypeError, ValueError):
            self.app.write_status("计算器：当前 Ans 不是可存储的实数。")
        self.display.focus_set()

    def _record_history(self, expression: str, result: str, mode: str, base_in: str) -> None:
        if self.history and self.history[-1]["expression"] == expression and self.history[-1]["result"] == result:
            self._history_index = len(self.history)
            return
        self.history.append({"expression": expression, "result": result, "mode": mode, "base": base_in})
        if len(self.history) > 100:
            self.history.pop(0)
        self._history_index = len(self.history)

    def recall_history(self, direction: int) -> None:
        if not self.history:
            self.app.write_status("计算器：暂无历史记录。")
            return
        self._history_index = max(0, min(len(self.history) - 1, self._history_index + direction))
        item = self.history[self._history_index]
        if item["mode"] != self.mode:
            self.mode = item["mode"]
            self._update_status_line()
        self.base_in = item["base"]
        self.expression_var.set(item["expression"])
        self.display.icursor(tk.END)
        self._last_result_raw = item["result"]
        self.result_var.set(self._format_result_view(item["result"]))
        self.display.focus_set()

    def _sub_ans(self, text: str) -> str:
        return re.sub(r"\bAns\b", f"({self.ans_text})", text)

    def _render_math(self) -> None:
        # 把当前表达式排版成二维数学式显示，并在 2D 面板中绘制编辑光标。
        try:
            text = self.expression_var.get()
            self._cursor = max(0, min(len(text), self._cursor))
            mark = math_render.CURSOR_MARK
            if self._selection:
                a, b = self._selection
                a, b = max(0, a), min(len(text), b)
                shown = text[:a] + mark + text[a:b] + mark + text[b:]
                caret_color = self.LCD_FG
            else:
                # 始终插入光标占位符，让排版固定；闪烁只切换颜色（隐藏时用底色），
                # 这样光标“消失的一瞬间”不会再让整行左右跳动 / 留下空位。
                shown = text[:self._cursor] + mark + text[self._cursor:]
                caret_color = self.LCD_FG if self._caret_on else self.LCD_BG
            math_render.render(
                self.math_canvas,
                shown or " ",
                base_size=self.DISPLAY_BASE_SIZE,
                placeholder=" ",
                fg=self.LCD_FG,
                caret=caret_color,
            )
        except Exception:
            pass

    def _render_result_math(self) -> None:
        """把精确结果也用二维数学排版显示在 LCD 下行。"""
        try:
            text = self.result_var.get().strip() or "0"
            text = text.replace("\n", "   ").removeprefix("= ").removeprefix("解：")
            math_render.render(
                self.result_canvas, text, base_size=22, pad=8,
                fg=self.LCD_FG, placeholder="0",
            )
        except Exception:
            pass

    def _update_preview(self) -> None:
        # 不做实时预览：仅在按下 = 后才显示结果，输入时清空上一次结果。
        self.result_var.set("0" if not self.expression_var.get().strip() else "")

    def evaluate(self) -> None:
        source_text = self.expression_var.get().strip()
        if not source_text:
            return
        self._eval_seq += 1
        seq = self._eval_seq
        self._pending_result = None
        self.result_var.set("计算中……")
        mode, base_in, angle = self.mode, self.base_in, self.angle_mode
        text = self._sub_ans(source_text)
        variables = dict(self.memories)

        def work():
            try:
                if mode == "BASE":
                    out = base_calculate(text, base_in)
                elif mode == "TABLE" or text.lower().startswith("table("):
                    out = table_calculate(text, angle)
                elif mode == "CURR" or re.match(r"\s*(?:netfx|onlinefx|联网|fx|currency|货币)\(", text, re.I):
                    out = currency_calculate(text)
                else:
                    out = symbolic_calculate(text, angle, variables)
            except Exception:
                # Fall back to the fast numeric evaluator for plain arithmetic.
                try:
                    out = "= " + _format_number(safe_eval_expression(text, angle))
                except Exception as exc:
                    out = f"错误：{exc}"
            # Only touch a plain attribute here; Tk is updated on the main thread.
            self._pending_result = (seq, out, source_text, mode, base_in)

        threading.Thread(target=work, daemon=True, name="Passer-Calc").start()
        self._poll_result(seq)

    def _poll_result(self, seq: int) -> None:
        if self.closed or seq != self._eval_seq:
            return
        pending = self._pending_result
        if pending is not None and pending[0] == seq:
            self._pending_result = None
            out, source_text, mode, base_in = pending[1:]
            self._last_result_raw = out
            self.result_var.set(self._format_result_view(out))
            self._remember_answer(out)
            self._record_history(source_text, out, mode, base_in)
            return
        self.window.after(60, lambda: self._poll_result(seq))

    def _remember_answer(self, out: str) -> None:
        """Store a reusable numeric value for the Ans key when one is available."""
        if not out or out.startswith("错误"):
            return
        candidate = out
        if "≈" in candidate:
            candidate = candidate.split("≈")[-1]
        candidate = candidate.split("\n")[0].lstrip("= ").strip()
        if re.fullmatch(r"-?\d+(\.\d+)?", candidate):
            self.ans_text = candidate

    # --- window plumbing -------------------------------------------------------
    def start_move(self, event):
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event):
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.theme.place_toplevel_absolute(self.window, max(self.window.winfo_width(), self.MIN_W),
                                           max(self.window.winfo_height(), self.MIN_H),
                                           wx + event.x_root - sx, wy + event.y_root - sy)

    def start_resize(self, event):
        self.resize_start = (event.x_root, event.y_root, self.window.winfo_width(), self.window.winfo_height())

    def do_resize(self, event):
        if not self.resize_start:
            return
        sx, sy, sw, sh = self.resize_start
        self.window.geometry(f"{max(self.MIN_W, sw + event.x_root - sx)}x{max(self.MIN_H, sh + event.y_root - sy)}")

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self._blink_after is not None:
            try:
                self.window.after_cancel(self._blink_after)
            except Exception:
                pass
            self._blink_after = None
        if getattr(self.app, "calculator_window", None) is self:
            self.app.calculator_window = None
        self.window.destroy()
