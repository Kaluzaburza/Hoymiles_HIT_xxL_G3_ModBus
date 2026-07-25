"""Generate deterministic local brand assets for the HACS integration."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "custom_components" / "hoymiles_hit_modbus" / "brand"
BRAND.mkdir(parents=True, exist_ok=True)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a commonly available font with a safe fallback."""
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def draw_mark(size: int, dark: bool) -> Image.Image:
    """Draw a square inverter/energy mark."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    cyan = (0, 188, 212, 255)
    green = (63, 185, 80, 255)
    violet = (126, 87, 194, 255)
    foreground = (244, 247, 250, 255) if dark else (24, 38, 54, 255)

    pad = size * 0.08
    draw.rounded_rectangle(
        (pad, pad, size - pad, size - pad),
        radius=size * 0.22,
        fill=(16, 30, 46, 255) if dark else (245, 249, 252, 255),
        outline=cyan,
        width=max(4, size // 40),
    )

    # Solar input, DC/battery and AC/grid paths meet in an H-shaped inverter.
    draw.line(
        (size * 0.24, size * 0.28, size * 0.42, size * 0.42),
        fill=green,
        width=max(8, size // 18),
    )
    draw.line(
        (size * 0.76, size * 0.28, size * 0.58, size * 0.42),
        fill=cyan,
        width=max(8, size // 18),
    )
    draw.line(
        (size * 0.50, size * 0.76, size * 0.50, size * 0.59),
        fill=violet,
        width=max(8, size // 18),
    )
    draw.line(
        (size * 0.40, size * 0.35, size * 0.40, size * 0.67),
        fill=foreground,
        width=max(10, size // 15),
    )
    draw.line(
        (size * 0.60, size * 0.35, size * 0.60, size * 0.67),
        fill=foreground,
        width=max(10, size // 15),
    )
    draw.line(
        (size * 0.40, size * 0.51, size * 0.60, size * 0.51),
        fill=foreground,
        width=max(10, size // 15),
    )

    for x, y, color in (
        (0.21, 0.25, green),
        (0.79, 0.25, cyan),
        (0.50, 0.79, violet),
    ):
        radius = size * 0.045
        draw.ellipse(
            (
                size * x - radius,
                size * y - radius,
                size * x + radius,
                size * y + radius,
            ),
            fill=color,
        )
    return image


def draw_logo(dark: bool) -> Image.Image:
    """Draw a wide logo with the integration name."""
    width, height = 800, 240
    background = (18, 28, 41, 255) if dark else (255, 255, 255, 255)
    foreground = (244, 247, 250, 255) if dark else (24, 38, 54, 255)
    muted = (175, 190, 205, 255) if dark else (76, 94, 112, 255)
    image = Image.new("RGBA", (width, height), background)
    image.alpha_composite(draw_mark(190, dark), (25, 25))
    draw = ImageDraw.Draw(image)
    draw.text((240, 55), "Hoymiles HIT xxL G3", font=font(42, True), fill=foreground)
    draw.text((242, 120), "Modbus • ESPHome • Home Assistant", font=font(25), fill=muted)
    return image


draw_mark(256, False).save(BRAND / "icon.png")
draw_mark(256, True).save(BRAND / "dark_icon.png")
draw_logo(False).save(BRAND / "logo.png")
draw_logo(True).save(BRAND / "dark_logo.png")
print(f"Generated brand assets in {BRAND}")
