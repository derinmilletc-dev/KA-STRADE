"""Generate all site icons from the KaisTrade logo's circular brand mark.

Two families:
- Browser-tab favicons (favicon.ico, favicon-*.png): round mark on a transparent
  canvas. The dark fill stays inside the green ring (up to the middle of the ring
  stroke), so no dark edge shows outside the circle on light or colored tab bars.
- Home-screen / app icons (apple-touch-icon.png, icon-*.png): opaque dark square,
  because iOS turns transparency black and launchers expect a full tile.
"""
from pathlib import Path

from PIL import Image, ImageDraw

PUBLIC = Path(__file__).resolve().parents[1] / "frontend" / "public"
BACKGROUND = (10, 17, 24, 255)
CANVAS = 512
SUPERSAMPLE = 4


def brand_mark() -> Image.Image:
    with Image.open(PUBLIC / "kaistrade-logo.png") as original:
        source = original.convert("RGBA")
    alpha = source.getchannel("A")
    bounds = alpha.getbbox()
    if bounds is None:
        raise ValueError("KaisTrade logo has no visible artwork")
    left, top, right, bottom = bounds
    mark_right = next(
        (x for x in range(left, right) if alpha.crop((x, top, x + 1, bottom)).getbbox() is None),
        None,
    )
    if mark_right is None or not 0.9 <= (mark_right - left) / (bottom - top) <= 1.1:
        raise ValueError("KaisTrade logo must start with a separate circular brand mark")
    return source.crop((left, top, mark_right, bottom))


def ring_geometry(mark: Image.Image) -> tuple[float, float, float, float]:
    """Center, outer radius and stroke width of the ring, from the opaque runs on the center lines."""
    alpha = mark.getchannel("A")
    width, height = mark.size

    def outer_runs(values: list[int]) -> tuple[int, int, int]:
        opaque = [index for index, value in enumerate(values) if value >= 128]
        if not opaque:
            raise ValueError("KaisTrade brand mark has no ring")
        first, last = opaque[0], opaque[-1]
        stroke = next(index for index in range(first, len(values)) if values[index] < 128) - first
        return first, last, stroke

    row_first, row_last, row_stroke = outer_runs([alpha.getpixel((x, height // 2)) for x in range(width)])
    col_first, col_last, col_stroke = outer_runs([alpha.getpixel((width // 2, y)) for y in range(height)])
    center_x, center_y = (row_first + row_last + 1) / 2, (col_first + col_last + 1) / 2
    outer_radius = min(row_last + 1 - row_first, col_last + 1 - col_first) / 2
    return center_x, center_y, outer_radius, (row_stroke + col_stroke) / 2


def tab_favicon(mark: Image.Image) -> Image.Image:
    """Transparent canvas, ring fills it edge to edge, dark fill confined inside the ring."""
    center_x, center_y, outer_radius, stroke = ring_geometry(mark)
    scale = (CANVAS - 8) / (2 * outer_radius)
    artwork = mark.resize((round(mark.width * scale), round(mark.height * scale)), Image.Resampling.LANCZOS)
    offset_x = round(CANVAS / 2 - center_x * scale)
    offset_y = round(CANVAS / 2 - center_y * scale)
    fill_radius = (outer_radius - stroke / 2) * scale

    big = CANVAS * SUPERSAMPLE
    disc = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    r = fill_radius * SUPERSAMPLE
    ImageDraw.Draw(disc).ellipse((big / 2 - r, big / 2 - r, big / 2 + r, big / 2 + r), fill=BACKGROUND)
    canvas = disc.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)
    canvas.alpha_composite(artwork, (offset_x, offset_y))
    return canvas


def app_icon(mark: Image.Image) -> Image.Image:
    canvas = Image.new("RGBA", (CANVAS, CANVAS), BACKGROUND)
    scale = 410 / max(mark.size)
    artwork = mark.resize((round(mark.width * scale), round(mark.height * scale)), Image.Resampling.LANCZOS)
    canvas.alpha_composite(artwork, ((CANVAS - artwork.width) // 2, (CANVAS - artwork.height) // 2))
    return canvas


def main():
    mark = brand_mark()
    favicon = tab_favicon(mark)
    for size in (48, 96, 192, 512):
        favicon.resize((size, size), Image.Resampling.LANCZOS).save(PUBLIC / f"favicon-{size}x{size}.png")
    # Pillow drops ICO sizes larger than the base image, so the largest frame is the base.
    frames = [favicon.resize((size, size), Image.Resampling.LANCZOS) for size in (48, 32, 16)]
    frames[0].save(PUBLIC / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)], append_images=frames[1:])

    tile = app_icon(mark)
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        tile.resize((size, size), Image.Resampling.LANCZOS).save(PUBLIC / name)


if __name__ == "__main__":
    main()
