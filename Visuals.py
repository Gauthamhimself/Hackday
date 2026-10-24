"""
visuals.py -- ProofLens animated proof tree.

Public API (matches the team contract):
    draw_proof(proof: dict)

Run the demo on its own:
    streamlit run visuals.py

Expected `proof` format (parser.py should return something like this; every key
except "steps" is optional and common aliases are accepted):

    {
      "title": "sqrt(2) is irrational",
      "steps": [
        {
          "id": 1,
          "text": "Assume sqrt(2) = p/q in lowest terms.",
          "type": "assumption",          # assumption | implication | conclusion | gap
          "depends_on": [],              # ids of steps this one follows from
          "explanation": "Plain-English why/what.",
          "status": "ok",                # ok | gap | error
          "issue": ""                    # shown when status is gap/error
        },
        ...
      ]
    }
"""

import textwrap
import time

import streamlit as st

# --------------------------------------------------------------------------
# Styling
# --------------------------------------------------------------------------
KIND_COLORS = {
    "assumption": "#CFE3FF",   # blue
    "implication": "#D8F3DC",  # green
    "conclusion": "#E5D4FF",   # purple
    "gap": "#FFE0B2",          # orange
}
ERROR_COLOR = "#E53935"
GAP_COLOR = "#FB8C00"
DIM_FILL = "#F4F4F4"
DIM_TEXT = "#BDBDBD"
DIM_LINE = "#DDDDDD"

KIND_TAGS = {
    "assumption": "ASSUMPTION",
    "implication": "IMPLIES",
    "conclusion": "CONCLUSION",
    "gap": "GAP",
}


# --------------------------------------------------------------------------
# Normalisation helpers
# --------------------------------------------------------------------------
def _normalize(proof: dict) -> list:
    """Turn whatever the parser gave us into a clean, uniform list of steps."""
    raw_steps = (proof or {}).get("steps", []) or []
    steps = []
    for i, s in enumerate(raw_steps):
        sid = s.get("id", i + 1)
        kind = str(s.get("type") or s.get("kind") or "implication").lower()
        if kind not in KIND_COLORS:
            kind = "implication"

        if "depends_on" in s or "parents" in s:
            parents = s.get("depends_on", s.get("parents")) or []
        else:
            # No dependency info: chain to the previous step (except assumptions).
            parents = [] if (kind == "assumption" or i == 0) else [raw_steps[i - 1].get("id", i)]

        status = str(s.get("status") or "").lower()
        if s.get("error") or s.get("is_error"):
            status = "error"
        if status not in ("ok", "gap", "error"):
            status = "gap" if kind == "gap" else "ok"

        steps.append(
            {
                "id": sid,
                "text": str(s.get("text") or s.get("statement") or ""),
                "kind": kind,
                "parents": list(parents),
                "explanation": str(s.get("explanation") or ""),
                "status": status,
                "issue": str(s.get("issue") or s.get("note") or ""),
            }
        )
    return steps


def _esc(s: str) -> str:
    """Escape for a Graphviz double-quoted label (LaTeX backslashes survive)."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _label(step: dict, wrap: int) -> str:
    tag = KIND_TAGS.get(step["kind"], "")
    if step["status"] == "error":
        tag = "ERROR"
    head = f'{step["id"]}. {tag}'.strip()
    body = textwrap.wrap(step["text"], wrap) or [""]
    return "\\n".join([_esc(head)] + [_esc(line) for line in body])


def _is_tricky(step: dict, flash_gaps: bool) -> bool:
    return step["status"] == "error" or (flash_gaps and step["status"] == "gap")


# --------------------------------------------------------------------------
# Graphviz DOT builder (one call == one animation frame)
# --------------------------------------------------------------------------
def build_dot(steps, active, flash_on, opts) -> str:
    """
    active   : index of the step currently lit (steps[0..active] are revealed)
    flash_on : toggles the flashing state for tricky active steps
    """
    index = {s["id"]: i for i, s in enumerate(steps)}
    out = [
        "digraph proof {",
        f'  rankdir={opts["rankdir"]}; bgcolor="transparent"; nodesep=0.45; ranksep=0.6;',
        f'  node [shape=box, style="rounded,filled", fontname="Helvetica", '
        f'fontsize={opts["fontsize"]}, margin="0.22,0.14", penwidth=1.5];',
        '  edge [color="#8A8A8A", arrowsize=0.8, penwidth=1.4];',
    ]

    for i, s in enumerate(steps):
        revealed = i <= active
        if not revealed and not opts["show_future"]:
            continue

        label = _label(s, opts["wrap"])
        if not revealed:
            attrs = f'fillcolor="{DIM_FILL}", fontcolor="{DIM_TEXT}", color="{DIM_LINE}"'
        else:
            fill = KIND_COLORS[s["kind"]]
            line = "#555555"
            font = "#111111"
            style = "rounded,filled"
            pen = 1.5

            if s["status"] == "gap":
                fill, line, style = KIND_COLORS["gap"], GAP_COLOR, "rounded,filled,dashed"
            if s["status"] == "error":
                fill, line = "#FFCDD2", opts["error_color"]

            if i == active:
                pen = 4
                if _is_tricky(s, opts["flash_gaps"]):
                    hot = opts["error_color"] if s["status"] == "error" else GAP_COLOR
                    if flash_on:
                        fill, font, line = hot, "#FFFFFF", hot
                    else:
                        fill, line = "#FFFFFF", hot
                else:
                    line = "#111111"
            attrs = (
                f'fillcolor="{fill}", fontcolor="{font}", color="{line}", '
                f'style="{style}", penwidth={pen}'
            )
        out.append(f'  n{i} [label="{label}", {attrs}];')

    for i, s in enumerate(steps):
        for p in s["parents"]:
            if p not in index:
                continue
            j = index[p]
            if i > active and not opts["show_future"]:
                continue
            if i > active:
                out.append(f'  n{j} -> n{i} [color="{DIM_LINE}"];')
            elif i == active:
                out.append(f'  n{j} -> n{i} [color="#111111", penwidth=2.6];')
            else:
                out.append(f"  n{j} -> n{i};")
    out.append("}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def _render_info(steps, active, container):
    """Right-hand explanation panel for the active step."""
    s = steps[active]
    with container.container():
        st.markdown(f"**Step {s['id']} of {len(steps)}** &nbsp;·&nbsp; `{s['kind']}`")
        st.markdown(s["text"] or "_(no text)_")
        if s["explanation"]:
            st.info(s["explanation"])
        if s["status"] == "error":
            st.error(s["issue"] or "This step does not follow from the previous ones.")
        elif s["status"] == "gap":
            st.warning(s["issue"] or "A justification is skipped here.")
        if s["parents"]:
            st.caption("Follows from step(s): " + ", ".join(str(p) for p in s["parents"]))


def _frame(steps, active, flash_on, opts, graph_slot, info_slot):
    graph_slot.graphviz_chart(build_dot(steps, active, flash_on, opts), use_container_width=True)
    _render_info(steps, active, info_slot)


def draw_proof(proof: dict, key: str = "proof"):
    """Render the interactive animated proof tree inside a Streamlit app."""
    steps = _normalize(proof)
    if not steps:
        st.warning("No proof steps to draw yet.")
        return

    n = len(steps)
    title = (proof or {}).get("title")
    if title:
        st.subheader(title)

    # ---- User-tweakable parameters --------------------------------------
    with st.expander("Animation settings", expanded=False):
        c1, c2, c3 = st.columns(3)
        step_delay = c1.slider("Seconds per step", 0.3, 4.0, 1.2, 0.1, key=f"{key}_delay")
        flashes = c2.slider("Flashes on tricky step", 1, 8, 4, key=f"{key}_flashes")
        flash_delay = c3.slider("Flash speed (sec)", 0.1, 0.8, 0.25, 0.05, key=f"{key}_fdelay")

        c4, c5, c6 = st.columns(3)
        direction = c4.radio("Layout", ["Top → Bottom", "Left → Right"], key=f"{key}_dir")
        wrap = c5.slider("Text wrap (chars)", 15, 50, 28, key=f"{key}_wrap")
        fontsize = c6.slider("Font size", 9, 20, 12, key=f"{key}_font")

        c7, c8, c9 = st.columns(3)
        show_future = c7.checkbox("Show upcoming steps (dimmed)", True, key=f"{key}_future")
        flash_gaps = c8.checkbox("Also flash logical gaps", True, key=f"{key}_fgaps")
        error_color = c9.color_picker("Error colour", ERROR_COLOR, key=f"{key}_ecolor")

    opts = {
        "rankdir": "TB" if direction.startswith("Top") else "LR",
        "wrap": wrap,
        "fontsize": fontsize,
        "show_future": show_future,
        "flash_gaps": flash_gaps,
        "error_color": error_color,
    }

    # ---- Controls --------------------------------------------------------
    b1, b2, b3 = st.columns([1, 1, 4])
    play = b1.button("▶ Play", key=f"{key}_play", type="primary")
    show_all = b2.button("Show all", key=f"{key}_all")
    manual = (
        st.slider("Step", 1, n, n, key=f"{key}_step") - 1 if n > 1 else 0
    )

    # ---- Layout ----------------------------------------------------------
    left, right = st.columns([3, 2])
    graph_slot = left.empty()
    info_slot = right.empty()

    tricky_total = sum(1 for s in steps if s["status"] in ("error", "gap"))
    right.caption(f"{n} steps · {tricky_total} flagged")

    # ---- Animate or show static frame -------------------------------------
    if play:
        for i, s in enumerate(steps):
            if _is_tricky(s, opts["flash_gaps"]):
                for f in range(flashes * 2):
                    _frame(steps, i, f % 2 == 0, opts, graph_slot, info_slot)
                    time.sleep(flash_delay)
                _frame(steps, i, True, opts, graph_slot, info_slot)
                time.sleep(step_delay)
            else:
                _frame(steps, i, False, opts, graph_slot, info_slot)
                time.sleep(step_delay)
    else:
        active = n - 1 if show_all else manual
        _frame(steps, active, True, opts, graph_slot, info_slot)

    # ---- Handy export ------------------------------------------------------
    st.download_button(
        "Download Graphviz (.dot)",
        build_dot(steps, n - 1, True, opts),
        file_name="proof_tree.dot",
        key=f"{key}_dl",
    )


# --------------------------------------------------------------------------
# Demo: `streamlit run visuals.py`
# --------------------------------------------------------------------------
DEMO_PROOF = {
    "title": "Demo: sqrt(2) is irrational (with a planted error)",
    "steps": [
        {"id": 1, "type": "assumption", "text": "Assume sqrt(2) = p/q with p, q coprime integers.",
         "explanation": "Proof by contradiction: assume the opposite of what we want.", "depends_on": []},
        {"id": 2, "type": "implication", "text": "Then 2q^2 = p^2.", "depends_on": [1],
         "explanation": "Square both sides and multiply by q^2."},
        {"id": 3, "type": "implication", "text": "So p^2 is even, hence p is even.", "depends_on": [2],
         "status": "gap",
         "issue": "Skipped step: why does p^2 even imply p even? (If p were odd, p^2 would be odd.)",
         "explanation": "Uses the contrapositive: odd * odd = odd."},
        {"id": 4, "type": "implication", "text": "Write p = 2k, so q^2 = 2k^2 and q is even.", "depends_on": [3],
         "explanation": "Substitute back and repeat the evenness argument."},
        {"id": 5, "type": "implication", "text": "Therefore q = 3 since q^2 = 2k^2.", "depends_on": [4],
         "status": "error",
         "issue": "Invalid: nothing forces q to equal 3. Only that q is even.",
         "explanation": "This conclusion does not follow from the line above."},
        {"id": 6, "type": "conclusion", "text": "Both p and q are even, contradicting coprimality.", "depends_on": [3, 4],
         "explanation": "So the assumption fails and sqrt(2) is irrational."},
    ],
}

if __name__ == "__main__":
    import json

    st.set_page_config(page_title="ProofLens visuals demo", layout="wide")
    st.title("ProofLens · animated proof tree")

    uploaded = st.file_uploader("Upload a proof JSON (optional)", type="json")
    if uploaded is not None:
        try:
            data = json.load(uploaded)
            # Accept {"steps": [...]}, {"proof": {...}}, or a bare list of steps
            if isinstance(data, list):
                data = {"steps": data}
            elif "proof" in data and isinstance(data["proof"], dict):
                data = data["proof"]
            draw_proof(data, key="uploaded")
        except json.JSONDecodeError as e:
            st.error(f"That file isn't valid JSON: {e}")
    else:
        draw_proof(DEMO_PROOF)