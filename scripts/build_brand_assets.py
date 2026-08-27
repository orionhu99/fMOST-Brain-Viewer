"""Build deterministic application logo and icon assets from the approved RGBA master."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
MASTER = ASSETS / "fmost_brain_logo_final_black.png"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def alpha_crop(image: Image.Image, threshold: int = 2) -> Image.Image:
    alpha = image.getchannel("A").point(lambda value: 255 if value > threshold else 0)
    bounds = alpha.getbbox()
    if bounds is None:
        raise ValueError("Approved logo master is fully transparent")
    return image.crop(bounds)


def black_background_crop(image: Image.Image, threshold: int = 8) -> Image.Image:
    """Crop visible logo content while retaining its approved black backdrop."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    visible = Image.fromarray(rgb.max(axis=2)).point(
        lambda value: 255 if value > threshold else 0
    )
    bounds = visible.getbbox()
    if bounds is None:
        raise ValueError("Approved logo master contains no visible artwork")
    return image.convert("RGBA").crop(bounds)


def black_to_alpha(image: Image.Image) -> Image.Image:
    """Recover a clean RGBA glow whose composite on black matches the master."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    alpha = rgb.max(axis=2, keepdims=True)
    red, green, blue = (rgb[:, :, index] for index in range(3))
    green_glow = (green > 8) & (green >= red * 1.15) & (green >= blue * 1.15)
    neutral_core = (rgb.min(axis=2) > 72) & (rgb.max(axis=2) - rgb.min(axis=2) < 64)
    alpha[~(green_glow | neutral_core), 0] = 0
    alpha[alpha < 8] = 0
    straight = np.divide(rgb * 255.0, alpha, out=np.zeros_like(rgb), where=alpha > 0)
    rgba = np.concatenate((straight, alpha), axis=2).clip(0, 255).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def contain(
    image: Image.Image,
    size: tuple[int, int],
    margin: float,
    background: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> Image.Image:
    canvas = Image.new("RGBA", size, background)
    maximum = (round(size[0] * (1 - 2 * margin)), round(size[1] * (1 - 2 * margin)))
    fitted = image.copy()
    fitted.thumbnail(maximum, Image.Resampling.LANCZOS)
    position = ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2)
    canvas.alpha_composite(fitted, position)
    return canvas


def main() -> None:
    black_master = Image.open(MASTER).convert("RGBA")
    master = black_to_alpha(black_master)
    master.save(ASSETS / "fmost_brain_logo_final_transparent.png", optimize=True)
    cropped = alpha_crop(master)

    logo = contain(cropped, (1536, 768), margin=0.05)
    logo.save(ASSETS / "fmost_brain_logo.png", optimize=True)

    black = Image.new("RGBA", logo.size, (0, 0, 0, 255))
    black.alpha_composite(logo)
    black.convert("RGB").save(ASSETS / "fmost_brain_logo_black.png", optimize=True)

    # The detailed pearl-white logo reads best as a desktop launcher on its
    # approved black field. Keeping that field also avoids dark color fringes
    # from reconstructing transparency around the fluorescent axon.
    icon_master = contain(
        black_background_crop(black_master),
        (1024, 1024),
        margin=0.07,
        background=(0, 0, 0, 255),
    )
    icon_master.save(ASSETS / "fmost_brain_icon.png", optimize=True)

    icon_dir = ASSETS / "icon_sizes"
    icon_dir.mkdir(exist_ok=True)
    rendered = []
    for size in ICON_SIZES:
        icon = icon_master.resize((size, size), Image.Resampling.LANCZOS)
        path = icon_dir / f"fmost_brain_icon_{size}.png"
        icon.save(path, optimize=True)
        rendered.append(icon)

    icon_master.save(
        ASSETS / "fmost_brain_viewer.ico",
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES if size >= 16],
    )


if __name__ == "__main__":
    main()
