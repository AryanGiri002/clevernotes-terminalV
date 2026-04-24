from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pdf2image import convert_from_path


def _find_libreoffice() -> str:
    for name in ("libreoffice", "soffice"):
        p = shutil.which(name)
        if p:
            return p
    mac_default = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if sys.platform == "darwin" and Path(mac_default).exists():
        return mac_default
    raise SystemExit("LibreOffice not found on PATH. Re-run the installer.")


def _pdf_to_pngs(pdf: Path, out_dir: Path, poppler_path: str | None) -> list[Path]:
    """Rasterize a PDF into `1.png, 2.png, ...` under out_dir. Shared by the
    pptx path (via LibreOffice → temp pdf → here) and the native-pdf path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    poppler_kw = {"poppler_path": poppler_path} if poppler_path else {}
    images = convert_from_path(str(pdf), dpi=150, fmt="png", **poppler_kw)

    written: list[Path] = []
    for idx, img in enumerate(images, start=1):
        path = out_dir / f"{idx}.png"
        img.save(path, "PNG")
        written.append(path)
    return written


def pptx_to_pngs(pptx: Path, out_dir: Path, poppler_path: str | None = None) -> list[Path]:
    soffice = _find_libreoffice()
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        result = subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(td_path),
                str(pptx),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice conversion failed for {pptx.name}:\n{result.stderr}"
            )
        pdfs = list(td_path.glob("*.pdf"))
        if not pdfs:
            raise RuntimeError(f"LibreOffice produced no PDF for {pptx.name}")
        return _pdf_to_pngs(pdfs[0], out_dir, poppler_path)


def pdf_to_pngs(pdf: Path, out_dir: Path, poppler_path: str | None = None) -> list[Path]:
    """Rasterize a user-supplied PDF straight to slide PNGs, skipping the
    LibreOffice hop that .pptx inputs need."""
    return _pdf_to_pngs(pdf, out_dir, poppler_path)


def slides_in_dir(dir_: Path) -> list[Path]:
    """Return existing non-discarded slide PNGs in numeric order."""
    pngs = [
        p for p in dir_.glob("*.png")
        if "_DISCARDED" not in p.stem and p.stem.isdigit()
    ]
    pngs.sort(key=lambda p: int(p.stem))
    return pngs


def get_poppler_path(cfg: dict[str, str]) -> str | None:
    return cfg.get("POPPLER_PATH") or os.environ.get("POPPLER_PATH") or None
