from __future__ import annotations

import json
import re
from functools import partial
from pathlib import Path
from typing import Callable

from ..llm.google_client import Clients, Stage3Chat, image_part
from ..progress import Tracker, retry_message, warn


# The "{slide_numbers_list}" placeholder is substituted per-group (e.g. "5, 7, 8").
# We ask the LLM to emit its explanation in slide-linked segments, each preceded
# by a delimiter line like `===SLIDES: 7,8===`. We then parse those segments
# and render the Markdown with slide images interleaved between their notes
# instead of all-images-then-all-notes.
NOTES_PROMPT_BASE = """Can you explain to me these slides?
Give me an explanation in a notes kind of way so I can study it later
and understand things clearly.

If these slides contain any image then explain that image as well
(also mention which slide that image was on, by seeing at the bottom
right corner as we stamped each slide).

If the slides contain any code then also return the code in the response
as well as the explanation of that code. If the code snippets in the slides
are incomplete then complete those snippets, return those completed snippets
as well as their explanation (add comments within the code snippets for
better explanation).

If there are multiple code snippets scattered around in these slides then
explain the bigger picture as well, how they are working together.

The format of your response should be in Markdown (README style).
Be thorough. Explain concepts, don't just describe what's on the slide.

Do not include a top-level heading — the group title is added separately.

OUTPUT STRUCTURE — SLIDE-LINKED SEGMENTS (very important):

The slides in this group, in the order shown, are numbered: {slide_numbers_list}.

Break your explanation into "segments". Before each segment, write a
delimiter line in this EXACT format:

===SLIDES: <comma-separated slide numbers>===

Each segment's text covers ONLY the slide(s) listed in its delimiter.

Rules:
- Every slide in this group must appear in exactly one delimiter.
- Prefer ONE slide per segment. Combine multiple slides into a single
  segment ONLY if their content is so tightly coupled (e.g. a 2-slide
  code listing that must be read together, a continuing diagram) that
  splitting would break the explanation.
- Delimiter lines MUST use the exact form `===SLIDES: 5===` or
  `===SLIDES: 7,8===`. No bold, no extra punctuation, no variation.
- Start your response with the first delimiter (no preamble before it).
- Do NOT include `![slide](...)` image markdown yourself — the images
  are placed for you from the delimiters.

Example of a correctly-formatted response for a group containing slides 5, 7, 8:

===SLIDES: 5===
(your explanation of slide 5 here)

===SLIDES: 7,8===
(your joint explanation of slides 7 and 8 here — use this form only
when the two slides truly belong together)
"""


# Per-file "all groups done" marker (used for fast-skip of entire files on rerun).
PPTX_COMPLETE_MARKER = "<!-- clevernotes:{name}:complete -->"
# Per-group "this group's notes are safely written" marker. Written after each
# group's body + image refs + trailing '---'. Lets us resume a file mid-flight.
GROUP_COMPLETE_MARKER = "<!-- clevernotes:{name}:group:{gid}:complete -->"

_FILE_MARKER_RE = re.compile(r"<!-- clevernotes:([^:]+):complete -->")
_GROUP_MARKER_RE = re.compile(r"<!-- clevernotes:([^:]+):group:([^:]+):complete -->")

# Slide-segment delimiter. Tolerates whitespace variation and case, but requires
# the `===SLIDES:` prefix and trailing `===` on its own line.
_SEGMENT_RE = re.compile(
    r"^[ \t]*={3,}\s*SLIDES\s*:\s*([\d,\s]+?)\s*={3,}[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)


def _scan_markers(notes_md: Path) -> tuple[set[str], dict[str, set[str]]]:
    """Return (completed_files, completed_groups_per_file)."""
    if not notes_md.exists():
        return set(), {}
    text = notes_md.read_text()
    files = set(_FILE_MARKER_RE.findall(text))
    groups: dict[str, set[str]] = {}
    for fname, gid in _GROUP_MARKER_RE.findall(text):
        groups.setdefault(fname, set()).add(str(gid))
    return files, groups


def _truncate_after_last_marker(notes_md: Path) -> None:
    """Discard anything after the last completion marker (group or file).

    If the process was killed mid-group, there may be a half-written group
    block on disk. Any complete group will have its trailing
    GROUP_COMPLETE_MARKER. We cut the file right after the last such marker
    so the next run starts from a known-good state.
    """
    if not notes_md.exists():
        return
    text = notes_md.read_text()
    last_end = -1
    for m in _FILE_MARKER_RE.finditer(text):
        last_end = max(last_end, m.end())
    for m in _GROUP_MARKER_RE.finditer(text):
        last_end = max(last_end, m.end())
    if last_end < 0:
        notes_md.write_text("")
        return
    trimmed = text[:last_end].rstrip() + "\n\n"
    if trimmed != text:
        notes_md.write_text(trimmed)


def _parse_segments(
    body: str,
    slide_filenames: list[str],
) -> list[tuple[list[str], str]] | None:
    """Parse `===SLIDES: N,M===` delimited segments out of an LLM response.

    Returns a list of (filenames_for_this_segment, explanation_text) tuples,
    preserving the order the LLM produced. Returns None if no delimiters were
    found at all — the caller should then fall back to the legacy rendering.

    Slide numbers in the delimiters are mapped to filenames like `5.png`.
    Numbers that don't correspond to any slide in this group are silently
    dropped (the LLM occasionally hallucinates numbers).
    """
    matches = list(_SEGMENT_RE.finditer(body))
    if not matches:
        return None

    valid_filenames = set(slide_filenames)
    segments: list[tuple[list[str], str]] = []
    for i, m in enumerate(matches):
        raw_ids = [x.strip() for x in m.group(1).split(",")]
        filenames: list[str] = []
        seen: set[str] = set()
        for rid in raw_ids:
            if not rid.isdigit():
                continue
            fn = f"{int(rid)}.png"
            if fn in valid_filenames and fn not in seen:
                filenames.append(fn)
                seen.add(fn)

        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = body[start:end].strip()
        segments.append((filenames, text))
    return segments


def _render_group_md(
    group_title: str,
    slide_filenames: list[str],
    pptx_name: str,
    body_md: str,
) -> str:
    """Build the Markdown block for one group. Tries interleaved rendering
    first; falls back to legacy (all images then one body) if parsing fails
    or the LLM didn't follow the segment format."""

    # The markdown lives at NOTES/final_notes/file_N.md; slide PNGs live at
    # NOTES/file_N/K.png. `../file_N/K.png` resolves correctly from the md
    # file's own parent, which is what GitHub / VS Code / pandoc's
    # --resource-path all do.
    def rel(fn: str) -> str:
        return f"../{pptx_name}/{fn}"

    lines: list[str] = [f"## {group_title}", ""]

    segments = _parse_segments(body_md, slide_filenames)

    # Why a blank line BETWEEN consecutive image refs: pandoc treats two
    # `![...](...)` on back-to-back lines (no blank line between) as ONE
    # paragraph with two inline images, which renders them side-by-side in
    # the PDF and overflows the page when both are full-width slides. A
    # blank line makes each image its own paragraph → its own figure → they
    # stack vertically in reading order.
    def _append_images(fns: list[str]) -> None:
        for i, fn in enumerate(fns):
            if i > 0:
                lines.append("")
            slide_num = Path(fn).stem
            lines.append(f"![{pptx_name} : slide {slide_num}]({rel(fn)})")

    if segments and any(seg_files for seg_files, _ in segments):
        covered: set[str] = set()
        for seg_files, seg_text in segments:
            _append_images(seg_files)
            covered.update(seg_files)
            if seg_files:
                lines.append("")
            if seg_text:
                lines.append(seg_text)
                lines.append("")

        # Any slides the LLM never referenced — append at end so the reader
        # doesn't lose the visual. This should be rare if the LLM follows the
        # prompt, but we guard against it.
        missing = [fn for fn in slide_filenames if fn not in covered]
        if missing:
            lines.append("")
            lines.append("<!-- clevernotes: these slides were not explicitly "
                         "labelled by the model; shown here without per-slide notes -->")
            lines.append("")
            _append_images(missing)
            lines.append("")
    else:
        # Fallback: old all-images-then-body format. Happens when the LLM
        # ignores the segment format entirely. The content is still good,
        # just not interleaved.
        _append_images(slide_filenames)
        lines.append("")
        lines.append(body_md.strip())
        lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _append_group_block(
    combined_md: Path,
    per_file_md: Path,
    group_title: str,
    slide_filenames: list[str],
    body_md: str,
    pptx_name: str,
    group_id: str,
) -> None:
    """Append a rendered group block + its completion marker to BOTH
    combined_notes.md and the per-file (file_N.md) document.

    Both files get the same content so they stay in sync. Markers live in
    both too — they're HTML comments, invisible when reading the Markdown,
    and they let the resume logic truncate either file cleanly."""
    block = _render_group_md(group_title, slide_filenames, pptx_name, body_md)
    marker = GROUP_COMPLETE_MARKER.format(name=pptx_name, gid=group_id)
    # MUST have a blank line between the trailing `---` rule and the marker.
    # Without it, pandoc's pipe-table parser treats `---` as a table-header
    # separator and swallows the following heading/paragraph/list content
    # into a bogus 1-column longtable, producing a PDF where words wrap one
    # per line. The Lua filter (pagebreak.lua) converts `---` to \clearpage,
    # so this blank line only matters for pandoc's parser, not the output
    # layout. Same reasoning for the trailing "\n\n" after the marker.
    payload = block + "\n" + marker + "\n\n"
    for path in (combined_md, per_file_md):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(payload)


def _append_complete_marker(
    combined_md: Path,
    per_file_md: Path,
    pptx_name: str,
) -> None:
    marker = PPTX_COMPLETE_MARKER.format(name=pptx_name) + "\n\n"
    for path in (combined_md, per_file_md):
        with path.open("a") as f:
            f.write(marker)


def _slide_numbers_from_filenames(filenames: list[str]) -> list[int]:
    nums: list[int] = []
    for fn in filenames:
        stem = Path(fn).stem
        if stem.isdigit():
            nums.append(int(stem))
    return nums


def generate_for_pptx(
    clients: Clients,
    pptx_dir: Path,
    combined_md: Path,
    per_file_md: Path,
    prompt_suffix: str,
    tracker: Tracker | None,
    tracker_key: str | None,
    already_done_group_ids: set[str] | None = None,
    on_group_done: Callable[[], None] | None = None,
    on_file_done: Callable[[], None] | None = None,
) -> None:
    """Generate notes for one pptx.

    Callbacks:
      on_group_done — called after each group's block + marker land on disk.
        Used by the UI to flip this pptx's banner label from NOT STARTED to
        PARTIALLY DONE on the first success.
      on_file_done — called once after the PPTX_COMPLETE_MARKER is written.
        Used to flip the label to DONE.
    """
    grouping_path = pptx_dir / f"{pptx_dir.name}_grouping_phase_summary.json"
    grouping = json.loads(grouping_path.read_text())
    groups = grouping.get("groups", [])
    done = already_done_group_ids or set()

    # One Stage3Chat per file: chat history (cross-group context) lives for
    # this file only. The pool (daily-exhaustion state) is shared across files
    # via `clients.stage3_pool`.
    chat = Stage3Chat(clients.stage3_pool, clients.model_s3)

    # Advance the progress bar for already-done groups so UI matches disk.
    if tracker and tracker_key and done:
        for group in groups:
            if str(group["group_id"]) in done:
                tracker.advance(tracker_key)

    for group in groups:
        group_id = str(group["group_id"])
        group_title = group["group_title"]
        slide_filenames = group["slides"]

        if group_id in done:
            # Already on disk from a previous run — skip without touching the LLM.
            # This group's content is NOT in the current chat session, so
            # later groups won't know what was covered. Acceptable tradeoff
            # for resume correctness.
            continue

        slide_abs_paths = [pptx_dir / fn for fn in slide_filenames]
        slide_numbers = _slide_numbers_from_filenames(slide_filenames)
        slide_numbers_str = ", ".join(str(n) for n in slide_numbers) or "(see images)"

        parts: list = [image_part(p) for p in slide_abs_paths if p.exists()]
        # Use replace (not str.format) so stray `{` in the preset suffix — or
        # anywhere else — won't break prompt construction.
        prompt_text = (
            NOTES_PROMPT_BASE.replace("{slide_numbers_list}", slide_numbers_str)
            + prompt_suffix
        )
        parts.append(prompt_text)

        label = f"{pptx_dir.name} group {group_id}"
        on_retry = partial(
            lambda a, s, e, lbl: retry_message(lbl, a, s, e),
            lbl=label,
        )

        def _on_switch(new_idx: int, reason: str, lbl: str = label) -> None:
            # 1-based for the human-facing message; key #1 is primary,
            # key #2 is the backup.
            warn(
                f"{lbl}: stage-3 {reason} — switching to key #{new_idx + 1} "
                f"and replaying chat history."
            )

        body = chat.send(parts, on_retry=on_retry, on_switch=_on_switch)
        _append_group_block(
            combined_md, per_file_md, group_title, slide_filenames, body,
            pptx_name=pptx_dir.name, group_id=group_id,
        )

        if tracker and tracker_key:
            tracker.advance(tracker_key)
        if on_group_done:
            try:
                on_group_done()
            except Exception:  # noqa: BLE001
                pass  # UI callback failure never blocks pipeline progress

    _append_complete_marker(combined_md, per_file_md, pptx_dir.name)
    if on_file_done:
        try:
            on_file_done()
        except Exception:  # noqa: BLE001
            pass


def _rebuild_per_file_mds_from_combined(
    combined_md: Path,
    per_file_mds: dict[str, Path],
) -> None:
    """Rewrite every per-file .md to match the current combined_notes.md.

    `combined_notes.md` is the single source of truth for resume. Per-file
    .md files are projections — one per pptx, containing only that pptx's
    group blocks + markers. On restart we rebuild them so they match
    combined exactly, which handles all the edge cases:

      - user deleted a per-file .md but kept combined → restored
      - user deleted combined but kept per-file .md → per-file wiped (the
        truncated-to-empty combined_md is now the truth)
      - combined was killed mid-write and then truncated — per-file gets
        rebuilt without the half-written group

    Content is partitioned by walking combined_md's completion markers in
    order; each marker declares which pptx its preceding content belongs to.
    """
    # Ensure parent exists and start every bucket fresh.
    for path in per_file_mds.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    if not combined_md.exists():
        return
    text = combined_md.read_text()
    if not text:
        return

    # One regex matches both forms:
    #   <!-- clevernotes:<name>:group:<gid>:complete -->
    #   <!-- clevernotes:<name>:complete -->
    # Group(1) captures the pptx name in both.
    marker_re = re.compile(
        r"<!-- clevernotes:([^:]+)(?::group:[^:]+)?:complete -->"
    )

    buckets: dict[str, list[str]] = {name: [] for name in per_file_mds}
    cursor = 0
    for m in marker_re.finditer(text):
        pptx_name = m.group(1)
        chunk = text[cursor:m.end()]
        if pptx_name in buckets:
            # Strip leading whitespace on the FIRST chunk for a bucket so the
            # per-file md doesn't start with blank lines.
            if not buckets[pptx_name]:
                chunk = chunk.lstrip()
            buckets[pptx_name].append(chunk)
        cursor = m.end()

    for pptx_name, path in per_file_mds.items():
        joined = "".join(buckets[pptx_name])
        if joined:
            joined = joined.rstrip() + "\n\n"
        path.write_text(joined)


def prepare_notes_md(
    combined_md: Path,
    per_file_mds: dict[str, Path],
    pptx_names: list[str],
) -> tuple[set[str], dict[str, set[str]]]:
    """Bring all note files into a clean state for resume.

    Steps (in order):
      1. Truncate `combined_notes.md` at its last completion marker,
         dropping any half-written group block from a killed process.
      2. Scan `combined_notes.md` for completion markers → this is the
         source of truth for what's done.
      3. Rebuild each per-file `.md` from the relevant slice of combined,
         so both files always agree on state.

    Returns (completed_files, completed_groups_per_file).
    """
    # (1) Truncate combined to last marker.
    _truncate_after_last_marker(combined_md)

    # (2) Source-of-truth scan.
    completed_files, completed_groups = _scan_markers(combined_md)
    completed_files = {n for n in completed_files if n in pptx_names}
    completed_groups = {k: v for k, v in completed_groups.items() if k in pptx_names}

    # Ensure combined exists (even if empty) so later appends have a target.
    if not combined_md.exists():
        combined_md.write_text("")

    # (3) Rebuild per-file mds from combined. Always runs, even on a cold
    # start (it just writes empty files).
    _rebuild_per_file_mds_from_combined(combined_md, per_file_mds)

    return completed_files, completed_groups
