"""Turn a ProofLens JSON (the "manim" block from parser.py) into a runnable ManimGL file.

    python json_to_manim.py sample_output.json            # writes scenes/sample_output_scene.py
    python json_to_manim.py sample_output.json --render   # ...and renders an .mp4 with manimgl

From Python:
    from json_to_manim import generate_manim_code, render_proof
    code = generate_manim_code(proof)          # the .py source as a string
    mp4 = render_proof(proof, "my_proof")      # writes + renders, returns the .mp4 path
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from manim_scene import build_manim_scene

HERE = Path(__file__).resolve().parent
SCENE_DIR = HERE / "scenes"
VIDEO_DIR = HERE / "videos"
SCENE_NAME = "ProofScene"

# Names that may appear in Gemma's function_plot expressions (also enforced in parser.find_problems).
PLOT_NAMES = {"x", "sin", "cos", "tan", "exp", "log", "sqrt", "abs", "pi", "e"}
# These create their own temporary mobjects, so they can share a play() with anything else.
NON_MUTATING = {"Flash", "FlashAround", "FocusOn"}
# Windows fonts with full math-symbol coverage (∠ √ ² Δ ∥ ≤ ∈ ⇒). On other systems Pango
# falls back to a default font, which may draw some symbols as boxes.
FONTS = {"equation": "Cambria Math", "angle_label": "Cambria Math"}
DEFAULT_FONT = "Segoe UI"
# Keep long text inside its area of the screen.
MAX_WIDTH = {"equation": 6.2, "caption": 12.8, "title": 12.0, "label": 2.6, "angle_label": 1.2, "mark": 2.0}


def _v(point) -> str:
    return f"np.array([{point[0]}, {point[1]}, {point[2] if len(point) > 2 else 0.0}])"


def _ref(mid: str) -> str:
    return f"m[{mid!r}]"


def _safe_expr(expr: str) -> bool:
    try:
        return set(compile(expr, "<expr>", "eval").co_names) <= PLOT_NAMES
    except SyntaxError:
        return False


def _mobject_lines(o: dict) -> list[str]:
    """Python lines that create one mobject and store it in m[id]."""
    cls, mid, color = o["class"], o["id"], o.get("color", "WHITE")
    ref = _ref(mid)
    lines = []
    if cls == "Dot":
        lines.append(f"{ref} = Dot({_v(o['position'])}, radius={o.get('radius', 0.08)}, color={color})")
    elif cls in ("Line", "Arrow"):
        extra = f", buff={o.get('buff', 0)}" if cls == "Arrow" else ""
        lines.append(f"{ref} = {cls}({_v(o['start'])}, {_v(o['end'])}{extra})")
        lines.append(f"{ref}.set_stroke({color}, width={o.get('stroke_width', 4)})")
    elif cls == "Arc":
        lines.append(f"{ref} = Arc(start_angle={o['start_angle']}, angle={o['angle']}, radius={o['radius']}, "
                     f"arc_center={_v(o['arc_center'])})")
        lines.append(f"{ref}.set_stroke({color}, width={o.get('stroke_width', 4)})")
    elif cls == "Circle":
        lines.append(f"{ref} = Circle(radius={o['radius']}).move_to({_v(o['position'])})")
        lines.append(f"{ref}.set_stroke({color}, width=4).set_fill({color}, opacity={o.get('fill_opacity', 0)})")
    elif cls in ("Rectangle", "RoundedRectangle"):
        corner = f", corner_radius={o['corner_radius']}" if cls == "RoundedRectangle" else ""
        lines.append(f"{ref} = {cls}(width={o['width']}, height={o['height']}{corner}).move_to({_v(o['position'])})")
        lines.append(f"{ref}.set_stroke({color}, width=3).set_fill({color}, opacity={o.get('fill_opacity', 0)})")
    elif cls == "NumberLine":
        lo, hi, step = o["x_range"]
        lines.append(f"{ref} = NumberLine(x_range=[{lo}, {hi}, {step}], width={o['width']}, include_numbers=False)")
        lines.append(f"{ref}.move_to({_v(o['position'])}).set_color({color})")
        # End labels as Text so no LaTeX is needed.
        for end, val in (("lo", lo), ("hi", hi)):
            lines.append(f"{ref}.add(Text({str(round(val, 3))!r}, font_size=22).set_color({color})"
                         f".next_to({ref}.n2p({val}), DOWN, buff=0.25))")
    elif cls == "Axes":
        lines.append(f"{ref} = Axes(x_range={o['x_range']}, y_range={o['y_range']}, "
                     f"width={o['width']}, height={o['height']})")
        lines.append(f"{ref}.move_to({_v(o['position'])}).set_color({color})")
    elif cls == "Graph":
        expr = o.get("expr", "x")
        if not _safe_expr(expr):
            expr = "0"
        fn = "f_" + re.sub(r"\W", "_", mid)
        lines.append(f"def {fn}(x):")
        lines.append(f"    try:")
        lines.append(f"        return float({expr})")
        lines.append(f"    except (ValueError, ZeroDivisionError, OverflowError):")
        lines.append(f"        return 0.0")
        lines.append(f"{ref} = {_ref(o['axes'])}.get_graph({fn}, x_range=[{o['x_range'][0]}, {o['x_range'][1]}])")
        lines.append(f"{ref}.set_stroke({color}, width={o.get('stroke_width', 4)})")
    elif cls == "Text":
        font = FONTS.get(o.get("role"), DEFAULT_FONT)
        lines.append(f"{ref} = Text({o.get('text', '')!r}, font={font!r}, "
                     f"font_size={o.get('font_size', 30)}).set_color({color})")
        width = MAX_WIDTH.get(o.get("role"))
        if width:
            lines.append(f"if {ref}.get_width() > {width}: {ref}.set_width({width})")
        if o.get("next_to"):
            lines.append(f"{ref}.next_to({_ref(o['next_to'])}, {_v(o.get('direction', [1, 0, 0]))}, buff=0.15)")
        else:
            lines.append(f"{ref}.move_to({_v(o['position'])})")
    else:
        lines.append(f"# unsupported mobject class {cls!r} for {mid!r}; using an empty placeholder")
        lines.append(f"{ref} = VGroup()")
    if cls == "Dot" and o.get("coords") and o.get("role") == "plot_point":
        # Snap plot points onto the real axes instead of the approximate position.
        x, y = o["coords"]
        lines.append(f"if 'axes' in m: {ref}.move_to(m['axes'].c2p({x}, {y}))")
    return lines


def _animation_expr(a: dict) -> str:
    cls, rate = a["class"], a.get("rate_func", "smooth")
    t = _ref(a["target"])
    color = a.get("color")
    if cls == "SetColor":
        return f"{t}.animate.set_color({color})"
    if cls == "Rotate":
        return (f"Rotate({t}, angle={a.get('angle_degrees', -78)} * PI / 180, "
                f"about_point={_v(a['about_point'])}, rate_func={rate})")
    if cls == "FadeTransform":
        return f"FadeTransform({_ref(a['source'])}, {t}, rate_func={rate})"
    if cls == "Flash":
        return f"Flash({t}.get_center(), color={color or 'YELLOW'})"
    if cls == "FocusOn":
        return f"FocusOn({t}.get_center())"
    if cls == "Indicate":
        scale = a.get("scale_factor", 1.2)
        return f"Indicate({t}, color={color or 'YELLOW'}, scale_factor={scale})"
    if cls == "FlashAround":
        return f"FlashAround({t}, color={color or 'YELLOW'})"
    if cls in ("WiggleOutThenIn", "ApplyWave"):
        return f"{cls}({t})"
    if cls in ("FadeIn", "FadeOut", "ShowCreation", "Write"):
        return f"{cls}({t}, rate_func={rate})"
    return f"Wait()  # unsupported animation {cls!r}"


def _split_conflicts(anims: list[dict]) -> list[list[dict]]:
    """Two animations changing the same mobject at once fight each other; run those one after another."""
    batches: list[list[dict]] = []
    for a in anims:
        touches = None if a["class"] in NON_MUTATING else a["target"]
        for batch in batches:
            if touches is None or all(b["target"] != touches or b["class"] in NON_MUTATING for b in batch):
                batch.append(a)
                break
        else:
            batches.append([a])
    return batches


def _play_lines(play: dict) -> list[str]:
    lines = []
    for batch in _split_conflicts(play["animations"]):
        exprs = [_animation_expr(a) for a in batch]
        rt = max(play.get("run_time", 1.0), 0.3)
        if play.get("lag_ratio", 0) > 0 and len(exprs) > 1:
            inner = ",\n                ".join(exprs)
            lines.append(f"self.play(LaggedStart(\n                {inner},\n"
                         f"                lag_ratio={play['lag_ratio']}), run_time={rt})")
        else:
            lines.append(f"self.play({', '.join(exprs)}, run_time={rt})")
    return lines


def animation_seconds(entry: dict) -> float:
    """How long a timeline entry animates (before its wait), exactly as _play_lines plays it."""
    seconds = entry["camera"].get("run_time", 0.8) if entry.get("camera") else 0.0
    for play in entry["plays"]:
        seconds += len(_split_conflicts(play["animations"])) * max(play.get("run_time", 1.0), 0.3)
    return seconds


def generate_manim_code(proof: dict, scene: dict | None = None) -> str:
    """Return the source of a ManimGL file with one Scene (ProofScene) animating the proof."""
    scene = scene or proof.get("manim") or build_manim_scene(proof)
    body = ["m = {}", ""]
    body.append("# ---- objects ----")
    for o in scene["mobjects"]:
        body += _mobject_lines(o)
    body.append("")
    for entry in scene["timeline"]:
        body.append(f"# ---- {entry['step']}: {entry.get('caption', '')[:70]} ----")
        cam = entry.get("camera")
        if cam and cam.get("reset"):
            body.append(f"self.play(self.frame.animate.move_to(ORIGIN).set_height(FRAME_HEIGHT), "
                        f"run_time={cam.get('run_time', 0.8)})")
        elif cam and cam.get("move_to"):
            body.append(f"self.play(self.frame.animate.move_to({_v(cam['move_to'])}).set_height({cam['height']}), "
                        f"run_time={cam.get('run_time', 0.8)})")
        for play in entry["plays"]:
            body += _play_lines(play)
        body.append(f"self.wait({entry.get('wait', 0.5)})")
        body.append("")
    body.append("self.wait(2)")

    indented = "\n".join(("        " + line) if line else "" for line in "\n".join(body).splitlines())
    title = proof.get("title", "Proof")
    return f'''"""ProofLens scene: {title}

Generated by json_to_manim.py. Render with:
    manimgl {{this_file}} {SCENE_NAME} -w
"""

from math import sin, cos, tan, exp, log, sqrt, pi, e

from manimlib import *


class {SCENE_NAME}(Scene):
    def construct(self):
{indented}
'''


def write_scene(proof: dict, name: str, scene: dict | None = None) -> Path:
    SCENE_DIR.mkdir(exist_ok=True)
    path = SCENE_DIR / f"{name}_scene.py"
    path.write_text(generate_manim_code(proof, scene), encoding="utf-8")
    return path


def render_proof(proof: dict, name: str = "proof", low_quality: bool = True, narrate: bool = True) -> Path:
    """Write the scene file and render it with manimgl. Returns the .mp4 path.

    With narrate=True the video gets a voice-over synced to each step, and the script with
    timestamps is saved as videos/<name>_narration.json. If the voice can't be made (e.g. no
    internet), the silent video is returned instead.
    """
    scene = proof.get("manim") or build_manim_scene(proof)
    narration = None
    if narrate:
        try:
            import narration as narr
            scene, narration = narr.prepare(proof, scene, animation_seconds, VIDEO_DIR / "audio", name)
        except Exception as exc:  # noqa: BLE001 -- a silent video beats no video
            print(f"narration skipped: {type(exc).__name__}: {exc}", file=sys.stderr)

    path = write_scene(proof, name, scene)
    VIDEO_DIR.mkdir(exist_ok=True)
    silent_name = f"{name}_silent" if narration else name
    cmd = [sys.executable, "-m", "manimlib", str(path), SCENE_NAME, "-w",
           "--video_dir", str(VIDEO_DIR), "--file_name", silent_name]
    if low_quality:
        cmd.append("-l")
    result = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace")
    mp4 = VIDEO_DIR / f"{silent_name}.mp4"
    if result.returncode != 0 or not mp4.exists():
        raise RuntimeError(f"manimgl failed (exit {result.returncode}):\n{result.stderr[-2000:]}")
    if not narration:
        return mp4

    narration["video"] = f"{name}.mp4"
    narr.save(narration, VIDEO_DIR / f"{name}_narration.json")
    (VIDEO_DIR / f"{name}.vtt").write_text(narr.to_vtt(narration), encoding="utf-8")
    try:
        return narr.mux(mp4, narration, VIDEO_DIR / "audio", VIDEO_DIR / f"{name}.mp4")
    except subprocess.CalledProcessError as exc:
        print(f"adding the voice failed: {exc.stderr[-500:]}", file=sys.stderr)
        return mp4


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Convert ProofLens JSON into a ManimGL scene")
    ap.add_argument("json_file", help="a parser output, e.g. sample_output.json")
    ap.add_argument("--render", action="store_true", help="also render an .mp4 with manimgl")
    ap.add_argument("--hq", action="store_true", help="render at full quality (slower)")
    ap.add_argument("--silent", action="store_true", help="skip the voice-over")
    args = ap.parse_args()

    proof = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    name = Path(args.json_file).stem
    if args.render:
        print("Rendered:", render_proof(proof, name, low_quality=not args.hq, narrate=not args.silent))
    else:
        print("Wrote:", write_scene(proof, name))
