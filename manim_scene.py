"""Turn a parsed proof into a ManimGL (3b1b/manim) scene description.

Owner: Tegh. parser.py calls build_manim_scene(proof) and stores the result in
proof["manim"]. Everything here is computed by code (positions, arcs, timings),
so the scene is exact even when Gemma's numbers aren't.

Shape of proof["manim"]:
    {
      "engine": "manimgl",
      "frame": {"width": 14.222, "height": 8.0},        # ManimGL default, origin at center
      "mobjects": [                                      # build these first, keyed by id
        {"id": "A", "class": "Dot", "position": [x, y, 0], "color": "BLUE_C", ...},
        {"id": "AB", "class": "Line", "start": [...], "end": [...], ...},
        {"id": "eq_S1", "class": "Text", "text": "a² = 2b²", "tex": "a^2 = 2b^2", ...},
      ],
      "timeline": [                                      # one entry per proof step, in order
        {"step": "S1", "caption": "...", "wait": 0.6, "camera": null,
         "plays": [                                      # each one = one self.play(...)
           {"lag_ratio": 0.15, "run_time": 0.8,          # lag_ratio > 0 -> LaggedStart
            "animations": [{"class": "ShowCreation", "target": "AB", "rate_func": "smooth"}]}
         ]}
      ]
    }

Animation classes used (all exist in manimlib): FadeIn, FadeOut, ShowCreation, Write,
FadeTransform, Indicate, FlashAround, Flash, FocusOn, WiggleOutThenIn, ApplyWave, Rotate.
Two pseudo-classes need one line each in the player:
    "SetColor" -> mob.animate.set_color(COLOR)
    "Rotate"   -> Rotate(mob, angle=kwargs["angle_degrees"] * DEG, about_point=kwargs["about_point"])
Text mobjects carry both "text" (Unicode, works without LaTeX) and "tex" (needs MiKTeX).
"""

import math
import textwrap

from readable import unicode_scripts as _unicode_scripts

FRAME_WIDTH, FRAME_HEIGHT = 14.222, 8.0
FIGURE_CENTER = (-3.2, 0.3)      # left part of the screen: the picture
EQUATION_POS = (3.7, 1.2)        # right part: the equation being rewritten
CAPTION_POS = (0.0, -2.9)        # bottom, above where the player shows subtitles
FIGURE_W, FIGURE_H = 6.4, 5.4

PALETTE = ["BLUE_C", "GREEN_C", "PURPLE_C", "TEAL_C", "GOLD_C"]
COLOR_ERROR, COLOR_GOOD, COLOR_FOCUS = "RED_C", "GREEN_C", "YELLOW_C"

# Shapes get drawn, text gets written, dots fade in.
DRAWN = {"Line", "Arc", "Circle", "Rectangle", "RoundedRectangle", "NumberLine", "Axes", "Graph", "Arrow"}
# Elements that only show up when a step points at them; everything else appears in step 1.
ACCENT_ROLES = {"angle", "angle_label", "interval", "plot_point", "mark"}

SAFE_MATH = {name: getattr(math, name) for name in ("sin", "cos", "tan", "exp", "log", "sqrt", "pi", "e")}
SAFE_MATH["abs"] = abs


def _p(x: float, y: float) -> list[float]:
    """A ManimGL point, clamped inside the frame."""
    x = min(max(x, -FRAME_WIDTH / 2 + 0.3), FRAME_WIDTH / 2 - 0.3)
    y = min(max(y, -FRAME_HEIGHT / 2 + 0.3), FRAME_HEIGHT / 2 - 0.3)
    return [round(x, 3), round(y, 3), 0.0]


def _unit(dx: float, dy: float) -> list[float]:
    n = math.hypot(dx, dy) or 1.0
    return [round(dx / n, 3), round(dy / n, 3), 0.0]


# ---------- layouts: template params -> mobjects ----------

def _layout_geometry(params: dict) -> list[dict]:
    cx, cy = FIGURE_CENTER
    pts = {p["id"]: p for p in params.get("points", [])}
    to_scene = lambda p: (cx + (p["x"] - 5) * 0.6, cy + (p["y"] - 5) * 0.55)
    centroid = (
        sum(to_scene(p)[0] for p in pts.values()) / max(len(pts), 1),
        sum(to_scene(p)[1] for p in pts.values()) / max(len(pts), 1),
    )
    mobs = []
    for seg in params.get("segments", []):
        a, b = pts.get(seg.get("from")), pts.get(seg.get("to"))
        if a and b:
            mobs.append({"id": seg["id"], "class": "Line", "role": "segment",
                         "start": _p(*to_scene(a)), "end": _p(*to_scene(b)), "color": "WHITE", "stroke_width": 4})
    for circ in params.get("circles", []):
        c = pts.get(circ.get("center"))
        if c:
            mobs.append({"id": circ["id"], "class": "Circle", "role": "circle", "position": _p(*to_scene(c)),
                         "radius": round(circ["radius"] * 0.58, 3), "color": "TEAL_C"})
    for pid, p in pts.items():
        x, y = to_scene(p)
        mobs.append({"id": pid, "class": "Dot", "role": "point", "position": _p(x, y), "color": "BLUE_C"})
        mobs.append({"id": f"{pid}_label", "class": "Text", "role": "label", "text": pid, "font_size": 30,
                     "next_to": pid, "direction": _unit(x - centroid[0], y - centroid[1]), "color": "BLUE_A"})
    for i, ang in enumerate(params.get("angles", [])):
        v, a, b = (pts.get(ang.get(k)) for k in ("vertex", "from", "to"))
        if not (v and a and b):
            continue
        (vx, vy), (ax, ay), (bx, by) = to_scene(v), to_scene(a), to_scene(b)
        t1, t2 = math.atan2(ay - vy, ax - vx), math.atan2(by - vy, bx - vx)
        sweep = (t2 - t1 + math.pi) % (2 * math.pi) - math.pi   # shortest signed turn
        radius = 0.42 + 0.08 * (i % 3)                          # nested angles at one vertex don't overlap
        color = PALETTE[i % len(PALETTE)]
        mobs.append({"id": ang["id"], "class": "Arc", "role": "angle", "arc_center": _p(vx, vy),
                     "radius": radius, "start_angle": round(t1, 4), "angle": round(sweep, 4),
                     "color": color, "stroke_width": 5})
        mid = t1 + sweep / 2
        mobs.append({"id": f"{ang['id']}_label", "class": "Text", "role": "angle_label",
                     "text": f"{ang.get('degrees', round(abs(math.degrees(sweep)), 1))}°", "font_size": 22,
                     "position": _p(vx + (radius + 0.35) * math.cos(mid), vy + (radius + 0.35) * math.sin(mid)),
                     "color": color})
    return mobs


def _layout_dominoes(params: dict) -> list[dict]:
    cx, cy = FIGURE_CENTER
    ids, base = params.get("domino_ids", []), params.get("base", 1)
    gap, w, h = min(0.95, FIGURE_W / max(len(ids), 1)), 0.28, 1.5
    x0 = cx - gap * (len(ids) - 1) / 2
    mobs = []
    for i, did in enumerate(ids):
        x = x0 + i * gap
        broken = params.get("breaks_at") == base + i
        mobs.append({"id": did, "class": "Rectangle", "role": "domino", "position": _p(x, cy),
                     "width": w, "height": h, "color": COLOR_ERROR if broken else PALETTE[i % 2],
                     "fill_opacity": 0.85,
                     # falling = rotate clockwise about the bottom-right corner
                     "pivot": _p(x + w / 2, cy - h / 2)})
        mobs.append({"id": f"{did}_label", "class": "Text", "role": "label", "text": f"n={base + i}",
                     "font_size": 22, "position": _p(x, cy - h / 2 - 0.35), "color": "GREY_A"})
    return mobs


def _layout_sets(params: dict) -> list[dict]:
    cx, cy = FIGURE_CENTER
    groups = params.get("groups", [])[:3]
    radius = 1.55
    offsets = {1: [0.0], 2: [-0.95, 0.95], 3: [-1.5, 0.0, 1.5]}.get(len(groups), [0.0])
    centers = {g["id"]: (cx + dx, cy + (0.35 if len(groups) == 3 and i == 1 else 0))
               for i, (g, dx) in enumerate(zip(groups, offsets))}
    mobs = []
    for i, g in enumerate(groups):
        gx, gy = centers[g["id"]]
        mobs.append({"id": g["id"], "class": "Circle", "role": "group", "position": _p(gx, gy), "radius": radius,
                     "color": PALETTE[i % len(PALETTE)], "fill_opacity": 0.15})
        mobs.append({"id": f"{g['id']}_label", "class": "Text", "role": "label", "text": g.get("label", g["id"]),
                     "font_size": 24, "position": _p(gx, gy + radius + 0.3), "color": PALETTE[i % len(PALETTE)]})
    # An item sits at the average center of the groups it belongs to, so shared items land in the overlap.
    slots: dict[tuple, int] = {}
    for item in params.get("items", []):
        home = [centers[g["id"]] for g in groups if item["id"] in (g.get("members") or [])]
        if home:
            x, y = sum(c[0] for c in home) / len(home), sum(c[1] for c in home) / len(home)
        else:
            x, y = cx, cy - radius - 0.9
        key = (round(x, 2), round(y, 2))
        k = slots[key] = slots.get(key, -1) + 1
        y += (0.5 * ((k + 1) // 2)) * (1 if k % 2 else -1)       # stack 0, +0.5, -0.5, +1.0, ...
        mobs.append({"id": item["id"], "class": "Dot", "role": "item", "position": _p(x - 0.25, y),
                     "radius": 0.11, "color": "WHITE"})
        mobs.append({"id": f"{item['id']}_label", "class": "Text", "role": "label",
                     "text": item.get("label", item["id"]), "font_size": 24,
                     "next_to": item["id"], "direction": [1.0, 0.0, 0.0], "color": "WHITE"})
    return mobs


def _layout_number_line(params: dict) -> list[dict]:
    cx, cy = FIGURE_CENTER
    lo, hi = params["min"], params["max"]
    to_x = lambda v: cx - FIGURE_W / 2 + (v - lo) / (hi - lo) * FIGURE_W
    step = max(round((hi - lo) / 8, 1), 0.1)
    mobs = [{"id": "number_line", "class": "NumberLine", "role": "axis", "x_range": [lo, hi, step],
             "width": FIGURE_W, "position": _p(cx, cy), "include_numbers": True, "color": "GREY_A"}]
    for m in params.get("marks", []):
        v = min(max(float(m.get("value", lo)), lo), hi)
        mobs.append({"id": m["id"], "class": "Dot", "role": "mark", "position": _p(to_x(v), cy),
                     "value": v, "color": COLOR_FOCUS})
        mobs.append({"id": f"{m['id']}_label", "class": "Text", "role": "mark", "text": m.get("label", ""),
                     "font_size": 26, "position": _p(to_x(v), cy + 0.55), "color": COLOR_FOCUS})
    for i, iv in enumerate(params.get("intervals", [])):
        a = min(max(float(iv.get("from", lo)), lo), hi)
        b = min(max(float(iv.get("to", hi)), lo), hi)
        y = cy - 0.45 - 0.3 * i
        mobs.append({"id": iv["id"], "class": "Line", "role": "interval", "start": _p(to_x(a), y),
                     "end": _p(to_x(b), y), "color": PALETTE[i % len(PALETTE)], "stroke_width": 8})
    return mobs


def _layout_graph(params: dict) -> list[dict]:
    cx, cy = FIGURE_CENTER
    nodes = params.get("nodes", [])
    r = 2.1
    pos = {n["id"]: (cx + r * math.cos(math.pi / 2 + 2 * math.pi * i / max(len(nodes), 1)),
                     cy + r * math.sin(math.pi / 2 + 2 * math.pi * i / max(len(nodes), 1)))
           for i, n in enumerate(nodes)}
    mobs = []
    for e in params.get("edges", []):
        a, b = pos.get(e.get("from")), pos.get(e.get("to"))
        if a and b:
            mobs.append({"id": e["id"], "class": "Line", "role": "edge", "start": _p(*a), "end": _p(*b),
                         "color": "GREY_B", "stroke_width": 4})
    for n in nodes:
        mobs.append({"id": n["id"], "class": "Circle", "role": "node", "position": _p(*pos[n["id"]]),
                     "radius": 0.32, "color": "BLUE_C", "fill_opacity": 0.9})
        mobs.append({"id": f"{n['id']}_label", "class": "Text", "role": "label", "text": n.get("label", n["id"]),
                     "font_size": 26, "position": _p(*pos[n["id"]]), "color": "WHITE"})
    return mobs


def _safe_eval(expr: str, x: float) -> float | None:
    try:
        y = eval(compile(expr, "<expr>", "eval"), {"__builtins__": {}}, {**SAFE_MATH, "x": x})
        return float(y) if math.isfinite(y) else None
    except Exception:
        return None


def _layout_function_plot(params: dict) -> list[dict]:
    cx, cy = FIGURE_CENTER
    x_min, x_max = params["x_min"], params["x_max"]
    if x_min >= x_max:
        x_min, x_max = x_max - 1, x_min + 1
    funcs = params.get("functions", [])
    xs = [x_min + (x_max - x_min) * i / 60 for i in range(61)]
    ys = [y for f in funcs for y in (_safe_eval(str(f.get("expr", "")), x) for x in xs) if y is not None]
    y_min, y_max = (min(ys), max(ys)) if ys else (-5.0, 5.0)
    pad = (y_max - y_min) * 0.15 or 1.0
    y_min, y_max = math.floor(y_min - pad), math.ceil(y_max + pad)
    to_scene = lambda x, y: (cx - FIGURE_W / 2 + (x - x_min) / (x_max - x_min) * FIGURE_W,
                             cy - FIGURE_H / 2 + (y - y_min) / (y_max - y_min) * FIGURE_H)
    mobs = [{"id": "axes", "class": "Axes", "role": "axis", "position": _p(cx, cy),
             "x_range": [x_min, x_max, max(round((x_max - x_min) / 6, 1), 0.1)],
             "y_range": [y_min, y_max, max(round((y_max - y_min) / 6, 1), 0.1)],
             "width": FIGURE_W, "height": FIGURE_H, "color": "GREY_A"}]
    for i, f in enumerate(funcs):
        mobs.append({"id": f["id"], "class": "Graph", "role": "function", "axes": "axes",
                     "expr": str(f.get("expr", "x")), "x_range": [x_min, x_max],
                     "label": f.get("label", ""), "color": PALETTE[i % len(PALETTE)], "stroke_width": 5})
    first = str(funcs[0].get("expr", "x")) if funcs else "x"
    for pt in params.get("points", []):
        x = float(pt.get("x", 0))
        y = _safe_eval(first, x)
        if y is None:
            continue
        mobs.append({"id": pt["id"], "class": "Dot", "role": "plot_point", "coords": [x, round(y, 4)],
                     "position": _p(*to_scene(x, y)), "color": COLOR_FOCUS})
        mobs.append({"id": f"{pt['id']}_label", "class": "Text", "role": "plot_point", "text": pt.get("label", ""),
                     "font_size": 24, "next_to": pt["id"], "direction": [0.0, 1.0, 0.0], "color": COLOR_FOCUS})
    return mobs


def _layout_flow(steps: list[dict]) -> list[dict]:
    """The proof tree itself: rows by depth, arrows from each step to the steps that use it."""
    cx, cy = FIGURE_CENTER
    depth: dict[str, int] = {}
    for s in steps:
        depth[s["id"]] = 1 + max((depth.get(d, 0) for d in s.get("depends_on") or []), default=-1)
    rows: dict[int, list[str]] = {}
    for sid, d in depth.items():
        rows.setdefault(d, []).append(sid)
    n_rows = max(rows) + 1 if rows else 1
    pos = {}
    for d, ids in rows.items():
        y = cy + FIGURE_H / 2 - 0.5 - d * (FIGURE_H - 1) / max(n_rows - 1, 1)
        for i, sid in enumerate(ids):
            pos[sid] = (cx + (i - (len(ids) - 1) / 2) * min(1.9, FIGURE_W / len(ids)), y)
    by_id = {s["id"]: s for s in steps}
    mobs = []
    for s in steps:
        for dep in s.get("depends_on") or []:
            if dep in pos:
                mobs.append({"id": f"edge_{dep}_{s['id']}", "class": "Arrow", "role": "edge",
                             "start": _p(*pos[dep]), "end": _p(*pos[s["id"]]), "buff": 0.35, "color": "GREY_B"})
    for sid, (x, y) in pos.items():
        s = by_id[sid]
        color = COLOR_ERROR if s.get("error") else "ORANGE" if s.get("gap") else "BLUE_C"
        mobs.append({"id": sid, "class": "RoundedRectangle", "role": "node", "position": _p(x, y),
                     "width": 1.3, "height": 0.6, "corner_radius": 0.15, "color": color, "fill_opacity": 0.25})
        mobs.append({"id": f"{sid}_label", "class": "Text", "role": "label", "text": sid, "font_size": 24,
                     "position": _p(x, y), "color": "WHITE"})
    return mobs


LAYOUTS = {
    "geometry": _layout_geometry,
    "dominoes": _layout_dominoes,
    "sets": _layout_sets,
    "number_line": _layout_number_line,
    "graph": _layout_graph,
    "function_plot": _layout_function_plot,
}


# ---------- timeline: step animations -> manim play calls ----------

def _show(mob: dict, run_time: float) -> dict:
    cls = "ShowCreation" if mob["class"] in DRAWN else "Write" if mob["class"] == "Text" else "FadeIn"
    return {"class": cls, "target": mob["id"], "rate_func": "smooth", "run_time": run_time}


def _emphasize(action: str, emphasis: str, target: str, mob: dict, run_time: float) -> list[dict]:
    anims = []
    if action == "break" or emphasis == "shake":
        anims += [{"class": "SetColor", "target": target, "color": COLOR_ERROR, "rate_func": "smooth"},
                  {"class": "WiggleOutThenIn", "target": target, "rate_func": "linear"},
                  {"class": "Flash", "target": target, "color": COLOR_ERROR, "rate_func": "smooth"}]
    elif action == "zoom":
        anims.append({"class": "FocusOn", "target": target, "rate_func": "smooth"})
    elif action == "move":
        anims.append({"class": "ApplyWave", "target": target, "rate_func": "smooth"})
    elif action == "connect":
        anims.append({"class": "FlashAround", "target": target, "color": "TEAL_C", "rate_func": "smooth"})
    elif action == "transform":
        anims.append({"class": "Indicate", "target": target, "color": COLOR_GOOD, "scale_factor": 1.25,
                      "rate_func": "there_and_back"})
    elif action == "highlight" or emphasis == "pulse":
        anims.append({"class": "Indicate", "target": target, "color": COLOR_FOCUS, "rate_func": "there_and_back"})
    if emphasis == "glow" and action != "break":
        anims.append({"class": "FlashAround", "target": target, "color": COLOR_FOCUS, "rate_func": "smooth"})
    for a in anims:
        a["run_time"] = run_time
    return anims


def build_manim_scene(proof: dict) -> dict:
    """Build the ManimGL scene description for a normalized proof (see module docstring)."""
    steps = proof.get("steps", [])
    visual = proof.get("visual", {})
    template = visual.get("template", "flow")
    layout = LAYOUTS.get(template)
    figure = layout(visual.get("params", {})) if layout else _layout_flow(steps)
    by_id = {m["id"]: m for m in figure}

    text_mobs = []
    for s in steps:
        disp = s.get("display") if isinstance(s.get("display"), dict) else {}
        statement = s.get("statement", "")
        # No display from Gemma: use the statement if it's short plain text, else a label like "Inference".
        fallback = (textwrap.shorten(statement, 32, placeholder=" …")
                    if "\\" not in statement and "$" not in statement else s.get("type", "step").title())
        text_mobs.append({"id": f"eq_{s['id']}", "class": "Text", "role": "equation",
                          "text": _unicode_scripts(disp.get("text") or fallback),
                          "tex": disp.get("tex", ""), "font_size": 40, "position": _p(*EQUATION_POS),
                          "color": COLOR_ERROR if s.get("error") else "WHITE"})
        text_mobs.append({"id": f"cap_{s['id']}", "class": "Text", "role": "caption",
                          "text": s.get("animation", {}).get("caption", ""), "font_size": 28,
                          "position": _p(*CAPTION_POS), "color": "GREY_A"})
    title = {"id": "title", "class": "Text", "role": "title", "text": proof.get("title", ""), "font_size": 36,
             "position": _p(0, FRAME_HEIGHT / 2 - 0.55), "color": "WHITE"}

    shown: set[str] = set()
    fallen: set[str] = set()
    timeline = []
    for i, s in enumerate(steps):
        anim = s.get("animation", {})
        rt = round(anim.get("duration_ms", 800) / 1000, 2)
        action, emphasis = anim.get("action", "highlight"), anim.get("emphasis", "none")
        targets = [t for t in anim.get("targets", []) if t in by_id]
        plays = []

        # 1. Bring things on screen: the title and the whole base figure in step 1, accents when first used.
        intro = []
        if i == 0:
            intro.append(_show(title, 0.8))
            intro += [_show(m, 0.8) for m in figure if m["role"] not in ACCENT_ROLES and m["id"] not in shown]
            shown.update(m["id"] for m in figure if m["role"] not in ACCENT_ROLES)
        for t in targets:
            for mid in (t, f"{t}_label"):
                if mid in by_id and mid not in shown:
                    intro.append(_show(by_id[mid], rt))
                    shown.add(mid)
        if intro:
            plays.append({"lag_ratio": 0.08 if i == 0 else 0.15, "run_time": max(rt, 1.2 if i == 0 else rt),
                          "animations": intro})

        # 2. The equation: written fresh, or morphed from the previous step's.
        eq = f"eq_{s['id']}"
        caption_anims = [{"class": "FadeIn", "target": f"cap_{s['id']}", "rate_func": "smooth"}]
        if i == 0:
            eq_anims = [{"class": "Write", "target": eq, "rate_func": "linear"}]
        else:
            prev = steps[i - 1]["id"]
            eq_anims = [{"class": "FadeTransform", "source": f"eq_{prev}", "target": eq, "rate_func": "smooth"}]
            caption_anims.insert(0, {"class": "FadeOut", "target": f"cap_{prev}", "rate_func": "smooth"})
        for a in eq_anims + caption_anims:
            a["run_time"] = rt
        plays.append({"lag_ratio": 0, "run_time": rt, "animations": eq_anims + caption_anims})

        # 3. The step's own move on the figure.
        if template == "dominoes" and len(targets) >= 2 and action != "break" and i > 0:
            # Inductive step: dominoes fall one after another (stopping before a broken one).
            # Each domino falls only once; later steps just highlight the fallen chain.
            breaks_at = visual.get("params", {}).get("breaks_at")
            base = visual.get("params", {}).get("base", 1)
            falling = [t for t in targets if t not in fallen
                       and (breaks_at is None or int(t[1:]) - 1 + base < breaks_at)]
            fallen.update(falling)
            if falling:
                plays.append({"lag_ratio": 0.35, "run_time": max(rt * 2, 2.0), "animations": [
                    {"class": "Rotate", "target": t, "angle_degrees": -78, "about_point": by_id[t]["pivot"],
                     "rate_func": "rush_into"} for t in falling]})
            else:
                plays.append({"lag_ratio": 0.1, "run_time": rt, "animations": [
                    {"class": "Indicate", "target": t, "color": COLOR_GOOD, "scale_factor": 1.1,
                     "rate_func": "there_and_back", "run_time": rt} for t in targets]})
        else:
            fig_anims = [a for t in targets for a in _emphasize(action, emphasis, t, by_id[t], rt)]
            if s.get("error"):
                fig_anims += _emphasize("break", "shake", eq, {}, rt)
            if fig_anims:
                plays.append({"lag_ratio": 0.12 if len(targets) > 1 else 0, "run_time": rt,
                              "animations": fig_anims})

        if s.get("type") == "conclusion" and not s.get("error"):
            plays.append({"lag_ratio": 0, "run_time": 1.0, "animations": [
                {"class": "FlashAround", "target": eq, "color": COLOR_GOOD, "rate_func": "smooth", "run_time": 1.0}]})

        camera = None
        if action == "zoom" and targets:
            camera = {"move_to": by_id[targets[0]].get("position") or by_id[targets[0]].get("start"),
                      "height": FRAME_HEIGHT * 0.6, "run_time": rt}
        elif timeline and timeline[-1]["camera"] and not timeline[-1]["camera"].get("reset"):
            camera = {"reset": True, "run_time": rt}

        timeline.append({"step": s["id"], "caption": anim.get("caption", ""), "plays": plays, "camera": camera,
                         "wait": 1.2 if s.get("error") or s.get("type") == "conclusion" else 0.5})

    # Anything never pointed at still shows up by the end, so the final frame is complete.
    leftover = [_show(m, 0.6) for m in figure if m["id"] not in shown]
    if leftover and timeline:
        timeline[-1]["plays"].append({"lag_ratio": 0.1, "run_time": 1.0, "animations": leftover})
    # Never end zoomed in: the final frame should show the whole picture.
    if timeline and timeline[-1]["camera"] and not timeline[-1]["camera"].get("reset"):
        timeline.append({"step": "end", "caption": "", "plays": [], "wait": 0.5,
                         "camera": {"reset": True, "run_time": 0.8}})

    return {
        "engine": "manimgl",
        "frame": {"width": FRAME_WIDTH, "height": FRAME_HEIGHT},
        "template": template,
        "mobjects": [title] + figure + text_mobs,
        "timeline": timeline,
        "total_seconds": round(sum(p["run_time"] for t in timeline for p in t["plays"]) +
                               sum(t["wait"] for t in timeline), 1),
    }
