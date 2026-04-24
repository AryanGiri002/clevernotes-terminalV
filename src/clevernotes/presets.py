from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Question:
    key: str
    prompt: str
    options: list[tuple[str, str]]  # (label, fragment-or-"" for no-op)
    default_idx: int


QUESTIONS: list[Question] = [
    Question(
        key="format",
        prompt="How should your notes be structured?",
        options=[
            ("Bullet points and lists (scannable)",
             "Structure the notes primarily as bullet points and nested lists for quick scanning."),
            ("Flowing paragraphs (textbook-like)",
             "Structure the notes as flowing paragraphs, like a textbook chapter."),
            ("Mix — bullets for facts, paragraphs for concepts",
             "Use a mix: bullet points for factual lists and paragraphs for conceptual explanations."),
        ],
        default_idx=2,
    ),
    Question(
        key="analogies",
        prompt="Do you want real-life analogies and everyday examples?",
        options=[
            ("Yes, use them wherever they help",
             "Where possible, explain concepts using real-world analogies that make abstract ideas concrete and relatable."),
            ("No, keep it strictly subject-focused", ""),
        ],
        default_idx=0,
    ),
    Question(
        key="depth",
        prompt="What level of technical depth do you want?",
        options=[
            ("Beginner-friendly, explain every term (ELI5-ish)",
             "Assume the reader is new to this material. Explain every technical term the first time it appears, and avoid jargon where plain language works."),
            ("Rigorous — assume I know the prerequisites",
             "Assume the reader has solid prerequisite knowledge. Prefer precise technical language over simplifications."),
            ("Balanced", ""),
        ],
        default_idx=2,
    ),
    Question(
        key="length",
        prompt="How detailed should the notes be?",
        options=[
            ("Concise — exam cram, just the essentials",
             "Keep the notes concise and exam-focused: emphasize definitions, key facts, and common pitfalls. Skip lengthy derivations unless critical."),
            ("Thorough — full explanations even for basics",
             "Be thorough: explain concepts from the ground up, include context and motivation, and don't skip steps."),
        ],
        default_idx=1,
    ),
    Question(
        key="examples",
        prompt="For math/code-heavy slides, include step-by-step worked examples?",
        options=[
            ("Yes — walk through sample problems",
             "For any math or code content, include a fully worked step-by-step example showing how to apply the concept."),
            ("No — just explain the concept", ""),
        ],
        default_idx=0,
    ),
    Question(
        key="review_questions",
        prompt="Add 2–3 quick review questions at the end of each group's notes?",
        options=[
            ("Yes",
             "At the end of the notes, add a short '### Review questions' section with 2–3 self-test questions that check understanding of the key ideas covered."),
            ("No", ""),
        ],
        default_idx=1,
    ),
]


DEFAULT_ANSWERS = {q.key: q.default_idx for q in QUESTIONS}


def _ask_once() -> dict[str, int]:
    print()
    print("Before we start — a few questions to tune how your notes are written.")
    print("For each question, type the OPTION NUMBER (e.g. 1, 2, or 3) and press Enter.")
    print("Press Enter on its own to accept the option marked (default).")
    print()
    answers: dict[str, int] = {}
    for i, q in enumerate(QUESTIONS, start=1):
        print(f"[{i}/{len(QUESTIONS)}] {q.prompt}")
        for j, (label, _) in enumerate(q.options):
            marker = "  (default)" if j == q.default_idx else ""
            print(f"  {j + 1}) {label}{marker}")
        while True:
            raw = input("Your choice [number, or Enter for default]: ").strip()
            if raw == "":
                answers[q.key] = q.default_idx
                default_label = q.options[q.default_idx][0]
                print(f"  → using default: {default_label}")
                break
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(q.options):
                    answers[q.key] = idx
                    print(f"  → selected: {q.options[idx][0]}")
                    break
            print(f"  (invalid — enter 1-{len(q.options)} or press Enter for default)")
        print()
    return answers


def _print_summary(answers: dict[str, int]) -> None:
    print()
    print("=" * 70)
    print("Your preset selections — these will be appended to the Stage 3 prompt:")
    print("=" * 70)
    for i, q in enumerate(QUESTIONS, start=1):
        idx = answers.get(q.key, q.default_idx)
        label, fragment = q.options[idx]
        print()
        print(f"[{i}] {q.prompt}")
        print(f"    Chosen: {label}")
        if fragment:
            print(f"    Prompt fragment added: \"{fragment}\"")
        else:
            print("    Prompt fragment added: (none — neutral / no-op option)")
    print()
    print("=" * 70)
    print()


def ask_interactive() -> dict[str, int]:
    while True:
        answers = _ask_once()
        _print_summary(answers)
        ans = input("Proceed with these presets? [Y = proceed / n = restart from Q1]: ").strip()
        if ans.lower() in ("n", "no"):
            print()
            print("Restarting the questionnaire from the top.")
            continue
        return answers


def load_or_ask(notes_dir: Path, reset: bool = False, use_defaults: bool = False) -> dict[str, int]:
    cache = notes_dir / ".presets.json"
    if use_defaults:
        return DEFAULT_ANSWERS.copy()
    if cache.exists() and not reset:
        try:
            data = json.loads(cache.read_text())
            if all(k in data for k in (q.key for q in QUESTIONS)):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    answers = ask_interactive()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(answers, indent=2))
    return answers


def build_prompt_suffix(answers: dict[str, int]) -> str:
    fragments: list[str] = []
    for q in QUESTIONS:
        idx = answers.get(q.key, q.default_idx)
        _, fragment = q.options[idx]
        if fragment:
            fragments.append(fragment)
    if not fragments:
        return ""
    return "\n\nAdditional style guidance:\n- " + "\n- ".join(fragments)
