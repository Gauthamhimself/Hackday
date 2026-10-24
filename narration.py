"""Voice-over for the manim video: a teaching script, TTS audio and timestamps. Owner: Tegh.

json_to_manim.render_proof(..., narrate=True) uses this to:
  1. write a short spoken script for every step of the scene timeline,
  2. synthesize each line with a Microsoft neural voice (edge-tts, no API key),
  3. stretch each step's pause so the animation lasts as long as its narration,
  4. save <name>_narration.json with the start/end time of every line, and
  5. mix the clips into the video at exactly those times.
"""

import asyncio
import copy
import json
import re
import subprocess
from pathlib import Path

import edge_tts

VOICE = "en-US-AndrewMultilingualNeural"   # warm, confident, conversational
RATE = "-5%"                                # a touch slower than default, for teaching
LEAD_IN = 0.2                               # seconds between a step starting and the voice starting
TAIL = 0.4                                  # breathing room after each line

# How math symbols should be read aloud.
SPOKEN = [
    (r"∑\[([^\]]*)\]", r" the sum over \1 of "), (r"∏\[([^\]]*)\]", r" the product over \1 of "),
    (r"lim\[([^\]]*)\]", r" the limit as \1 of "), (r"\|([^|]+)\|", r" the size of \1 "),
    ("≥", " is at least "), ("≤", " is at most "), ("≠", " is not equal to "), ("≡", " is congruent to "),
    ("≈", " is about "), ("∉", " is not in "), ("∈", " in "), ("⇒", " implies "), ("⇔", " if and only if "),
    ("→", " goes to "), ("∀", " for all "), ("∃", " there exists "), ("∩", " intersect "), ("∪", " union "),
    ("∅", " the empty set "), ("⊆", " is a subset of "), ("√", " the square root of "), ("∠", " angle "),
    ("°", " degrees "), ("∑", " the sum "), ("∏", " the product "), ("…", " and so on "), ("Δ", " triangle "),
    ("ℕ", " the natural numbers "), ("ℤ", " the integers "), ("ℚ", " the rationals "), ("ℝ", " the reals "),
    ("∥", " is parallel to "), ("||", " is parallel to "), ("²", " squared "), ("³", " cubed "),
    ("ⁿ", " to the n "), ("×", " times "), ("·", " times "), ("=", " equals "), (" + ", " plus "),
    (r"(?<=[\w)])/(?=\w|\()", " over "),
]
SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋ₙₖᵢⱼₘ", "0123456789+-nkijm")


def speakable(text: str) -> str:
    for pattern, spoken in SPOKEN:
        text = re.sub(pattern, spoken, text) if pattern.startswith(("(", "∑", "∏", "lim", "\\")) \
            else text.replace(pattern, spoken)
    text = re.sub(r"(\w)([₀-₉₊₋ₙₖᵢⱼₘ]+)", lambda mt: f"{mt.group(1)} sub {mt.group(2).translate(SUBSCRIPTS)}", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _sentence(text) -> str:
    text = (text or "").strip()
    return text if not text or text[-1] in ".!?" else text + "."


def build_script(proof: dict, scene: dict) -> list[str]:
    """One spoken line per scene timeline entry (same order, same length)."""
    steps = {s["id"]: s for s in proof.get("steps", [])}
    last_step = proof["steps"][-1]["id"] if proof.get("steps") else None
    lines = []
    for i, entry in enumerate(scene["timeline"]):
        s = steps.get(entry["step"])
        if s is None:
            lines.append("")
            continue
        caption = _sentence(s.get("animation", {}).get("caption"))
        why = _sentence(s.get("justification"))
        parts = [f"Let's walk through this proof: {proof.get('title', '')}."] if i == 0 else []
        parts.append(caption)
        if s.get("error"):
            opener = "" if caption.lower().startswith(("wait", "but")) else "But wait, this is where it breaks. "
            parts.append(opener + _sentence(s.get("error_explanation")))
        else:
            # Short justifications ("Inductive hypothesis.") just repeat the caption; skip them.
            if len(why.split()) >= 6 and why.lower() != caption.lower():
                parts.append(why)
            if s.get("gap"):
                parts.append("Notice that a step is skipped here. " + _sentence(s.get("gap_explanation")))
        has_error = any(st.get("error") for st in proof.get("steps", []))
        if entry["step"] == last_step and proof.get("verdict_explanation") and not has_error:
            parts.append("So, in summary: " + _sentence(proof["verdict_explanation"]))
        lines.append(" ".join(p for p in parts if p))
    return lines


async def _synthesize_all(lines: list[str], out_dir: Path, name: str) -> list[Path | None]:
    async def one(i, line):
        if not line:
            return None
        path = out_dir / f"{name}_{i:02d}.mp3"
        for attempt in range(3):  # the voice service occasionally drops a request
            try:
                await edge_tts.Communicate(speakable(line), VOICE, rate=RATE).save(str(path))
                return path
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5)
    return await asyncio.gather(*(one(i, line) for i, line in enumerate(lines)))


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    return float(out.stdout.strip() or 0)


def prepare(proof: dict, scene: dict, animation_seconds, out_dir: Path, name: str) -> tuple[dict, dict]:
    """Synthesize the narration and return (scene with stretched waits, narration timing dict).

    animation_seconds(entry) must give how long a timeline entry animates before its wait,
    exactly as the generated manim code plays it.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = build_script(proof, scene)
    clips = asyncio.run(_synthesize_all(lines, out_dir, name))

    scene = copy.deepcopy(scene)
    t, segments = 0.0, []
    for entry, line, clip in zip(scene["timeline"], lines, clips):
        anim = animation_seconds(entry)
        audio = _duration(clip) if clip else 0.0
        entry["wait"] = round(max(entry.get("wait", 0.5), LEAD_IN + audio + TAIL - anim), 2)
        if clip:
            segments.append({"step": entry["step"], "start": round(t + LEAD_IN, 2),
                             "end": round(t + LEAD_IN + audio, 2), "text": line,
                             "audio_file": clip.name, "audio_seconds": round(audio, 2)})
        t += anim + entry["wait"]
    narration = {"voice": VOICE, "rate": RATE, "total_seconds": round(t + 2, 2), "segments": segments}
    return scene, narration


def mux(video: Path, narration: dict, clip_dir: Path, out: Path) -> Path:
    """Mix every clip into the silent video at its start time."""
    segs = narration["segments"]
    if not segs:
        return video
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(video)]
    for seg in segs:
        cmd += ["-i", str(clip_dir / seg["audio_file"])]
    delays = ";".join(f"[{i + 1}:a]adelay={int(seg['start'] * 1000)}:all=1[a{i}]" for i, seg in enumerate(segs))
    mix = "".join(f"[a{i}]" for i in range(len(segs)))
    cmd += ["-filter_complex", f"{delays};{mix}amix=inputs={len(segs)}:normalize=0[aout]",
            "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def _clock(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def to_vtt(narration: dict, words_per_caption: int = 12) -> str:
    """WebVTT subtitles: each spoken line split into short captions, timed by word count."""
    cues = ["WEBVTT", ""]
    for seg in narration["segments"]:
        words = seg["text"].split()
        # Break at sentence ends first, then split long sentences, so captions don't cut mid-thought.
        chunks = []
        for sentence in re.split(r"(?<=[.!?])\s+", seg["text"]):
            sw = sentence.split()
            size = -(-len(sw) // -(-len(sw) // words_per_caption)) if sw else 1  # even pieces, no orphan word
            chunks += [sw[i:i + size] for i in range(0, len(sw), size)]
        span = seg["end"] - seg["start"]
        t = seg["start"]
        for chunk in chunks:
            end = t + span * len(chunk) / max(len(words), 1)
            cues += [f"{_clock(t)} --> {_clock(end)}", " ".join(chunk), ""]
            t = end
    return "\n".join(cues)


def save(narration: dict, path: Path) -> Path:
    path.write_text(json.dumps(narration, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
