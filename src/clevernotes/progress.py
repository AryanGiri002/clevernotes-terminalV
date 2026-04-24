from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)

console = Console()


def banner(text: str) -> None:
    console.print()
    console.rule(f"[bold cyan]{text}")


def info(text: str) -> None:
    console.print(f"[dim]{text}")


def warn(text: str) -> None:
    console.print(f"[yellow]! {text}")


def err(text: str) -> None:
    console.print(f"[red]x {text}")


def ok(text: str) -> None:
    console.print(f"[green]✓[/green] {text}")


@dataclass
class Tracker:
    progress: Progress
    task_ids: dict[str, int]

    def advance(self, key: str, n: int = 1) -> None:
        self.progress.advance(self.task_ids[key], n)

    def set_description(self, key: str, desc: str) -> None:
        self.progress.update(self.task_ids[key], description=desc)

    def complete(self, key: str, desc: str | None = None) -> None:
        tid = self.task_ids[key]
        task = self.progress.tasks[tid]
        self.progress.update(tid, completed=task.total or 0)
        if desc is not None:
            self.progress.update(tid, description=desc)


@contextmanager
def bars(entries: list[tuple[str, str, int]]):
    """entries: list of (key, description, total)."""
    progress = Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )
    task_ids: dict[str, int] = {}
    with progress:
        for key, desc, total in entries:
            task_ids[key] = progress.add_task(desc, total=total)
        yield Tracker(progress, task_ids)


@dataclass
class StatusTracker(Tracker):
    """Tracker variant that can trigger a redraw of a dynamic header.

    Used by `bars_with_status`: the header is a callable, and calling
    `refresh_status()` re-invokes it to pick up any mutated state.
    """
    live: Live = None  # type: ignore[assignment]
    render_group: Callable[[], RenderableType] = None  # type: ignore[assignment]

    def refresh_status(self) -> None:
        if self.live is not None and self.render_group is not None:
            self.live.update(self.render_group())


@contextmanager
def bars_with_status(
    render_header: Callable[[], RenderableType],
    entries: list[tuple[str, str, int]],
):
    """Like `bars()`, but renders a dynamic header above the progress bars
    that can be re-rendered on demand.

    `render_header` is a zero-arg callable returning a Rich renderable
    (Text, Panel, whatever). It's called on every `tracker.refresh_status()`
    to rebuild the header from current caller-side state, so the header
    stays in sync as files transition NOT STARTED → PARTIALLY DONE → DONE.
    """
    progress = Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )
    task_ids: dict[str, int] = {}

    def _group() -> RenderableType:
        return Group(render_header(), progress)

    with Live(_group(), console=console, refresh_per_second=4, transient=False) as live:
        for key, desc, total in entries:
            task_ids[key] = progress.add_task(desc, total=total)
        yield StatusTracker(progress, task_ids, live=live, render_group=_group)


@contextmanager
def spinner(message: str):
    progress = Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        console=console,
        transient=True,
    )
    with progress:
        task_id = progress.add_task(message, total=None)
        yield lambda new_msg: progress.update(task_id, description=new_msg)


def _fmt_wait(sleep_s: int) -> str:
    if sleep_s < 60:
        return f"{sleep_s}s"
    if sleep_s < 3600:
        return f"{sleep_s // 60}m{sleep_s % 60:02d}s"
    return f"{sleep_s // 3600}h{(sleep_s % 3600) // 60:02d}m"


def retry_message(label: str, attempt: int, sleep_s: int, exc: Exception) -> None:
    # Trim long error strings so the progress view stays readable
    exc_str = str(exc).strip().replace("\n", " ")
    if len(exc_str) > 140:
        exc_str = exc_str[:137] + "..."
    warn(
        f"{label}: transient error (attempt {attempt}) "
        f"[{type(exc).__name__}: {exc_str}] — waiting {_fmt_wait(sleep_s)} then retrying"
    )
