"""ProofLens - Streamlit frontend.

This file only handles presentation and integration. Model inference lives in
parser.py (`parse_proof(image_bytes, text) -> dict`) and graph rendering lives
in visuals.py (`draw_proof(proof)`).
"""

import hashlib
import io
import logging

import streamlit as st

try:
    from PIL import Image
except ImportError:  # Pillow is only used for the image preview/validation
    Image = None

logger = logging.getLogger("prooflens")

PLACEHOLDER = """Let n be an integer. Prove that if n is even, then n² is even.
Proof:
Assume n is even.
Then n = 2k for some integer k.
Therefore, n² = 4k² = 2(2k²).
Since 2k² is an integer, n² is even."""

SOURCES = ["Paste text", "Upload image", "Use camera"]

# --------------------------------------------------------------------------
# Optional integrations. A missing teammate module must not break the page.
# --------------------------------------------------------------------------
try:
    from parser import parse_proof
    PARSER_ERROR = None
except Exception as exc:  # noqa: BLE001
    parse_proof = None
    PARSER_ERROR = f"{type(exc).__name__}: {exc}"
    logger.warning("parser import failed: %s", PARSER_ERROR)

try:
    from visuals import draw_proof
    VISUALS_ERROR = None
except Exception as exc:  # noqa: BLE001
    draw_proof = None
    VISUALS_ERROR = f"{type(exc).__name__}: {exc}"
    logger.warning("visuals import failed: %s", VISUALS_ERROR)


# --------------------------------------------------------------------------
# Page setup and styling
# --------------------------------------------------------------------------
st.set_page_config(page_title="ProofLens", page_icon="🔎", layout="wide")

st.markdown(
    """
    <style>
    .block-container {max-width: 1100px; padding-top: 2.5rem;}
    .pl-title {font-size: 2.6rem; font-weight: 700; letter-spacing: -0.02em; margin: 0;}
    .pl-sub {font-size: 1.1rem; opacity: .7; margin: .15rem 0 1.5rem 0;}
    .pl-tag {display:inline-block; padding: 2px 10px; border-radius: 999px;
             font-size: .78rem; font-weight: 600; margin-right: .4rem;}
    .pl-valid {background:#e6f4ea; color:#1e6b34;}
    .pl-warn  {background:#fff4d6; color:#8a5a00;}
    .pl-error {background:#fde7e9; color:#a1202c;}
    .pl-neutral {background:#eceff3; color:#4a5565;}
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------
for key, default in {
    "result": None,        # dict returned by parse_proof
    "error": None,         # user-facing error message
    "analyzed_sig": None,  # signature of the input that produced `result`
    "analyzed_source": None,
    "analyzed_text": "",   # submitted text or "" for images
    "analyzed_image": None,  # submitted image bytes or None
    "run_id": 0,           # bumped on each analysis / reset to reset widgets
}.items():
    st.session_state.setdefault(key, default)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def signature(text, image_bytes):
    h = hashlib.sha256()
    h.update((text or "").encode("utf-8"))
    h.update(image_bytes or b"")
    return h.hexdigest()


def read_image(file):
    """Return validated image bytes from an uploaded/captured file, or None."""
    if file is None:
        return None
    data = file.getvalue()
    if Image is not None:
        Image.open(io.BytesIO(data)).verify()  # raises on corrupt/non-image data
    return data


def first(d, keys, default=None):
    """First non-empty value among `keys` in dict `d`."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def as_text(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "\n".join(f"- {as_text(x)}" for x in v)
    if isinstance(v, dict):
        return "\n".join(f"**{k}:** {as_text(x)}" for k, x in v.items())
    return str(v)


STATUS_MAP = {
    "valid": ("Valid", "pl-valid"), "ok": ("Valid", "pl-valid"),
    "correct": ("Valid", "pl-valid"), "true": ("Valid", "pl-valid"),
    "supported": ("Valid", "pl-valid"),
    "unsupported": ("Needs justification", "pl-warn"),
    "gap": ("Needs justification", "pl-warn"),
    "warning": ("Needs justification", "pl-warn"),
    "unclear": ("Needs justification", "pl-warn"),
    "missing_justification": ("Needs justification", "pl-warn"),
    "error": ("Likely error", "pl-error"), "invalid": ("Likely error", "pl-error"),
    "incorrect": ("Likely error", "pl-error"), "false": ("Likely error", "pl-error"),
    "wrong": ("Likely error", "pl-error"),
}


def step_status(step):
    """Return (label, css_class) or None if the parser gave no status."""
    raw = first(step, ["status", "validity", "verdict", "is_valid", "valid"])
    if raw is None:
        return None
    if isinstance(raw, bool):
        raw = "valid" if raw else "invalid"
    key = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    return STATUS_MAP.get(key, (str(raw), "pl-neutral"))


def tag(label, css):
    return f'<span class="pl-tag {css}">{label}</span>'


def get_steps(proof):
    steps = first(proof, ["steps", "proof_steps", "logical_steps"], [])
    return steps if isinstance(steps, list) else []


def render_step(i, step):
    if not isinstance(step, dict):  # parser gave plain strings
        step = {"statement": as_text(step)}
    number = first(step, ["id", "step", "number", "index", "step_number"], i)
    statement = first(step, ["statement", "text", "claim", "content", "math"])
    explanation = first(step, ["explanation", "plain_english", "plain_explanation", "description"])
    justification = first(step, ["justification", "reason", "rule", "theorem"])
    deps = first(step, ["depends_on", "dependencies", "dependency", "from", "uses", "parents"])
    issue = first(step, ["issue", "problem", "gap", "error", "warning", "note"])
    status = step_status(step)

    known = {"id", "step", "number", "index", "step_number", "statement", "text", "claim",
             "content", "math", "explanation", "plain_english", "plain_explanation",
             "description", "justification", "reason", "rule", "theorem", "depends_on",
             "dependencies", "dependency", "from", "uses", "parents", "status", "validity",
             "verdict", "is_valid", "valid", "issue", "problem", "gap", "error", "warning", "note"}
    extras = {k: v for k, v in step.items() if k not in known and v not in (None, "", [], {})}

    head = f"Step {number}"
    if statement:
        head += f": {str(statement).splitlines()[0][:90]}"
    expanded = bool(status and status[1] in ("pl-warn", "pl-error"))
    with st.expander(head, expanded=expanded or i <= 1):
        if status:
            st.markdown(tag(*status), unsafe_allow_html=True)
        if statement:
            st.code(str(statement), language="text")
        if explanation:
            st.markdown(f"**In plain English:** {as_text(explanation)}")
        if justification:
            st.markdown(f"**Justification:** {as_text(justification)}")
        if deps:
            d = deps if isinstance(deps, (list, tuple)) else [deps]
            st.markdown("**Follows from step(s):** " + ", ".join(str(x) for x in d))
        elif deps is not None and i > 1:
            pass
        if issue:
            box = st.error if status and status[1] == "pl-error" else st.warning
            box(as_text(issue))
        for k, v in extras.items():
            st.markdown(f"**{str(k).replace('_', ' ').capitalize()}:** {as_text(v)}")


def render_visual(proof):
    st.subheader("Visual proof tree")
    if draw_proof is None:
        st.info("The proof tree is unavailable because the visualization module could not be loaded.")
        logger.warning("visuals unavailable: %s", VISUALS_ERROR)
        return
    with st.expander("How to read this tree"):
        st.markdown(
            "Each node is a statement in the proof. Arrows point from the statements a step "
            "relies on to the step that uses them, ending at the conclusion."
        )
    try:
        fig = draw_proof(proof)
        if fig is None:
            return  # draw_proof rendered into the page itself
        if hasattr(fig, "source") or isinstance(fig, str):
            st.graphviz_chart(fig, use_container_width=True)  # graphviz object or DOT text
        elif hasattr(fig, "savefig"):
            st.pyplot(fig)
        else:
            st.write(fig)
    except Exception as exc:  # noqa: BLE001
        logger.exception("draw_proof failed")
        st.warning("The proof tree couldn't be drawn for this proof, but the written analysis above is still available.")
        if "dot" in str(exc).lower() or "graphviz" in str(exc).lower():
            st.caption("Graphviz may not be installed on this machine.")


def render_results(proof):
    st.success("Analysis complete.")

    stale = st.session_state.analyzed_sig != st.session_state.get("current_sig")
    if stale:
        st.info("The input has changed since this analysis. Click **Analyze Proof** to update it.")

    steps = get_steps(proof)
    known_top = {"steps", "proof_steps", "logical_steps"}

    # A. overview
    st.subheader("Proof overview")
    objective = first(proof, ["objective", "goal", "summary", "statement", "theorem", "claim"])
    conclusion = first(proof, ["conclusion", "result"])
    extracted = first(proof, ["proof_text", "extracted_text", "text", "transcription", "proof", "raw_text"])
    if extracted is None and st.session_state.analyzed_text:
        extracted = st.session_state.analyzed_text
    c1, c2 = st.columns([3, 2])
    with c1:
        if extracted:
            st.markdown("**Proof**")
            st.code(as_text(extracted), language="text", wrap_lines=True)
        elif st.session_state.analyzed_image:
            st.image(st.session_state.analyzed_image, caption="Submitted image")
    with c2:
        if objective:
            st.markdown(f"**Objective**\n\n{as_text(objective)}")
        if conclusion:
            st.markdown(f"**Conclusion**\n\n{as_text(conclusion)}")
    if st.session_state.analyzed_image and extracted:
        with st.expander("Submitted image"):
            st.image(st.session_state.analyzed_image)

    # C (summary part). assumptions + issues, shown before steps for demo flow
    assumptions = first(proof, ["assumptions", "assumption"])
    gaps = first(proof, ["gaps", "logical_gaps", "issues", "errors", "warnings"])
    if assumptions or gaps:
        st.subheader("Assumptions and logical gaps")
        a, g = st.columns(2)
        with a:
            if assumptions:
                st.markdown("**Assumptions**")
                for item in assumptions if isinstance(assumptions, list) else [assumptions]:
                    st.markdown(f"- {as_text(item)}")
        with g:
            if gaps:
                st.markdown("**Potential gaps** *(flagged by the analysis, not a formal verification)*")
                for item in gaps if isinstance(gaps, list) else [gaps]:
                    st.warning(as_text(item))

    # B. steps
    st.subheader("Step-by-step explanation")
    if steps:
        for i, step in enumerate(steps, start=1):
            render_step(i, step)
    else:
        st.info("The analysis did not return any individual steps.")

    # Anything else the parser returned that we don't know about
    handled = known_top | {"objective", "goal", "summary", "statement", "theorem", "claim",
                           "conclusion", "result", "proof_text", "extracted_text", "text",
                           "transcription", "proof", "raw_text", "assumptions", "assumption",
                           "gaps", "logical_gaps", "issues", "errors", "warnings"}
    extra = {k: v for k, v in proof.items() if k not in handled and v not in (None, "", [], {})}
    if extra:
        with st.expander("Additional details"):
            for k, v in extra.items():
                st.markdown(f"**{str(k).replace('_', ' ').capitalize()}:** {as_text(v)}")

    # D. visualization
    if steps:
        render_visual(proof)


def run_analysis(text, image_bytes, source):
    """Call the parser once and store the outcome in session state."""
    st.session_state.result = None
    st.session_state.error = None
    try:
        with st.spinner("Analyzing your proof. This can take a few seconds..."):
            result = parse_proof(image_bytes, text)
        if not isinstance(result, dict) or not result:
            raise ValueError("unexpected parser output")
        st.session_state.result = result
        st.session_state.analyzed_sig = signature(text, image_bytes)
        st.session_state.analyzed_source = source
        st.session_state.analyzed_text = text or ""
        st.session_state.analyzed_image = image_bytes
    except Exception:  # noqa: BLE001
        logger.exception("parse_proof failed")
        st.session_state.error = (
            "We couldn't analyze that proof. Check that the text or image is a readable "
            "proof and try again. If it keeps failing, the AI service may be unavailable."
        )


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------
st.markdown('<p class="pl-title">🔎 ProofLens</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="pl-sub">Turn confusing math proofs into step-by-step visual explanations.</p>',
    unsafe_allow_html=True,
)

if parse_proof is None:
    st.error(
        "The proof analysis module (`parser.py`) isn't available yet, so proofs can't be "
        "analyzed. Add it next to `app.py` and reload."
    )
    logger.error("parser unavailable: %s", PARSER_ERROR)

rid = st.session_state.run_id
source = st.radio("Input method", SOURCES, horizontal=True, label_visibility="collapsed",
                  key=f"source_{rid}")

text_input, image_bytes, input_error = "", None, None
if source == SOURCES[0]:
    text_input = st.text_area(
        "Proof", height=240, placeholder=PLACEHOLDER, key=f"text_{rid}",
        help="Plain text, Unicode symbols and LaTeX-style expressions are all fine.",
    )
else:
    try:
        if source == SOURCES[1]:
            f = st.file_uploader("Upload a photo or screenshot of the proof",
                                 type=["png", "jpg", "jpeg"], key=f"upload_{rid}")
        else:
            f = st.camera_input("Take a picture of the handwritten proof", key=f"camera_{rid}")
        image_bytes = read_image(f)
        if image_bytes:
            st.image(image_bytes, caption="Preview", width=420)
    except Exception:  # noqa: BLE001
        logger.exception("image read failed")
        input_error = "That file couldn't be read as an image. Please try a PNG or JPG."
        st.error(input_error)

st.session_state.current_sig = signature(text_input.strip(), image_bytes)

has_input = bool(text_input.strip()) if source == SOURCES[0] else bool(image_bytes)
col_a, col_b, _ = st.columns([1.3, 1.6, 4])
analyze = col_a.button("Analyze Proof", type="primary", use_container_width=True,
                       disabled=parse_proof is None)
start_over = col_b.button("Analyze a different proof", use_container_width=True,
                          disabled=st.session_state.result is None and st.session_state.error is None)

if start_over:
    st.session_state.update(result=None, error=None, analyzed_sig=None,
                            analyzed_text="", analyzed_image=None,
                            run_id=st.session_state.run_id + 1)
    st.rerun()

if analyze:
    if input_error:
        pass
    elif not has_input:
        st.session_state.result, st.session_state.error = None, None
        st.warning("Please paste a proof or provide an image first.")
    else:
        run_analysis(text_input.strip() or "", image_bytes, source)

st.divider()

if st.session_state.error:
    st.error(st.session_state.error)
elif st.session_state.result is not None:
    render_results(st.session_state.result)
elif not analyze:
    st.markdown("#### How it works")
    st.markdown(
        "1. Paste a proof, upload an image, or snap a photo of handwritten work.\n"
        "2. Click **Analyze Proof**.\n"
        "3. Read each step in plain English, spot unsupported leaps, and explore the proof tree."
    )
