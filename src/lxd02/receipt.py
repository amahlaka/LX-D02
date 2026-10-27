"""Render receipts for the LX-D02: a 384 px wide strip of any length.

No Home Assistant imports: part of the lxd02 package, and copied unchanged
into the Home Assistant integration (HA-LXD02).
Blocks are stacked top to bottom: icon, title, text, QR code, picture. The
text is Markdown (see markdown.py) unless markdown=False.
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

from .markdown import ImageLoader, Renderer, Style, Typesetter, render_markdown

WIDTH = 384
MARGIN = 8
GAP = 16
ASSETS = Path(__file__).with_name("assets")
MDI_FONT = ASSETS / "materialdesignicons-webfont.ttf"
MDI_CODEPOINTS = ASSETS / "mdi-codepoints.json"
SIZES = {"small": 20, "normal": 24, "large": 32}


@cache
def _mdi_codepoints() -> dict[str, int]:
    return json.loads(MDI_CODEPOINTS.read_text())


def mdi_glyph(icon: str) -> str:
    """The font character for an icon name such as "mdi:coffee"."""
    name = icon.removeprefix("mdi:").strip()
    try:
        return chr(_mdi_codepoints()[name])
    except KeyError:
        raise ValueError(f"unknown MDI icon: {icon}") from None


def qr_image(data: str, size: int = 240) -> Image.Image:
    qr = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image().get_image().convert("L")
    scale = max(1, size // img.width)  # whole-dot modules stay sharp
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


def icon_image(icon: str, size: int = 96) -> Image.Image:
    font = ImageFont.truetype(str(MDI_FONT), size)
    img = Image.new("L", (size, size), 255)
    ImageDraw.Draw(img).text((size // 2, size // 2), mdi_glyph(icon), font=font, fill=0, anchor="mm")
    return img


def fit_picture(picture: Image.Image, width: int = WIDTH) -> Image.Image:
    """Scale a picture to the paper width, flattening any transparency onto white."""
    picture = picture.convert("RGBA")
    flat = Image.new("RGBA", picture.size, "white")
    flat.alpha_composite(picture)
    height = max(1, round(flat.height * width / flat.width))
    return flat.convert("L").resize((width, height), Image.LANCZOS)


def _with_margin(img: Image.Image) -> Image.Image:
    out = Image.new("L", (WIDTH, img.height), 255)
    out.paste(img, (MARGIN, 0))
    return out


def render_receipt(title: str = "", text: str = "", qr: str | None = None,
                   icon: str | None = None, picture: Image.Image | None = None,
                   size: str = "normal", align: str = "left", markdown: bool = True,
                   load_image: ImageLoader | None = None) -> Image.Image:
    """Stack the given blocks into one WIDTH px wide image.

    `load_image(path)` opens pictures referenced from the Markdown text as
    ![alt](path); without it they print as "[alt]".
    """
    inner = WIDTH - 2 * MARGIN
    blocks: list[Image.Image] = []
    if icon:
        blocks.append(icon_image(icon))
    if title:
        # Shrink a title with a long word (down to the body size) rather than breaking it.
        longest = max(title.split(), key=len, default="")
        title_size = round(SIZES[size] * 1.4)
        while title_size > SIZES[size] and Typesetter(title_size).width(longest, Style(bold=True)) > inner:
            title_size -= 1
        ts = Typesetter(title_size)
        renderer = Renderer(SIZES[size])
        blocks.append(_with_margin(renderer.text_block(ts, [(title, Style(bold=True))], inner, "center")))
    if text:
        if markdown:
            body = render_markdown(text, inner, SIZES[size], align, load_image)
        else:
            renderer = Renderer(SIZES[size])
            body = renderer.text_block(renderer.body, [(text, Style())], inner, align)
        blocks.append(_with_margin(body))
    if qr:
        blocks.append(qr_image(qr))
    if picture is not None:
        blocks.append(fit_picture(picture))
    if not blocks:
        raise ValueError("nothing to print: give a title, text, QR code, icon or picture")
    height = sum(b.height for b in blocks) + GAP * (len(blocks) - 1)
    img = Image.new("L", (WIDTH, height), 255)
    y = 0
    for block in blocks:
        img.paste(block, ((WIDTH - block.width) // 2, y))
        y += block.height + GAP
    return img
