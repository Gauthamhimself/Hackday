"""ProofLens - Streamlit frontend.

Presentation and integration only. Model inference lives in parser.py
(`parse_proof(text, image_bytes, mime_type, ...) -> dict`) and the proof tree in
visuals.py (`draw_proof(proof, key=...)`, which renders into the page itself).

History is kept in st.session_state, so it lasts for the browser session only.
"""

import hashlib
import io
import logging
import uuid
from datetime import datetime

import streamlit as st

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

logger = logging.getLogger("prooflens")

PLACEHOLDER = """Let n be an integer. Prove that if n is even, then n² is even.
Proof:
Assume n is even.
Then n = 2k for some integer k.
Therefore, n² = 4k² = 2(2k²).
Since 2k² is an integer, n² is even."""

SOURCES = ["Paste text", "Upload image", "Use camera", "Upload PDF"]
KIND_LABEL = {"text": "Text", "image": "Image", "camera": "Camera", "pdf": "PDF"}

MAX_PDF_MB = 15
MAX_PDF_PAGES = 100
MIN_TEXT_CHARS = 40       # below this a PDF is treated as scanned / image-only
MAX_HISTORY = 30

# --------------------------------------------------------------------------
# Teammates' modules. A missing one must not break the page.
# --------------------------------------------------------------------------
try:
    from parser import parse_proof
    PARSER_ERROR = None
except Exception as exc:  # noqa: BLE001
    parse_proof, PARSER_ERROR = None, f"{type(exc).__name__}: {exc}"

try:
    try:
        from visuals import draw_proof
    except ImportError:  # the file is currently committed as Visuals.py
        from Visuals import draw_proof
    VISUALS_ERROR = None
except Exception as exc:  # noqa: BLE001
    draw_proof, VISUALS_ERROR = None, f"{type(exc).__name__}: {exc}"

try:
    from json_to_manim import render_proof
    ANIMATION_ERROR = None
except Exception as exc:  # noqa: BLE001
    render_proof, ANIMATION_ERROR = None, f"{type(exc).__name__}: {exc}"

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
    section[data-testid="stSidebar"] .stButton button {text-align:left; justify-content:flex-start;}
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------
for key, default in {
    "history": [],          # newest first; each entry is a dict (see add_history)
    "active_id": None,      # id of the history entry being displayed
    "fresh_id": None,       # id of the entry produced by the latest analysis
    "error": None,          # user-facing error from the latest analysis
    "confirm_clear": False,
    "run_id": 0,            # bumped by "New Problem" to reset input widgets
}.items():
    st.session_state.setdefault(key, default)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def tag(label, css):
    return f'<span class="pl-tag {css}">{label}</span>'


def short(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def human_size(n):
    return f"{n / 1_048_576:.1f} MB" if n >= 1_048_576 else f"{max(n // 1024, 1)} KB"


def make_title(proof, text, filename):
    title = proof.get("title") if isinstance(proof, dict) else None
    if not title:
        lines = [l.strip() for l in (text or "").splitlines() if len(l.strip()) > 3]
        title = lines[0] if lines else (filename or "Untitled proof")
    return short(title, 60)


# --------------------------------------------------------------------------
# Image helpers
# --------------------------------------------------------------------------
def read_image(file):
    """Validated (bytes, mime) for an uploaded/captured image, or (None, None)."""
    if file is None:
        return None, None
    data = file.getvalue()
    if not data:
        raise ValueError("empty file")
    mime = file.type or "image/jpeg"
    if Image is not None:
        img = Image.open(io.BytesIO(data))
        img.verify()
        mime = Image.MIME.get(img.format, mime)
    return data, mime


# --------------------------------------------------------------------------
# PDF helpers
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=8)
def extract_pdf(data: bytes) -> dict:
    """Extract text per page. Cached by file content, so a new file never reuses old text.

    Returns {"pages": [str, ...], "error": str | None}. Never logs document content.
    """
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return {"pages": [], "error": "This PDF is password-protected. Remove the password and upload it again."}
        n = len(reader.pages)
        if n == 0:
            return {"pages": [], "error": "This PDF has no pages."}
        if n > MAX_PDF_PAGES:
            return {"pages": [], "error": f"This PDF has {n} pages; the limit is {MAX_PDF_PAGES}. "
                                          "Upload just the pages that contain the proof."}
        pages = []
        for page in reader.pages:
            try:
                pages.append((page.extract_text() or "").strip())
            except Exception:  # noqa: BLE001 - one bad page shouldn't sink the document
                pages.append("")
        return {"pages": pages, "error": None}
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF read failed: %s", type(exc).__name__)
        return {"pages": [], "error": "We couldn't read this PDF. It may be corrupted or in an unsupported format."}


@st.cache_data(show_spinner=False, max_entries=8)
def pdf_page_image(data: bytes, page_no: int):
    """Largest embedded image on a page as (bytes, mime), or None. Works for scanned PDFs."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        images = list(reader.pages[page_no - 1].images)
        if not images:
            return None
        raw = max(images, key=lambda i: len(i.data)).data
        img = Image.open(io.BytesIO(raw))
        if img.format in ("JPEG", "PNG"):
            return raw, Image.MIME[img.format]
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue(), "image/png"
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF page image failed: %s", type(exc).__name__)
        return None


# --------------------------------------------------------------------------
# Input section. Each function returns a submission dict or None.
# A submission: kind, text, image, mime, filename, meta.
# --------------------------------------------------------------------------
def text_input_ui(rid):
    text = st.text_area("Proof", height=240, placeholder=PLACEHOLDER, key=f"text_{rid}",
                        help="Plain text, Unicode symbols and LaTeX-style expressions are all fine.")
    return {"kind": "text", "text": text.strip(), "image": None, "mime": None,
            "filename": None, "meta": None}


def image_input_ui(rid, camera):
    try:
        if camera:
            f = st.camera_input("Take a picture of the handwritten proof", key=f"camera_{rid}")
        else:
            f = st.file_uploader("Upload a photo or screenshot of the proof",
                                 type=["png", "jpg", "jpeg"], key=f"upload_{rid}")
        data, mime = read_image(f)
    except Exception:  # noqa: BLE001
        st.error("That file couldn't be read as an image. Please try a PNG or JPG.")
        return None
    if not data:
        return None
    st.image(data, caption="Preview", width=420)
    return {"kind": "camera" if camera else "image", "text": "", "image": data, "mime": mime,
            "filename": None if camera else f.name, "meta": None}


def pdf_input_ui(rid):
    if PdfReader is None:
        st.error("PDF support needs the `pypdf` package. Run `pip install pypdf`.")
        return None
    f = st.file_uploader("Upload a PDF containing a proof", type=["pdf"], key=f"pdf_{rid}",
                         help="Remove the file with the ✕ next to its name, or drop a new one to replace it.")
    if f is None:
        return None
    data = f.getvalue()
    if not data:
        st.error("That file is empty.")
        return None
    if len(data) > MAX_PDF_MB * 1_048_576:
        st.error(f"That PDF is {human_size(len(data))}; the limit is {MAX_PDF_MB} MB.")
        return None
    if b"%PDF" not in data[:1024]:
        st.error("That file doesn't look like a PDF.")
        return None

    info = extract_pdf(data)
    if info["error"]:
        st.error(info["error"])
        return None

    pages = info["pages"]
    n = len(pages)
    st.caption(f"📄 **{f.name}** · {n} page{'s' if n != 1 else ''} · {human_size(len(data))}")
    digest = hashlib.sha1(data).hexdigest()[:10]
    chars = sum(len("".join(p.split())) for p in pages)

    if chars >= MIN_TEXT_CHARS:
        return pdf_text_ui(rid, f.name, pages, digest)
    return pdf_scanned_ui(rid, f.name, data, n, digest)


def pdf_text_ui(rid, filename, pages, digest):
    n = len(pages)
    first_page, last_page = (st.slider("Pages to analyze", 1, n, (1, n), key=f"pdfrange_{rid}_{digest}")
                             if n > 1 else (1, 1))
    chosen = "\n\n".join(p for p in pages[first_page - 1:last_page] if p)
    st.success("Text extracted from the PDF. Review and edit it below. Keep only the proof you want analyzed.")
    text = st.text_area("Extracted text", value=chosen, height=280,
                        key=f"pdftext_{rid}_{digest}_{first_page}_{last_page}")
    meta = f"{n} pages" if n == 1 or (first_page, last_page) == (1, n) else \
        f"pages {first_page}–{last_page} of {n}"
    return {"kind": "pdf", "text": text.strip(), "image": None, "mime": None,
            "filename": filename, "meta": meta}


def pdf_scanned_ui(rid, filename, data, n, digest):
    st.warning(
        "This PDF has little or no selectable text, so it looks like a **scan**. ProofLens can't "
        "run OCR on its own text layer, but it can send one page's embedded scan to the AI as an "
        "image. Pick a page below."
    )
    page_no = (st.number_input("Page to analyze", 1, n, 1, key=f"pdfpage_{rid}_{digest}")
               if n > 1 else 1)
    found = pdf_page_image(data, int(page_no))
    if not found:
        st.error(f"Page {page_no} has no embedded image to analyze. Try another page, or "
                 "upload a photo or screenshot of the proof instead.")
        return None
    img, mime = found
    st.image(img, caption=f"Page {page_no}", width=420)
    return {"kind": "pdf", "text": "", "image": img, "mime": mime, "filename": filename,
            "meta": f"scanned, page {page_no} of {n}"}


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------
def add_history(sub, proof):
    entry = {
        "id": uuid.uuid4().hex[:10],
        "title": make_title(proof, sub["text"], sub["filename"]),
        "kind": sub["kind"],
        "filename": sub["filename"],
        "meta": sub["meta"],
        "time": datetime.now(),
        "proof": proof,
        "source_text": sub["text"],
        "image": sub["image"],   # only for image/camera/scanned-PDF-page input; raw PDFs are never kept
    }
    st.session_state.history = [entry] + st.session_state.history[: MAX_HISTORY - 1]
    return entry


def get_entry(entry_id):
    return next((e for e in st.session_state.history if e["id"] == entry_id), None)


def cb_select(entry_id):
    st.session_state.active_id = entry_id
    st.session_state.error = None


def cb_new():
    st.session_state.update(active_id=None, error=None, fresh_id=None, confirm_clear=False,
                            run_id=st.session_state.run_id + 1)


def cb_delete(entry_id):
    st.session_state.history = [e for e in st.session_state.history if e["id"] != entry_id]
    if st.session_state.active_id == entry_id:
        st.session_state.active_id = None


def cb_clear(confirm):
    st.session_state.confirm_clear = confirm


def cb_clear_all():
    st.session_state.update(history=[], active_id=None, fresh_id=None, confirm_clear=False)


def render_sidebar():
    with st.sidebar:
        st.header("ProofLens History")
        st.button("➕ New Problem", type="primary", use_container_width=True, on_click=cb_new)
        history = st.session_state.history
        if not history:
            st.caption("No problems yet. Analyze a proof and it will show up here.")
            return
        st.caption("Kept for this browser session only.")
        box = st.container(height=420) if len(history) > 6 else st.container()
        with box:
            for e in history:
                active = e["id"] == st.session_state.active_id
                c1, c2 = st.columns([5, 1])
                c1.button(short(e["title"], 34), key=f"sel_{e['id']}", help=e["title"],
                          type="primary" if active else "secondary", use_container_width=True,
                          on_click=cb_select, args=(e["id"],))
                c2.button("✕", key=f"del_{e['id']}", help="Delete this entry",
                          on_click=cb_delete, args=(e["id"],))
                label = KIND_LABEL[e["kind"]] + (f" · {short(e['filename'], 18)}" if e["filename"] else "")
                c1.caption(f"{label} · {e['time']:%H:%M}")
        if st.session_state.confirm_clear:
            st.warning("Delete all history?")
            a, b = st.columns(2)
            a.button("Yes, clear", on_click=cb_clear_all, use_container_width=True)
            b.button("Cancel", on_click=cb_clear, args=(False,), use_container_width=True)
        else:
            st.button("Clear history", on_click=cb_clear, args=(True,), use_container_width=True)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
VERDICTS = {
    "valid": (st.success, "The analysis found no problems with this proof."),
    "valid_with_gaps": (st.warning, "The analysis found the proof correct but with missing justifications."),
    "invalid": (st.error, "The analysis found a likely error in this proof."),
    "unclear": (st.info, "The analysis could not reach a clear verdict."),
}


def step_status(step):
    if step.get("error"):
        return "Likely error", "pl-error"
    if step.get("gap"):
        return "Needs justification", "pl-warn"
    return "Looks valid", "pl-valid"


def render_step(i, step):
    if not isinstance(step, dict):
        step = {"statement": str(step)}
    sid = step.get("id", f"S{i}")
    label, css = step_status(step)
    problem = bool(step.get("error") or step.get("gap"))
    with st.expander(f"Step {sid}: {short(step.get('statement', ''), 80)}", expanded=problem or i == 1):
        badges = tag(label, css)
        if step.get("type"):
            badges += tag(str(step["type"]).capitalize(), "pl-neutral")
        if step.get("confidence") in ("low", "medium"):
            badges += tag(f"{step['confidence']} confidence", "pl-neutral")
        st.markdown(badges, unsafe_allow_html=True)
        if step.get("statement"):
            st.code(str(step["statement"]), language="text", wrap_lines=True)
        if step.get("justification"):
            st.markdown(f"**Why this step is allowed:** {step['justification']}")
        if step.get("depends_on"):
            st.markdown("**Follows from:** " + ", ".join(str(d) for d in step["depends_on"]))
        if step.get("error"):
            st.error(f"**Likely error.** {step.get('error_explanation') or 'This step does not appear to follow.'}")
        elif step.get("gap"):
            st.warning(f"**Missing justification.** {step.get('gap_explanation') or 'This step skips some reasoning.'}")


def tree_input(proof):
    """Map parser fields to the ones visuals.draw_proof reads, without changing either module."""
    steps = []
    for s in proof.get("steps") or []:
        if not isinstance(s, dict):
            continue
        kind = s.get("type")
        steps.append({
            "id": s.get("id"),
            "text": s.get("statement", ""),
            "type": kind if kind in ("assumption", "conclusion") else "implication",
            "depends_on": s.get("depends_on") or [],
            "explanation": s.get("justification", ""),
            "status": "error" if s.get("error") else "gap" if s.get("gap") else "ok",
            "issue": s.get("error_explanation") or s.get("gap_explanation") or "",
        })
    return {"title": proof.get("title"), "steps": steps}


def render_tree(entry):
    st.subheader("Visual proof tree")
    if draw_proof is None:
        st.info("The proof tree is unavailable because the visualization module could not be loaded.")
        return
    with st.expander("How to read this tree"):
        st.markdown(
            "Each box is a step. Arrows point from the steps a statement relies on to the statement "
            "that uses them. Press **Play** to walk through the argument; steps with a missing "
            "justification or a likely error are highlighted."
        )
    try:
        draw_proof(tree_input(entry["proof"]), key=f"tree_{entry['id']}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("draw_proof failed: %s", type(exc).__name__)
        st.warning("The proof tree couldn't be drawn, but the written analysis above is still available.")


def render_animation(entry):
    st.subheader("Animated explanation")
    if render_proof is None:
        st.info("The animation is unavailable because the manim module could not be loaded.")
        return
    videos = st.session_state.setdefault("videos", {})
    video = videos.get(entry["id"])
    if video is None:
        st.caption("Renders a short 3Blue1Brown-style video of this proof with manim, narrated step by step.")
        if st.button("🎬 Animate this proof", key=f"animate_{entry['id']}"):
            try:
                with st.spinner("Recording the narration and rendering the animation. This takes about 40 seconds..."):
                    path = render_proof(entry["proof"], f"proof_{hashlib.md5(str(entry['id']).encode()).hexdigest()[:10]}")
                vtt = path.with_suffix(".vtt")
                videos[entry["id"]] = (path.read_bytes(), vtt.read_text(encoding="utf-8") if vtt.exists() else None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("render_proof failed: %s", type(exc).__name__)
                st.warning("The animation couldn't be rendered, but the analysis and proof tree above are still available.")
            else:
                st.rerun()  # redraw without the button, showing the video
    if video is not None:
        data, subtitles = video
        st.video(data, subtitles=subtitles)


def render_results(entry):
    proof = entry["proof"]
    steps = proof.get("steps") if isinstance(proof.get("steps"), list) else []

    if entry["id"] == st.session_state.fresh_id:
        st.success("Analysis complete.")
    else:
        st.info(f"📂 **Previous analysis** from {entry['time']:%b %d, %H:%M}, loaded from history. "
                "No new analysis was run.")

    # Verdict
    verdict = proof.get("verdict")
    if verdict in VERDICTS:
        box, default = VERDICTS[verdict]
        box(proof.get("verdict_explanation") or default)
        st.caption("This is an AI-generated assessment, not a formal verification.")

    # Overview
    st.subheader("Proof overview")
    if proof.get("title") or proof.get("technique"):
        bits = [f"**{proof['title']}**" if proof.get("title") else "",
                f"Technique: {proof['technique']}" if proof.get("technique") else ""]
        st.markdown("  \n".join(b for b in bits if b))
    if proof.get("summary"):
        st.markdown(proof["summary"])
    src = []
    if entry["filename"]:
        src.append(f"📄 {entry['filename']}")
    if entry["meta"]:
        src.append(entry["meta"])
    if src:
        st.caption(" · ".join(src))
    if entry["source_text"]:
        with st.expander("Submitted proof text", expanded=entry["kind"] == "text"):
            st.code(entry["source_text"], language="text", wrap_lines=True)
    if entry["image"]:
        with st.expander("Submitted image"):
            st.image(entry["image"])

    # Assumptions and gaps
    assumptions = [s for s in steps if isinstance(s, dict) and s.get("type") == "assumption"]
    flagged = [s for s in steps if isinstance(s, dict) and (s.get("error") or s.get("gap"))]
    if assumptions or flagged:
        st.subheader("Assumptions and logical gaps")
        a, g = st.columns(2)
        with a:
            st.markdown("**Assumptions**")
            for s in assumptions or []:
                st.markdown(f"- **{s.get('id')}:** {short(s.get('statement', ''), 140)}")
            if not assumptions:
                st.caption("None identified.")
        with g:
            st.markdown("**Flagged steps**")
            for s in flagged:
                if s.get("error"):
                    st.error(f"**{s.get('id')}, likely error:** {s.get('error_explanation', '')}")
                else:
                    st.warning(f"**{s.get('id')}, missing justification:** {s.get('gap_explanation', '')}")
            if not flagged:
                st.caption("No gaps or errors flagged.")

    # Steps
    st.subheader("Step-by-step explanation")
    if steps:
        for i, step in enumerate(steps, start=1):
            render_step(i, step)
    else:
        st.info("The analysis did not return any individual steps.")

    notes = proof.get("warnings")
    if notes:
        with st.expander("Analysis notes"):
            for n in notes:
                st.markdown(f"- {n}")

    if steps:
        render_tree(entry)
        render_animation(entry)


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------
def run_analysis(sub):
    """Call the parser once. Only a successful result creates a history entry."""
    st.session_state.error = None
    try:
        with st.spinner("Analyzing your proof. This can take a few seconds..."):
            proof = parse_proof(text=sub["text"], image_bytes=sub["image"], mime_type=sub["mime"])
        if not isinstance(proof, dict) or not proof:
            raise ValueError("unexpected parser output")
    except Exception as exc:  # noqa: BLE001
        logger.warning("parse_proof failed: %s", type(exc).__name__)
        msg = str(exc).lower()
        if "api key" in msg or "api_key" in msg or "credentials" in msg:
            st.session_state.error = ("The AI service isn't configured. Add your API key "
                                      "(see the project README or `.env`) and try again.")
        else:
            st.session_state.error = ("We couldn't analyze that proof. Check that the text or image "
                                      "is a readable proof and try again. If it keeps failing, the "
                                      "AI service may be unavailable.")
        st.session_state.active_id = None
        return
    entry = add_history(sub, proof)
    st.session_state.active_id = st.session_state.fresh_id = entry["id"]


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------
render_sidebar()

st.markdown('<p class="pl-title">🔎 ProofLens</p>', unsafe_allow_html=True)
st.markdown('<p class="pl-sub">Turn confusing math proofs into step-by-step visual explanations.</p>',
            unsafe_allow_html=True)

if parse_proof is None:
    st.error("The proof analysis module (`parser.py`) couldn't be loaded, so proofs can't be "
             "analyzed yet. Check that it and its dependencies are installed, then reload.")
    logger.error("parser unavailable: %s", PARSER_ERROR)

rid = st.session_state.run_id
source = st.radio("Input method", SOURCES, horizontal=True, label_visibility="collapsed",
                  key=f"source_{rid}")

if source == SOURCES[0]:
    submission = text_input_ui(rid)
elif source == SOURCES[1]:
    submission = image_input_ui(rid, camera=False)
elif source == SOURCES[2]:
    submission = image_input_ui(rid, camera=True)
else:
    submission = pdf_input_ui(rid)

has_input = bool(submission and (submission["text"] or submission["image"]))
analyze = st.button("Analyze Proof", type="primary", disabled=parse_proof is None)

if analyze:
    if has_input:
        run_analysis(submission)
    else:
        st.session_state.error = None
        st.warning("Please provide a proof first: paste text, or upload an image or PDF.")

st.divider()

active = get_entry(st.session_state.active_id)
if st.session_state.error:
    st.error(st.session_state.error)
elif active:
    render_results(active)
elif not analyze:
    st.markdown("#### How it works")
    st.markdown(
        "1. Paste a proof, or upload a photo, camera shot or PDF.\n"
        "2. Click **Analyze Proof**.\n"
        "3. Read each step in plain English, spot unsupported leaps, and explore the proof tree.\n\n"
        "Earlier problems appear in the sidebar so you can reopen them."
    )
