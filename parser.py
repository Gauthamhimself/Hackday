"""Gemma 4 proof parser: photo or text in, structured proof JSON out.

Owner: Tegh

Try it from the terminal:
    python parser.py                    # runs the built-in "horses" sample
    python parser.py photo.jpg          # parses a photo
    python parser.py photo.jpg -o out.json
"""

import argparse
import io
import json
import math
import mimetypes
import re
import sys
import textwrap
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from prompts import SYSTEM_PROMPT, retry_prompt

load_dotenv(Path(__file__).with_name(".env"))

MODELS = ["gemma-4-26b-a4b-it", "gemma-4-31b-it"]
DEFAULT_MODEL = MODELS[0]

SAMPLES = {
    "√2 is irrational (valid)": textwrap.dedent("""\
        Claim: √2 is irrational.
        Proof: Suppose √2 = a/b where a, b are integers with no common factors.
        Then 2 = a²/b², so a² = 2b². Thus a² is even, so a is even.
        Write a = 2k. Then 4k² = 2b², so b² = 2k², so b is even.
        So a and b are both even, contradicting that they have no common factors.
        Therefore √2 is irrational."""),
    "All horses are the same color (broken)": textwrap.dedent("""\
        Claim: In any set of n horses, all horses are the same color.
        Proof by induction on n.
        Base case n = 1: one horse is trivially the same color as itself.
        Inductive step: assume any set of n horses is all one color.
        Take a set of n+1 horses. Remove the first horse: the remaining n are one color.
        Remove the last horse instead: those n are also one color.
        The two groups overlap, so all n+1 horses are the same color. ∎"""),
}

# A backslash that isn't already part of a valid JSON escape (\\, \", \/, \uXXXX).
# Gemma often writes LaTeX like \ge or \mathbb inside strings, which breaks json.loads.
_LONE_BACKSLASH = re.compile(r'(\\\\)|\\(?!["/]|u[0-9a-fA-F]{4})')


class ParseError(Exception):
    """Gemma answered, but not with usable JSON. `raw` holds what it said."""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


def extract_json(text: str) -> dict:
    """Gemma may wrap JSON in code fences, add stray text, or leave LaTeX unescaped."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model response")
    body = text[start : end + 1]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return json.loads(_LONE_BACKSLASH.sub(lambda m: m.group(1) or "\\\\", body))


TEMPLATES = {"dominoes", "sets", "number_line", "geometry", "graph", "function_plot", "flow"}
ACTIONS = {"appear", "highlight", "move", "connect", "break", "transform", "zoom"}
EMPHASES = {"glow", "pulse", "shake", "none"}
DURATION_MS = {"short": 400, "medium": 800, "long": 1500}


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _element_ids(params: dict) -> set[str]:
    """Every id defined anywhere in the template params (points, groups, marks, ...)."""
    ids = set(params.get("domino_ids", []))
    for value in params.values():
        if isinstance(value, list):
            ids.update(str(el["id"]) for el in value if isinstance(el, dict) and "id" in el)
    return ids


def _normalize_geometry(params: dict) -> None:
    # Gemma picks the shape; the exact numbers are computed here, not trusted from the model.
    for key in list(params):
        if key not in ("points", "segments", "angles", "circles"):
            del params[key]
    points = {}
    for p in params.setdefault("points", []):
        p["x"] = min(max(_num(p.get("x"), 5), 0), 10)
        p["y"] = min(max(_num(p.get("y"), 5), 0), 10)
        points[p.get("id")] = p
    for seg in params.setdefault("segments", []):
        a, b = points.get(seg.get("from")), points.get(seg.get("to"))
        if a and b:
            seg["length"] = round(math.hypot(b["x"] - a["x"], b["y"] - a["y"]), 3)
    segments = {s.get("id"): s for s in params["segments"]}
    for ang in params.setdefault("angles", []):
        # Gemma sometimes names an angle's sides by segment id; use the segment's far endpoint.
        for side in ("from", "to"):
            seg = segments.get(ang.get(side))
            if seg and ang.get("vertex") in (seg.get("from"), seg.get("to")):
                ang[side] = seg["to"] if seg.get("from") == ang["vertex"] else seg["from"]
        v, a, b = (points.get(ang.get(k)) for k in ("vertex", "from", "to"))
        if v and a and b:
            t1 = math.atan2(a["y"] - v["y"], a["x"] - v["x"])
            t2 = math.atan2(b["y"] - v["y"], b["x"] - v["x"])
            deg = abs(math.degrees(t2 - t1)) % 360
            ang["degrees"] = round(min(deg, 360 - deg), 1)
    for circ in params.setdefault("circles", []):
        circ["radius"] = max(_num(circ.get("radius"), 1), 0.1)


def normalize_visual(proof: dict) -> dict:
    """Make Gemma's visual/animation data safe to draw: defaults, valid targets, exact numbers."""
    visual = proof.get("visual") if isinstance(proof.get("visual"), dict) else {}
    template = visual.get("template")
    params = visual.get("params") if isinstance(visual.get("params"), dict) else {}
    if template not in TEMPLATES:
        template, params = "flow", {}

    if template == "dominoes":
        params["base"] = int(_num(params.get("base"), 1))
        params["count"] = min(max(int(_num(params.get("count"), 6)), 2), 20)
        if params.get("breaks_at") is not None:
            params["breaks_at"] = int(_num(params["breaks_at"], params["base"]))
        params["domino_ids"] = [f"d{i}" for i in range(1, params["count"] + 1)]
    elif template == "geometry":
        _normalize_geometry(params)
    elif template == "number_line":
        lo, hi = _num(params.get("min"), 0), _num(params.get("max"), 10)
        params["min"], params["max"] = (lo, hi) if lo < hi else (hi - 1, lo + 1) if lo == hi else (hi, lo)
    elif template == "function_plot":
        params["x_min"], params["x_max"] = _num(params.get("x_min"), -5), _num(params.get("x_max"), 5)

    steps = proof.setdefault("steps", [])
    known = _element_ids(params) | {s.get("id") for s in steps}
    for step in steps:
        anim = step.get("animation") if isinstance(step.get("animation"), dict) else {}
        anim["action"] = anim.get("action") if anim.get("action") in ACTIONS else "highlight"
        anim["emphasis"] = anim.get("emphasis") if anim.get("emphasis") in EMPHASES else "none"
        if step.get("error"):
            anim["action"], anim["emphasis"] = "break", "shake"
        anim["duration"] = anim.get("duration") if anim.get("duration") in DURATION_MS else "medium"
        anim["duration_ms"] = DURATION_MS[anim["duration"]]
        targets = [str(t) for t in anim.get("targets", []) if str(t) in known]
        if template == "dominoes" and len(targets) >= 2:
            targets = params["domino_ids"]  # an inductive step knocks over the whole chain
        anim["targets"] = targets or ([step["id"]] if template == "flow" else [])
        anim["caption"] = anim.get("caption") or textwrap.shorten(step.get("statement", ""), 80)
        step["animation"] = anim

    proof["visual"] = {"template": template, "params": params}
    return proof


STEP_TYPES = {"assumption", "definition", "inference", "case", "conclusion"}
VERDICTS = {"valid", "valid_with_gaps", "invalid", "unclear"}
CONFIDENCES = {"high", "medium", "low"}
PLOT_NAMES = {"x", "sin", "cos", "tan", "exp", "log", "sqrt", "abs", "pi", "e"}


def _check_refs(problems: list[str], items: list, fields: tuple, valid: set, what: str) -> None:
    for el in items:
        if not isinstance(el, dict):
            problems.append(f"Every entry in {what} must be an object.")
            continue
        for f in fields:
            if el.get(f) not in valid:
                problems.append(f'{what} "{el.get("id")}" has {f}="{el.get(f)}", which is not a defined id.')


def find_problems(proof) -> list[str]:
    """List what's wrong with Gemma's answer, phrased so Gemma can fix it on a retry.

    Only checks things code can't repair itself; normalize_visual() handles the rest
    (durations, emphasis on error steps, clamping coordinates, ...).
    """
    if not isinstance(proof, dict):
        return ["The reply must be a single JSON object."]
    problems = []
    for key in ("title", "technique", "summary", "steps", "verdict", "verdict_explanation", "visual"):
        if not proof.get(key):
            problems.append(f'Top-level field "{key}" is missing or empty.')

    steps = proof.get("steps") if isinstance(proof.get("steps"), list) else []
    visual = proof.get("visual") if isinstance(proof.get("visual"), dict) else {}
    template = visual.get("template")
    params = visual.get("params") if isinstance(visual.get("params"), dict) else {}
    if template not in TEMPLATES:
        problems.append(f'visual.template "{template}" must be one of: {", ".join(sorted(TEMPLATES))}.')

    known = _element_ids(params)
    if template == "dominoes":
        known |= {f"d{i}" for i in range(1, int(_num(params.get("count"), 6)) + 1)}
    if template == "flow":
        known |= {s.get("id") for s in steps if isinstance(s, dict)}

    seen = set()
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            problems.append(f"Step {i} must be an object.")
            continue
        sid = s.get("id")
        if sid != f"S{i}":
            problems.append(f'Step {i} should have id "S{i}" (got "{sid}").')
        for field in ("statement", "justification"):
            if not s.get(field):
                problems.append(f'{sid}: "{field}" is empty.')
        if s.get("type") not in STEP_TYPES:
            problems.append(f'{sid}: type "{s.get("type")}" must be one of {sorted(STEP_TYPES)}.')
        if s.get("confidence") not in CONFIDENCES:
            problems.append(f'{sid}: confidence must be high, medium or low.')
        for dep in s.get("depends_on") or []:
            if dep not in seen:
                problems.append(f'{sid}: depends_on "{dep}" is not an earlier step.')
        if s.get("gap") and s.get("error"):
            problems.append(f"{sid}: a step can't be both a gap and an error; pick one.")
        if s.get("gap") and not s.get("gap_explanation"):
            problems.append(f"{sid}: gap is true but gap_explanation is empty.")
        if s.get("error") and not s.get("error_explanation"):
            problems.append(f"{sid}: error is true but error_explanation is empty.")

        anim = s.get("animation")
        if not isinstance(anim, dict):
            problems.append(f"{sid}: animation is missing.")
        else:
            targets = anim.get("targets") or []
            if not targets:
                problems.append(f"{sid}: animation.targets is empty; target at least one visual element.")
            unknown = [t for t in targets if t not in known]
            if unknown:
                problems.append(f"{sid}: animation targets {unknown} don't exist. Valid ids: {sorted(known)}.")
            if not anim.get("caption"):
                problems.append(f"{sid}: animation.caption is empty.")
        seen.add(sid)

    has_error = any(isinstance(s, dict) and s.get("error") for s in steps)
    has_gap = any(isinstance(s, dict) and s.get("gap") for s in steps)
    verdict = proof.get("verdict")
    if verdict not in VERDICTS:
        problems.append(f'verdict "{verdict}" must be one of {sorted(VERDICTS)}.')
    elif has_error and verdict != "invalid":
        problems.append('A step has error=true, so verdict must be "invalid".')
    elif not has_error and has_gap and verdict != "valid_with_gaps":
        problems.append('There are gaps but no errors, so verdict must be "valid_with_gaps".')
    elif not has_error and not has_gap and verdict in ("invalid", "valid_with_gaps"):
        problems.append(f'No step is marked as a gap or error, so verdict can\'t be "{verdict}". '
                        "Mark the responsible step, or change the verdict.")

    if template == "geometry":
        points = {p.get("id") for p in params.get("points", []) if isinstance(p, dict)}
        segment_ids = {s.get("id") for s in params.get("segments", []) if isinstance(s, dict)}
        if len(points) < 3:
            problems.append("geometry needs at least 3 points.")
        _check_refs(problems, params.get("segments", []), ("from", "to"), points, "segment")
        _check_refs(problems, params.get("angles", []), ("vertex",), points, "angle")
        _check_refs(problems, params.get("angles", []), ("from", "to"), points | segment_ids, "angle")
        _check_refs(problems, params.get("circles", []), ("center",), points, "circle")
        text = json.dumps(steps, ensure_ascii=False).lower() + str(proof.get("title", "")).lower()
        if not params.get("angles") and ("angle" in text or "°" in text or "degree" in text):
            problems.append('The proof talks about angles but geometry "angles" is empty. '
                            "Add every angle the proof mentions (vertex/from/to are point ids).")
    elif template == "sets":
        items = {it.get("id") for it in params.get("items", []) if isinstance(it, dict)}
        if not items or not params.get("groups"):
            problems.append('sets needs at least one item in "items" and one group in "groups".')
        for g in params.get("groups", []):
            missing = [m for m in (g.get("members") or []) if m not in items]
            if missing:
                problems.append(f'group "{g.get("id")}" has members {missing} that are not item ids.')
    elif template == "number_line":
        if not params.get("marks"):
            problems.append('number_line needs at least one entry in "marks".')
    elif template == "graph":
        nodes = {n.get("id") for n in params.get("nodes", []) if isinstance(n, dict)}
        if len(nodes) < 2:
            problems.append("graph needs at least 2 nodes.")
        _check_refs(problems, params.get("edges", []), ("from", "to"), nodes, "edge")
    elif template == "function_plot":
        if not params.get("functions"):
            problems.append('function_plot needs at least one entry in "functions".')
        for f in params.get("functions", []):
            try:
                names = set(compile(str(f.get("expr", "")), "<expr>", "eval").co_names)
            except SyntaxError:
                problems.append(f'function "{f.get("id")}" expr "{f.get("expr")}" is not valid Python.')
                continue
            if names - PLOT_NAMES:
                problems.append(f'function "{f.get("id")}" uses {sorted(names - PLOT_NAMES)}; '
                                f"only {sorted(PLOT_NAMES)} are allowed.")
    return problems


def parse_proof(
    text: str = "",
    image_bytes: bytes | None = None,
    mime_type: str | None = None,
    model: str = DEFAULT_MODEL,
    thinking: bool = False,
    api_key: str | None = None,
    max_retries: int = 2,
) -> dict:
    """Send a proof (text and/or image) to Gemma 4 and return the proof dict.

    The returned dict follows the schema in prompts.py (see sample_output.json),
    with the visual/animation data cleaned up by normalize_visual(). If Gemma's
    answer fails find_problems(), the problems are sent back and Gemma retries,
    up to max_retries times. The dict gets "attempts" (how many tries it took)
    and "warnings" (anything still wrong after the last try; usually []).
    Raises ParseError if no attempt produced usable JSON.
    """
    client = genai.Client(api_key=api_key) if api_key else genai.Client()

    contents = []
    if image_bytes:
        uploaded = client.files.upload(
            file=io.BytesIO(image_bytes), config={"mime_type": mime_type}
        )
        contents.append(uploaded)
    contents.append(text or "Analyze the proof shown in the image.")

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.2,
        response_mime_type="application/json",
        thinking_config=types.ThinkingConfig(
            thinking_level="high" if thinking else "minimal"
        ),
    )
    # A chat keeps the proof (and image) in context, so a retry only sends the problem list.
    chat = client.chats.create(model=model, config=config)
    response = chat.send_message(contents)

    best, best_problems, raw = None, None, ""
    for attempt in range(1, max_retries + 2):
        raw = response.text or ""
        try:
            proof = extract_json(raw)
            problems = find_problems(proof)
        except ValueError as e:  # JSONDecodeError is a ValueError
            proof, problems = None, [f"The reply was not valid JSON ({e}). Return only the JSON object."]
        if proof is not None and (best is None or len(problems) < len(best_problems)):
            best, best_problems = proof, problems
            best["attempts"] = attempt
        if not problems or attempt > max_retries:
            break
        response = chat.send_message(retry_prompt(problems))

    if best is None:
        raise ParseError("Couldn't read Gemma's answer as JSON", raw)
    best["warnings"] = best_problems
    return normalize_visual(best)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Parse a proof with Gemma 4")
    ap.add_argument("image", nargs="?", help="photo/screenshot of a proof (default: horses sample)")
    ap.add_argument("-o", "--out", help="write JSON to this file instead of printing")
    ap.add_argument("--thinking", action="store_true", help="slower, more careful (~2 min)")
    args = ap.parse_args()

    if args.image:
        path = Path(args.image)
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        proof = parse_proof(image_bytes=path.read_bytes(), mime_type=mime, thinking=args.thinking)
    else:
        proof = parse_proof(SAMPLES["All horses are the same color (broken)"], thinking=args.thinking)

    print(f"attempts: {proof['attempts']}, warnings: {proof['warnings'] or 'none'}", file=sys.stderr)
    out = json.dumps(proof, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(out)
