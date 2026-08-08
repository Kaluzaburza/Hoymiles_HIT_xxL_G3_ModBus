"""Build the four-panel dashboard screenshot used by the project README."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


PANELS = (
    ("Panel główny", "dashboard-start-v1.4.4.png", "#00B0FF"),
    ("Automatyka RCE", "dashboard-rce-v1.4.4.png", "#FFD600"),
    ("Tanie ładowanie", "dashboard-tariff-v1.4.4.png", "#00E676"),
    ("RCEm 253 V+", "dashboard-rcem-v1.4.4.png", "#FF6D00"),
)

CANVAS_SIZE = (1920, 1104)
MARGIN = 16
GAP = 16


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    ):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def build_collage(image_dir: Path, output: Path) -> None:
    canvas = Image.new("RGB", CANVAS_SIZE, "#0D1117")
    cell_width = (CANVAS_SIZE[0] - (2 * MARGIN) - GAP) // 2
    cell_height = (CANVAS_SIZE[1] - (2 * MARGIN) - GAP) // 2
    font = _font(27)

    for index, (label, filename, accent) in enumerate(PANELS):
        source_path = image_dir / filename
        with Image.open(source_path) as source:
            panel = ImageOps.fit(
                source.convert("RGB"),
                (cell_width, cell_height),
                method=Image.Resampling.LANCZOS,
            )

        column = index % 2
        row = index // 2
        x = MARGIN + column * (cell_width + GAP)
        y = MARGIN + row * (cell_height + GAP)
        canvas.paste(panel, (x, y))

        overlay = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        text_box = draw.textbbox((0, 0), label, font=font)
        label_width = text_box[2] - text_box[0] + 34
        label_height = text_box[3] - text_box[1] + 22
        label_box = (x + 14, y + 14, x + 14 + label_width, y + 14 + label_height)
        draw.rounded_rectangle(
            label_box,
            radius=12,
            fill=(13, 17, 23, 226),
            outline=accent,
            width=3,
        )
        draw.text(
            (label_box[0] + 17, label_box[1] + 8),
            label,
            font=font,
            fill="#FFFFFF",
        )
        canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")

        border = ImageDraw.Draw(canvas)
        border.rounded_rectangle(
            (x, y, x + cell_width - 1, y + cell_height - 1),
            radius=8,
            outline=accent,
            width=2,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=Path("docs/images"),
        help="Directory containing the four source screenshots.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/images/dashboard-overview.png"),
        help="Destination PNG path.",
    )
    args = parser.parse_args()
    build_collage(args.image_dir, args.output)


if __name__ == "__main__":
    main()
