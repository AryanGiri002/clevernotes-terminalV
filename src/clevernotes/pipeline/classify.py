from __future__ import annotations

import json
import re
from functools import partial
from pathlib import Path

from ..llm.google_client import Clients, generate_text, image_part
from ..progress import Tracker, retry_message


CLASSIFY_PROMPT = """Is this slide USEFUL or USELESS for a student trying to study the subject?

USELESS means: title slides, thank-you slides, disclaimers, safety icons,
section dividers with no educational content, social media pages,
completely empty slides or slides with only a decorative image and no content.

USEFUL means: slides with actual educational content — explanations,
definitions, diagrams, data, code, formulas, or structured information
a student would need to know.

Return JSON only, no preamble:
{"status": "USEFUL" | "USELESS", "summary": "<5-line summary, or null if USELESS>"}
"""


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    # strip common code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    return json.loads(text)


def classify_file(
    clients: Clients,
    pptx_dir: Path,
    pngs: list[Path],
    tracker: Tracker | None,
    tracker_key: str | None,
) -> None:
    summary_path = pptx_dir / f"summary_{pptx_dir.name}.json"
    results: list[dict] = []

    for png in pngs:
        idx = int(png.stem)
        on_retry = partial(
            lambda a, s, e, label: retry_message(label, a, s, e),
            label=f"{pptx_dir.name} slide {idx}",
        )
        text = generate_text(
            clients.stages12,
            clients.model_s1,
            [image_part(png), CLASSIFY_PROMPT],
            on_retry=on_retry,
        )
        try:
            data = _parse_json_response(text)
            status = data.get("status", "USELESS")
            summary = data.get("summary")
        except (json.JSONDecodeError, AttributeError):
            status = "USELESS"
            summary = None

        if status == "USELESS":
            discarded = png.with_name(f"{idx}_DISCARDED.png")
            png.rename(discarded)
        else:
            results.append({
                "index": idx,
                "file_name": png.name,
                "status": "USEFUL",
                "summary": summary,
            })

        if tracker and tracker_key:
            tracker.advance(tracker_key)

    summary_path.write_text(json.dumps(results, indent=2))
