"""Trim trailing background rows from the console screenshots."""

import pathlib
import sys

from PIL import Image

BG = (10, 11, 14)  # --bg
PAD = 56


def is_background(pixel) -> bool:
    return all(abs(a - b) <= 2 for a, b in zip(pixel, BG))


def trim(source: str, target: str) -> None:
    image = Image.open(source).convert("RGB")
    width, height = image.size
    pixels = image.load()

    last = height - 1
    for y in range(height - 1, -1, -1):
        if any(not is_background(pixels[x, y]) for x in range(0, width, 11)):
            last = y
            break

    bottom = min(height, last + PAD)
    cropped = image.crop((0, 0, width, bottom))
    pathlib.Path(target).parent.mkdir(parents=True, exist_ok=True)
    cropped.save(target, optimize=True)
    print(f"{target}: {width}x{bottom} ({pathlib.Path(target).stat().st_size // 1024} KB)")


if __name__ == "__main__":
    args = sys.argv[1:]
    pairs = (
        list(zip(args[0::2], args[1::2]))
        if args
        else [("raw-01.png", "docs/console.png"), ("raw-04.png", "docs/console-branch.png")]
    )
    for source, target in pairs:
        trim(source, target)
