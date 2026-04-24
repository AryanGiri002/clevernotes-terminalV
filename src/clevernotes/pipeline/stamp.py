from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def stamp_page_number(png: Path, number: int) -> None:
    img = Image.open(png).convert("RGBA")
    w, h = img.size
    font_size = max(24, h // 30)
    font = _font(font_size)

    text = str(number)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    pad = font_size // 2
    margin = font_size
    box_w = tw + pad * 2
    box_h = th + pad * 2
    x0 = w - box_w - margin
    y0 = h - box_h - margin

    draw.rectangle([x0, y0, x0 + box_w, y0 + box_h], fill=(0, 0, 0, 180))
    draw.text((x0 + pad - bbox[0], y0 + pad - bbox[1]), text, fill=(255, 255, 255, 255), font=font)

    out = Image.alpha_composite(img, overlay).convert("RGB")
    out.save(png, "PNG")


def stamp_all(pngs: list[Path]) -> None:
    for p in pngs:
        if not p.stem.isdigit():
            continue
        stamp_page_number(p, int(p.stem))
