"""Markdown → PDF conversion (pandoc + xelatex).

Core logic for Stage 4 of the clevernotes pipeline. Shell-outs to pandoc;
asset files (`header.tex`, `pagebreak.lua`) live next to this module so
they ship with the installed package.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


HEADER_TEX = Path(__file__).resolve().parent / "header.tex"
PAGEBREAK_LUA = Path(__file__).resolve().parent / "pagebreak.lua"


class MissingTools(RuntimeError):
    """pandoc and/or xelatex not on PATH."""

    def __init__(self, missing: list[str]):
        super().__init__(f"missing required tool(s): {', '.join(missing)}")
        self.missing = missing


def check_tools() -> list[str]:
    """Return the list of missing tools (empty if all present)."""
    return [t for t in ("pandoc", "xelatex") if shutil.which(t) is None]


def convert_md(
    md: Path,
    out_dir: Path | None = None,
    toc: bool = True,
    verbose: bool = False,
) -> Path:
    """Convert a single Markdown file to PDF via pandoc + xelatex.

    Returns the output PDF path. Raises CalledProcessError on pandoc failure.
    """
    out = (out_dir.resolve() if out_dir else md.parent) / (md.stem + ".pdf")
    out.parent.mkdir(parents=True, exist_ok=True)

    input_fmt = "markdown+lists_without_preceding_blankline-yaml_metadata_block"

    cmd: list[str] = [
        "pandoc",
        str(md),
        "-f", input_fmt,
        "-o", str(out),
        "--pdf-engine=xelatex",
        f"--resource-path={md.parent}{os.pathsep}{md.parent.parent}",
        "-V", "geometry:margin=1in",
        "-V", "documentclass=report",
        "-V", "colorlinks=true",
        "-V", "linkcolor=black",
        "-V", "urlcolor=blue",
        "-V", "toccolor=black",
        "-H", str(HEADER_TEX),
        "-L", str(PAGEBREAK_LUA),
    ]
    if toc:
        cmd += ["--toc", "--toc-depth=2", "-V", "toc-own-page=true"]

    if verbose:
        print("pdf: running:", " ".join(cmd))

    subprocess.run(cmd, check=True)
    return out
