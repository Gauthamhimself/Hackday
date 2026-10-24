r"""Turn the LaTeX Gemma writes into readable Unicode text. Owner: Tegh.

    readable(r"$n \ge 1$, so $\{H_1, \dots, H_n\}$")  ->  "n ≥ 1, so {H₁, …, Hₙ}"

The app, the proof tree and the manim video all show plain text, so parser.py runs every
human-facing field through readable(). The LaTeX form stays in each step's display.tex.
"""

import re

from pylatexenc.latex2text import LatexNodes2Text

_converter = LatexNodes2Text(math_mode="text", strict_latex_spaces=False)

SUB = str.maketrans("0123456789+-=()aeoxhklmnpstij", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜᵢⱼ")
SUP = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
# Relations and arrows read better with a space on each side ("n ∈ ℕ", not "n ∈ℕ").
SPACED = "∈∉≤≥≡≠≈⇒⇔→←↔⊆⊂⊇⊃∪∩=<>"
LATEX_HINT = re.compile(r"\\[A-Za-z{}]|\$")


def unicode_scripts(text: str) -> str:
    """'S_{n+1}' -> 'Sₙ₊₁', 'x^2' -> 'x²' (only when every character has a Unicode form)."""
    def convert(match, table):
        body = match.group(1) or match.group(2)
        out = body.translate(table)
        return out if all(ord(c) > 127 for c in out) else match.group(0)
    text = re.sub(r"_\{([^{}]+)\}|_([A-Za-z0-9])(?![A-Za-z0-9])", lambda mt: convert(mt, SUB), text)
    return re.sub(r"\^\{([^{}]+)\}|\^([A-Za-z0-9])(?![A-Za-z0-9])", lambda mt: convert(mt, SUP), text)


def readable(text):
    """LaTeX (with or without $...$) -> plain Unicode. Non-strings and plain text pass through safely."""
    if not isinstance(text, str) or not text:
        return text
    if LATEX_HINT.search(text):
        text = re.sub(r"(?<!\\)%", r"\\%", text)                     # % starts a LaTeX comment
        text = re.sub(r"\^\s*\{?\\circ\}?", "°", text)                 # 60^\circ -> 60°
        text = re.sub(r"\\pmod\s*\{([^{}]*)\}", r" (mod \1)", text)
        text = text.replace(r"\bmod", " mod ").replace(r"\mid", " ∣ ")
        text = re.sub(r"(\\[A-Za-z]+)(?=[_^])", r"\1 ", text)         # keep \sum apart from its _{...}
        text = unicode_scripts(text)                                   # before braces are flattened
        # Subscripts that can't be Unicode: a single word stays attached (V_even), anything longer
        # goes in brackets (\sum_{v \in V} -> ∑[v ∈ V]). No "_(" because Markdown would read
        # pairs of those as italics. Innermost first, so nested groups keep both levels.
        while True:
            grouped = re.sub(r"_\{([A-Za-z0-9]+)\}", r"_\1", text)
            grouped = re.sub(r"_\{([^{}]+)\}", r"[\1]", grouped)
            grouped = re.sub(r"\^\{([^{}]+)\}", r"^(\1)", grouped)
            if grouped == text:
                break
            text = grouped
        try:
            text = _converter.latex_to_text(text)
        except Exception:  # noqa: BLE001 -- never lose the text over a parse problem
            pass
        text = re.sub(f"\\s*([{SPACED}])\\s*", r" \1 ", text)
        text = re.sub(r"< =|> =|= =", lambda mt: mt.group(0).replace(" ", ""), text)
        text = re.sub(r"(?<=\S) (?=[₀-₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜᵢⱼ⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ])", "", text)  # "∑ ᵢ₌₁" -> "∑ᵢ₌₁"
    text = unicode_scripts(text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def make_readable(proof: dict) -> dict:
    """Clean every human-facing text field of a parsed proof in place (display.tex is left as LaTeX)."""
    for key in ("title", "summary", "verdict_explanation"):
        proof[key] = readable(proof.get(key))
    for step in proof.get("steps", []):
        if not isinstance(step, dict):
            continue
        for key in ("statement", "justification", "gap_explanation", "error_explanation"):
            step[key] = readable(step.get(key))
        if isinstance(step.get("animation"), dict):
            step["animation"]["caption"] = readable(step["animation"].get("caption"))
        if isinstance(step.get("display"), dict):
            step["display"]["text"] = readable(step["display"].get("text"))
    params = (proof.get("visual") or {}).get("params") or {}
    for value in params.values():
        for el in value if isinstance(value, list) else []:
            if isinstance(el, dict) and isinstance(el.get("label"), str):
                el["label"] = readable(el["label"])
    return proof
