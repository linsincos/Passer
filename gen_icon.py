"""Build Passer's PNG and multi-size ICO from passer_source.png."""
from pathlib import Path

from PIL import Image


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "passer_source.png"
ICON_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> None:
    source = Image.open(SOURCE).convert("RGBA")
    source.resize((256, 256), Image.Resampling.LANCZOS).save(HERE / "passer.png")
    source.save(HERE / "passer.ico", format="ICO", sizes=ICON_SIZES)
    source.resize((512, 512), Image.Resampling.LANCZOS).save(HERE / "passer_preview.png")
    print("wrote passer.ico, passer.png, passer_preview.png")


if __name__ == "__main__":
    main()
