from __future__ import annotations

import json
import re
from functools import partial
from pathlib import Path

from ..llm.google_client import Clients, generate_text
from ..progress import retry_message


GROUPING_PROMPT_TEMPLATE = """I have been assigned the task to teach a student the following slides.
I have given you the summaries for each slide (USELESS ones have already been excluded).

Based on these summaries, which slides should be grouped together such that
slides within a single group have high topic cohesion and it logically
makes sense to teach them together in one session?

GOOD grouping example:
Slides 1–4 all cover "Network Layer addressing and routing protocols" —
they build on each other sequentially. Slides 5–7 cover "Transport Layer
and TCP handshake mechanics" — different enough to be a separate group.

BAD grouping example:
Slide 1 (OSI Model overview) grouped with Slide 9 (Application Layer HTTP) —
they are thematically related but skip 7 slides of prerequisite content,
breaking the natural teaching flow and leaving gaps in understanding.

PREREQUISITE RULE:
If you group discontinuous slides together (e.g. slides 1, 2, and 9),
you must verify that the prerequisite concepts required to understand slide 9
are already covered by the other slides in that group (slides 1 and 2 in this example).
If they are not, do not group them together — slide 9 must go into a group
where its prerequisites are also present.

ORDERING RULE:
Within each group, slides must be ordered in the logical teaching sequence —
the order in which a student should encounter them to build understanding
progressively. Do not list slides in arbitrary or numerical order if the
logical teaching order differs.

CONSTRAINT: MAX_SLIDES_PER_GROUP = {max_slides}
Do not put more than {max_slides} slides in any single group.

For each group, also provide a concise group_title — a short, specific label
that accurately describes what this group of slides covers.

Return ONLY valid JSON in this exact format:
{{
  "no_of_groups": <integer>,
  "groups": [
    {{ "group_id": 1, "group_title": "...", "slides": ["1.png", "2.png"] }},
    ...
  ]
}}

Here are the slide summaries:
{summaries_json}
"""


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    return json.loads(text)


def group_file(
    clients: Clients,
    pptx_dir: Path,
    max_slides: int,
    max_groups: int,
) -> None:
    summary_path = pptx_dir / f"summary_{pptx_dir.name}.json"
    out_path = pptx_dir / f"{pptx_dir.name}_grouping_phase_summary.json"
    summaries = json.loads(summary_path.read_text())

    if not summaries:
        out_path.write_text(json.dumps({"no_of_groups": 0, "groups": []}, indent=2))
        return

    prompt = GROUPING_PROMPT_TEMPLATE.format(
        max_slides=max_slides,
        summaries_json=json.dumps(summaries, indent=2),
    )
    on_retry = partial(
        lambda a, s, e, label: retry_message(label, a, s, e),
        label=f"{pptx_dir.name} grouping",
    )
    text = generate_text(
        clients.stages12,
        clients.model_s2,
        [prompt],
        on_retry=on_retry,
    )
    data = _parse_json_response(text)

    groups = data.get("groups", [])
    if len(groups) > max_groups:
        raise RuntimeError(
            f"{pptx_dir.name}: LLM produced {len(groups)} groups, exceeds MAX_GROUPS_PER_PPTX={max_groups}"
        )

    out_path.write_text(json.dumps(data, indent=2))
