from __future__ import annotations

import argparse
import json
import re
import signal
import sys
from pathlib import Path

from . import __version__, config, presets
from .llm.google_client import Clients, PermanentLLMError
from .pipeline import classify, convert, group, notes, pdf, stamp
from .progress import bars, bars_with_status, banner, console, err, info, ok, spinner, warn
from rich.text import Text


INPUT_PATTERN = re.compile(r"^file_(\d+)\.(pptx|pdf)$", re.IGNORECASE)


def discover_inputs(cwd: Path) -> list[Path]:
    """Find `file_N.pptx` / `file_N.pdf` under cwd, sorted by N.

    A given N must resolve to exactly one file — having both `file_3.pptx`
    and `file_3.pdf` in the same folder is ambiguous (which one wins? which
    one is stale?), so we fail loudly instead of silently picking one.
    """
    by_index: dict[int, Path] = {}
    for p in cwd.iterdir():
        m = INPUT_PATTERN.match(p.name)
        if not m:
            continue
        idx = int(m.group(1))
        if idx in by_index:
            raise SystemExit(
                f"Duplicate input for index {idx}: {by_index[idx].name} and {p.name}.\n"
                f"Keep only one of them (either .pptx or .pdf) and rerun."
            )
        by_index[idx] = p
    return [by_index[i] for i in sorted(by_index)]


def build_args() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="clevernotes",
        description="Generate study notes from lecture PPTX/PDF files in the current folder.",
    )
    ap.add_argument("--reset-presets", action="store_true",
                    help="Re-ask the learning-style questionnaire even if answers are cached.")
    ap.add_argument("--default", dest="use_defaults", action="store_true",
                    help="Skip the questionnaire and use sensible defaults.")
    ap.add_argument("--version", action="version", version=f"clevernotes {__version__}")
    return ap


def _explain_failure(exc: Exception) -> str:
    if isinstance(exc, PermanentLLMError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _handle_interrupt() -> int:
    """Print a friendly Ctrl+C summary and return an exit code.

    Called from the outer KeyboardInterrupt handler in main(). We don't try
    to report which stage was active — the on-disk state already reflects
    everything that completed (Stage 1 writes summary_*.json atomically at
    the end of each file, Stage 2 writes grouping_*.json atomically, Stage 3
    appends per-group markers to combined_notes.md after each group). A
    simple resume instruction covers every case.
    """
    console.print()  # land on a fresh line after the visible ^C
    warn("Interrupted (Ctrl+C). Stopping safely.")
    info("Everything completed before the interruption is saved on disk:")
    info("  - Stages 0/1/2 artifacts (PNGs + JSONs) persist in NOTES/file_N/")
    info("  - Stage 3 groups that finished writing have a completion marker")
    info("    in NOTES/final_notes/combined_notes.md and NOTES/final_notes/file_N.md")
    info("Rerun `clevernotes` in this folder to resume from where it stopped.")
    return 130  # conventional exit code for SIGINT


def _run_pipeline(argv: list[str] | None) -> int:
    args = build_args().parse_args(argv)
    cwd = Path.cwd()

    pptxs = discover_inputs(cwd)
    if not pptxs:
        err("No input files found in this folder.")
        info("Expected filenames: file_1.pptx or file_1.pdf, file_2.pptx or file_2.pdf, ...")
        return 1

    info(f"Found {len(pptxs)} input file(s): {', '.join(p.name for p in pptxs)}")

    try:
        cfg = config.load()
    except SystemExit as exc:
        err(str(exc))
        return 1

    notes_dir = cwd / "NOTES"
    notes_dir.mkdir(exist_ok=True)

    # All user-facing deliverables (.md + .pdf from Stage 4) land in
    # NOTES/final_notes/. The slide PNGs and pipeline JSONs stay at
    # NOTES/file_N/ — that's internal plumbing the reader doesn't want to see.
    final_dir = notes_dir / "final_notes"
    final_dir.mkdir(exist_ok=True)

    answers = presets.load_or_ask(
        notes_dir,
        reset=args.reset_presets,
        use_defaults=args.use_defaults,
    )
    prompt_suffix = presets.build_prompt_suffix(answers)

    try:
        clients = Clients.from_cfg(cfg)
    except Exception as exc:  # noqa: BLE001
        err(f"Failed to initialize Google GenAI clients: {exc}")
        return 1

    max_slides = int(cfg["MAX_SLIDES_PER_GROUP"])
    max_groups = int(cfg["MAX_GROUPS_PER_PPTX"])
    poppler_path = convert.get_poppler_path(cfg)

    per_pptx_dirs: dict[str, Path] = {}
    for pptx in pptxs:
        m = INPUT_PATTERN.match(pptx.name)
        assert m
        name = f"file_{int(m.group(1))}"
        per_pptx_dirs[pptx.name] = notes_dir / name

    # Track failures across stages so the final summary is honest about what
    # got done vs what didn't. The pipeline keeps running past one file's
    # permanent failure — other files are independent and still worth doing.
    failures: dict[str, list[str]] = {p.name: [] for p in pptxs}

    # ---- Stage 0: convert + stamp (per input, idempotent via existing PNGs) ----
    banner("Stage 0/4: Converting inputs -> PNG and stamping page numbers")
    for pptx in pptxs:
        out_dir = per_pptx_dirs[pptx.name]
        summary_path = out_dir / f"summary_{out_dir.name}.json"
        if summary_path.exists():
            info(f"{pptx.name}: summary exists, skipping conversion.")
            continue
        existing = [p for p in out_dir.glob("*.png")] if out_dir.exists() else []
        if existing:
            info(f"{pptx.name}: {len(existing)} PNG(s) already present, skipping conversion.")
            continue
        is_pdf = pptx.suffix.lower() == ".pdf"
        spinner_msg = (
            f"{pptx.name}: pdf2image..." if is_pdf
            else f"{pptx.name}: LibreOffice + pdf2image..."
        )
        with spinner(spinner_msg) as set_msg:
            try:
                if is_pdf:
                    pngs = convert.pdf_to_pngs(pptx, out_dir, poppler_path=poppler_path)
                else:
                    pngs = convert.pptx_to_pngs(pptx, out_dir, poppler_path=poppler_path)
                set_msg(f"{pptx.name}: stamping page numbers on {len(pngs)} slides...")
                stamp.stamp_all(pngs)
            except Exception as exc:  # noqa: BLE001
                err(f"{pptx.name}: conversion failed: {_explain_failure(exc)}")
                failures[pptx.name].append(f"Stage 0 (convert): {exc}")
                continue
        ok(f"{pptx.name}: {len(pngs)} slides ready.")

    # ---- Stage 1: classify (across all files, sequentially) ----
    banner("Stage 1/4: Classifying slides (USEFUL / USELESS)")
    entries: list[tuple[str, str, int]] = []
    stage1_todo: list[tuple[Path, Path, list[Path]]] = []
    for pptx in pptxs:
        if failures[pptx.name]:
            continue  # skip — Stage 0 failed
        out_dir = per_pptx_dirs[pptx.name]
        summary_path = out_dir / f"summary_{out_dir.name}.json"
        if summary_path.exists():
            info(f"{pptx.name}: already classified, skipping.")
            continue
        pngs = convert.slides_in_dir(out_dir)
        if not pngs:
            warn(f"{pptx.name}: no slides to classify.")
            continue
        stage1_todo.append((pptx, out_dir, pngs))
        entries.append((pptx.name, pptx.name, len(pngs)))

    if stage1_todo:
        with bars(entries) as tracker:
            for pptx, out_dir, pngs in stage1_todo:
                try:
                    classify.classify_file(clients, out_dir, pngs, tracker, pptx.name)
                    tracker.complete(pptx.name)
                except Exception as exc:  # noqa: BLE001
                    err(f"{pptx.name}: Stage 1 failed: {_explain_failure(exc)}")
                    failures[pptx.name].append(f"Stage 1 (classify): {exc}")
        ok("Stage 1 complete.")
    else:
        info("Nothing to do in Stage 1.")

    # ---- Stage 2: group (across all files, sequentially) ----
    banner("Stage 2/4: Grouping slides by topic")
    stage2_ran = False
    for pptx in pptxs:
        if failures[pptx.name]:
            continue  # skip — earlier stage failed
        out_dir = per_pptx_dirs[pptx.name]
        grouping_path = out_dir / f"{out_dir.name}_grouping_phase_summary.json"
        if grouping_path.exists():
            info(f"{pptx.name}: already grouped, skipping.")
            continue
        stage2_ran = True
        with spinner(f"{pptx.name}: grouping...") as _set:
            try:
                group.group_file(clients, out_dir, max_slides=max_slides, max_groups=max_groups)
            except Exception as exc:  # noqa: BLE001
                err(f"{pptx.name}: Stage 2 failed: {_explain_failure(exc)}")
                failures[pptx.name].append(f"Stage 2 (group): {exc}")
                continue
        try:
            grouping = json.loads(grouping_path.read_text())
            ok(f"{pptx.name}: {grouping.get('no_of_groups', 0)} group(s).")
        except Exception:  # noqa: BLE001
            pass
    if not stage2_ran:
        info("Nothing to do in Stage 2.")

    # ---- Stage 3: generate notes (across all files, sequentially) ----
    banner("Stage 3/4: Generating study notes")
    combined_md = final_dir / "combined_notes.md"
    # Per-file .md: each pptx gets its own document (file_1.md, file_2.md, ...)
    # so the user can read one deck's notes in isolation. Content is identical
    # to what lands in combined_notes.md — both get appended to as we go.
    per_file_mds: dict[str, Path] = {
        per_pptx_dirs[p.name].name: final_dir / f"{per_pptx_dirs[p.name].name}.md"
        for p in pptxs
    }
    pptx_names = [per_pptx_dirs[p.name].name for p in pptxs]
    completed_files, completed_groups = notes.prepare_notes_md(
        combined_md, per_file_mds, pptx_names,
    )

    # Status shown above the progress bars. Source of truth is the completion
    # markers in combined_notes.md:
    #   DONE            — pptx has `<!-- clevernotes:file_N:complete -->`
    #   PARTIALLY DONE  — pptx has >=1 group-complete marker but no file marker
    #   NOT STARTED     — pptx has no markers at all
    # The map is mutated by the `on_group_done`/`on_file_done` callbacks so
    # the header re-renders live as Stage 3 progresses.
    def _initial_status(internal_name: str) -> str:
        if internal_name in completed_files:
            return "DONE"
        if completed_groups.get(internal_name):
            return "PARTIALLY DONE"
        return "NOT STARTED"

    status_state: dict[str, str] = {
        per_pptx_dirs[p.name].name: _initial_status(per_pptx_dirs[p.name].name)
        for p in pptxs
    }

    def _combined_status() -> str:
        vals = list(status_state.values())
        if not vals:
            return "NOT STARTED"
        if all(v == "DONE" for v in vals):
            return "DONE"
        if all(v == "NOT STARTED" for v in vals):
            return "NOT STARTED"
        return "PARTIALLY DONE"

    _STATUS_STYLE = {
        "DONE": "bold green",
        "PARTIALLY DONE": "bold yellow",
        "NOT STARTED": "dim",
    }

    def _render_status_header() -> Text:
        t = Text()
        t.append(
            "Note documents (appended live as groups finish):\n",
            style="dim",
        )
        cs = _combined_status()
        t.append(f"  {combined_md}  [", style="dim")
        t.append(cs, style=_STATUS_STYLE[cs])
        t.append("]\n")
        for p in pptxs:
            name = per_pptx_dirs[p.name].name
            per = per_file_mds[name]
            s = status_state[name]
            t.append(f"  {per}  [", style="dim")
            t.append(s, style=_STATUS_STYLE[s])
            t.append("]\n")
        return t

    stage3_todo: list[tuple[Path, Path, int, set[str]]] = []
    entries_3: list[tuple[str, str, int]] = []
    for pptx in pptxs:
        if failures[pptx.name]:
            continue  # earlier stage failed — can't generate notes
        out_dir = per_pptx_dirs[pptx.name]
        if out_dir.name in completed_files:
            info(f"{pptx.name}: notes already generated, skipping.")
            continue
        grouping_path = out_dir / f"{out_dir.name}_grouping_phase_summary.json"
        if not grouping_path.exists():
            warn(f"{pptx.name}: no grouping output, skipping Stage 3.")
            failures[pptx.name].append("Stage 3: grouping JSON missing")
            continue
        grouping = json.loads(grouping_path.read_text())
        n_groups = grouping.get("no_of_groups", 0)
        if n_groups == 0:
            info(f"{pptx.name}: no groups, skipping.")
            continue
        done_ids = completed_groups.get(out_dir.name, set())
        if done_ids:
            info(f"{pptx.name}: resuming — {len(done_ids)}/{n_groups} group(s) already done.")
        stage3_todo.append((pptx, out_dir, n_groups, done_ids))
        entries_3.append((pptx.name, pptx.name, n_groups))

    if stage3_todo:
        with bars_with_status(_render_status_header, entries_3) as tracker:
            for pptx, out_dir, _n, done_ids in stage3_todo:
                out_name = out_dir.name

                # Closures capturing `out_name` so the tracker can flip this
                # file's status label without hardcoding anything in notes.py.
                def _on_group(name: str = out_name) -> None:
                    # Stay PARTIALLY DONE until the file-complete marker lands;
                    # don't downgrade a DONE file (shouldn't happen, defensive).
                    if status_state[name] != "DONE":
                        status_state[name] = "PARTIALLY DONE"
                    tracker.refresh_status()

                def _on_file(name: str = out_name) -> None:
                    status_state[name] = "DONE"
                    tracker.refresh_status()

                try:
                    notes.generate_for_pptx(
                        clients, out_dir,
                        combined_md, per_file_mds[out_name],
                        prompt_suffix,
                        tracker, pptx.name,
                        already_done_group_ids=done_ids,
                        on_group_done=_on_group,
                        on_file_done=_on_file,
                    )
                    tracker.complete(pptx.name)
                except Exception as exc:  # noqa: BLE001
                    err(f"{pptx.name}: Stage 3 failed: {_explain_failure(exc)}")
                    failures[pptx.name].append(f"Stage 3 (notes): {exc}")
                    # Keep going — other files are independent.
        ok("Stage 3 complete.")
    else:
        info("Nothing to do in Stage 3.")

    # ---- Stage 4: render PDFs (combined + per-file) ----
    banner("Stage 4/4: Converting notes to PDF")
    missing_tools = pdf.check_tools()
    if missing_tools:
        warn(f"Skipping PDF render — missing tool(s): {', '.join(missing_tools)}")
        info("Install pandoc + a xelatex-capable TeX distribution, then rerun "
             "`clevernotes` — stages 0-3 will no-op and only Stage 4 will run.")
    else:
        pdf_targets: list[Path] = []
        if combined_md.exists() and combined_md.stat().st_size > 0:
            pdf_targets.append(combined_md)
        for pptx in pptxs:
            if failures[pptx.name]:
                continue
            per = per_file_mds[per_pptx_dirs[pptx.name].name]
            if per.exists() and per.stat().st_size > 0:
                pdf_targets.append(per)

        if not pdf_targets:
            info("Nothing to render.")
        else:
            # Skip PDFs that already exist and are newer than their source .md.
            # Catches the common "rerun clevernotes after everything finished"
            # case where stages 0-3 no-op and rebuilding 7 PDFs is pure waste,
            # while still picking up mds touched by a fresh group in this run.
            to_render: list[Path] = []
            for md in pdf_targets:
                out = md.with_suffix(".pdf")
                if out.exists() and out.stat().st_mtime >= md.stat().st_mtime:
                    info(f"{md.name}: PDF up-to-date, skipping.")
                    continue
                to_render.append(md)

            if not to_render:
                info("All PDFs up-to-date.")
            else:
                for md in to_render:
                    with spinner(f"{md.name} -> PDF...") as _set:
                        try:
                            out = pdf.convert_md(md)
                        except Exception as exc:  # noqa: BLE001
                            err(f"{md.name}: pandoc failed: {_explain_failure(exc)}")
                            continue
                    ok(f"{md.name} -> {out}")

    # ---- Final summary ----
    banner("Done")
    any_failed = any(failures[p.name] for p in pptxs)
    any_succeeded = any(not failures[p.name] for p in pptxs)

    if any_succeeded:
        ok(f"Combined notes: {combined_md}")
        for pptx in pptxs:
            if failures[pptx.name]:
                continue
            per = per_file_mds[per_pptx_dirs[pptx.name].name]
            info(f"  per-file: {per}")

    if any_failed:
        warn("Some files did not complete:")
        for pptx in pptxs:
            fs = failures[pptx.name]
            if not fs:
                continue
            for f in fs:
                err(f"  {pptx.name}: {f}")
        info(
            "Most transient errors auto-retry. What you see here means the error was "
            "PERMANENT (e.g. bad API key, unknown model name, corrupt input). Fix the "
            "underlying issue and rerun `clevernotes` — completed groups are preserved "
            "and only the missing ones will be regenerated."
        )
        return 2  # distinct from total-abort (1) so a caller can tell partial success

    return 0


def main(argv: list[str] | None = None) -> int:
    """Top-level entrypoint. Wraps the pipeline in a KeyboardInterrupt guard
    so Ctrl+C shows a friendly resume message instead of a stack trace."""
    # Restore the default SIGINT handler (pytest/debuggers sometimes disable
    # it); this lets KeyboardInterrupt propagate on Ctrl+C so our handler
    # runs.
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except (ValueError, OSError):
        pass  # signal() fails if we're not on the main thread; safe to ignore

    try:
        return _run_pipeline(argv)
    except KeyboardInterrupt:
        return _handle_interrupt()


if __name__ == "__main__":
    sys.exit(main())
