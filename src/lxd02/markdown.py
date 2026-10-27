"""Render Markdown onto a receipt.

A small, dependency-free renderer for what makes sense on a 58 mm receipt:

  # Heading 1 / ## Heading 2 / ### Heading 3
  **bold**, *italic* / _italic_, ~~strikethrough~~, `code`, [link](url), \\escapes
  - bullet lists (nested by indenting), 1. numbered lists
  - [ ] checklists / - [x] ticked items
  > quotes
  ``` code blocks ```
  --- horizontal rules
  | tables | with | header |
  ![picture](path)        (only when the caller passes an image loader)

Unlike strict Markdown, every line break is kept, since that is what people
expect when typing a list or a note to print.

No Home Assistant imports: part of the lxd02 package, and copied unchanged
into the Home Assistant integration (HA-LXD02).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).with_name("assets")
FONT_FILES = {
    (False, False): "DejaVuSans.ttf",
    (True, False): "DejaVuSans-Bold.ttf",
    (False, True): "DejaVuSansMono.ttf",
    (True, True): "DejaVuSansMono-Bold.ttf",
}
ITALIC_SLANT = 0.22  # synthetic italic: horizontal shift per pixel of height
CODE_BACKGROUND = 215  # light gray, prints as sparse dots

ImageLoader = Callable[[str], Image.Image]


@cache
def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / FONT_FILES[(bold, mono)]), size)


# --- inline ------------------------------------------------------------------

@dataclass(frozen=True)
class Style:
    bold: bool = False
    italic: bool = False
    mono: bool = False
    strike: bool = False
    underline: bool = False


Run = tuple[str, Style]

_INLINE = re.compile(r"""
    (?P<esc>\\[\\`*_~\[\]()\#+\-.!>|])
  | (?P<ticks>`+)(?P<code>.+?)(?P=ticks)
  | \*\*(?P<b1>.+?)\*\*(?!\*)
  | (?<!\w)__(?P<b2>.+?)__(?!\w)
  | ~~(?P<s>.+?)~~
  | \*(?P<i1>[^*\s](?:.*?[^*\s])?)\*
  | (?<!\w)_(?P<i2>[^_\s](?:.*?[^_\s])?)_(?!\w)
  | !?\[(?P<link>[^\]]+)\]\((?P<url>[^)\s]*)\)
""", re.VERBOSE)


def parse_inline(text: str, style: Style = Style()) -> list[Run]:
    """Split text into runs of the same style."""
    runs: list[Run] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            runs.append((text[pos:m.start()], style))
        if m["esc"]:
            runs.append((m["esc"][1], style))
        elif m["ticks"]:
            runs.append((m["code"], replace(style, mono=True)))
        elif m["b1"] or m["b2"]:
            runs += parse_inline(m["b1"] or m["b2"], replace(style, bold=True))
        elif m["s"]:
            runs += parse_inline(m["s"], replace(style, strike=True))
        elif m["i1"] or m["i2"]:
            runs += parse_inline(m["i1"] or m["i2"], replace(style, italic=True))
        else:
            runs += parse_inline(m["link"], replace(style, underline=True))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], style))
    return runs


def plain_text(runs: list[Run]) -> str:
    return "".join(text for text, _ in runs)


class Typesetter:
    """Measures, wraps and draws styled runs at one font size."""

    def __init__(self, size: int) -> None:
        self.size = size
        ascent, descent = font(size).getmetrics()
        self.ascent = ascent
        self.line_height = ascent + descent + max(2, size // 8)

    def font(self, style: Style) -> ImageFont.FreeTypeFont:
        return font(self.size, style.bold, style.mono)

    def width(self, text: str, style: Style) -> int:
        return round(self.font(style).getlength(text))

    def runs_width(self, runs: list[Run]) -> int:
        return sum(self.width(t, s) for t, s in runs)

    def wrap(self, runs: list[Run], max_w: int) -> list[list[Run]]:
        """Greedy word wrap; over-long words are broken between letters."""
        lines: list[list[Run]] = [[]]
        width = 0
        space: Run | None = None
        for token in _tokens(runs):
            if token == "\n":
                lines.append([])
                width, space = 0, None
                continue
            if token[0][0].isspace():
                space = token[0]
                continue
            word_w = self.runs_width(token)
            space_w = self.width(*space) if space and lines[-1] else 0
            if lines[-1] and width + space_w + word_w > max_w:
                lines.append([])
                width, space_w = 0, 0
            elif space_w:
                lines[-1].append(space)
                width += space_w
            space = None
            if word_w <= max_w:
                lines[-1] += token
                width += word_w
                continue
            for text, style in token:  # break the word between letters
                for ch in text:
                    ch_w = self.width(ch, style)
                    if lines[-1] and width + ch_w > max_w:
                        lines.append([])
                        width = 0
                    lines[-1].append((ch, style))
                    width += ch_w
        return [_merge(line) for line in lines]

    def draw_line(self, img: Image.Image, x: int, y: int, runs: list[Run]) -> None:
        """Draw one line with its top at y."""
        draw = ImageDraw.Draw(img)
        baseline = y + self.ascent
        for text, style in runs:
            w = self.width(text, style)
            f = self.font(style)
            if style.mono and text.strip():
                draw.rectangle([x - 1, y + 1, x + w, y + self.line_height - 2], fill=CODE_BACKGROUND)
            if style.italic:
                self._draw_italic(img, x, y, text, f, w)
            else:
                draw.text((x, baseline), text, font=f, fill=0, anchor="ls")
            thick = max(1, self.size // 14)
            if style.strike:
                mid = baseline - self.ascent * 3 // 10
                draw.rectangle([x, mid - thick // 2, x + w, mid - thick // 2 + thick - 1], fill=0)
            if style.underline:
                draw.rectangle([x, baseline + 2, x + w, baseline + 1 + thick], fill=0)
            x += w

    def _draw_italic(self, img: Image.Image, x: int, y: int, text: str, f, w: int) -> None:
        h = self.line_height
        pad = round(h * ITALIC_SLANT)
        mask = Image.new("L", (w + 2 * pad, h), 0)
        ImageDraw.Draw(mask).text((pad, self.ascent), text, font=f, fill=255, anchor="ls")
        # Output pixel (u, v) samples (u + s*(v - baseline), v): the top leans right.
        s = ITALIC_SLANT
        mask = mask.transform(mask.size, Image.AFFINE, (1, s, -s * self.ascent, 0, 1, 0),
                              resample=Image.BICUBIC)
        img.paste(0, (x - pad, y), mask)


def _tokens(runs: list[Run]):
    """Yield "\\n", whitespace tokens and words (lists of runs, a word may mix styles)."""
    word: list[Run] = []
    for text, style in runs:
        for piece in re.findall(r"\n|[^\S\n]+|[^\s]+", text):
            if piece == "\n" or piece.isspace():
                if word:
                    yield word
                    word = []
                yield "\n" if piece == "\n" else [(" ", style)]
            else:
                word.append((piece, style))
    if word:
        yield word


def _merge(line: list[Run]) -> list[Run]:
    merged: list[Run] = []
    for text, style in line:
        if merged and merged[-1][1] == style:
            merged[-1] = (merged[-1][0] + text, style)
        else:
            merged.append((text, style))
    return merged


# --- blocks ------------------------------------------------------------------

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_LIST = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(?:\[([ xX])\]\s+)?(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_IMAGE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)\s]+)\)\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


@dataclass
class ListItem:
    level: int
    marker: str  # "-" or e.g. "3."
    checked: bool | None  # None = not a checklist item
    text: str


def _starts_block(lines: list[str], i: int) -> bool:
    line = lines[i]
    return bool(_FENCE.match(line) or _HEADING.match(line) or _HR.match(line) or _LIST.match(line)
                or _QUOTE.match(line) or _IMAGE.match(line) or _is_table(lines, i))


def _is_table(lines: list[str], i: int) -> bool:
    return "|" in lines[i] and i + 1 < len(lines) and bool(_TABLE_SEP.match(lines[i + 1])) \
        and "-" in lines[i + 1]


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", line)]


def parse_blocks(text: str) -> list[tuple]:
    """Markdown source -> list of (kind, ...) block tuples."""
    lines = text.expandtabs(4).splitlines()
    blocks: list[tuple] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
        elif m := _FENCE.match(line):
            fence, body = m[1], []
            i += 1
            while i < n and not lines[i].strip().startswith(fence):
                body.append(lines[i])
                i += 1
            blocks.append(("code", "\n".join(body)))
            i += 1
        elif m := _HEADING.match(line):
            blocks.append(("heading", len(m[1]), m[2]))
            i += 1
        elif _HR.match(line):
            blocks.append(("hr",))
            i += 1
        elif _QUOTE.match(line):
            body = []
            while i < n and lines[i].strip():
                if m := _QUOTE.match(lines[i]):
                    body.append(m[1])
                elif not _starts_block(lines, i):
                    body.append(lines[i])  # lazy continuation of the quote
                else:
                    break
                i += 1
            blocks.append(("quote", parse_blocks("\n".join(body))))
        elif _LIST.match(line):
            items, indents = [], []
            while i < n:
                if m := _LIST.match(lines[i]):
                    indent = len(m[1])
                    while indents and indent < indents[-1]:
                        indents.pop()
                    if not indents or indent > indents[-1]:
                        indents.append(indent)
                    checked = None if m[3] is None else m[3] != " "
                    items.append(ListItem(len(indents) - 1, m[2], checked, m[4]))
                elif lines[i].strip() and lines[i].startswith(" ") and items:
                    items[-1].text += "\n" + lines[i].strip()  # continuation line
                else:
                    break
                i += 1
            blocks.append(("list", items))
        elif _IMAGE.match(line):
            m = _IMAGE.match(line)
            blocks.append(("image", m[1], m[2]))
            i += 1
        elif _is_table(lines, i):
            header = _cells(line)
            aligns = []
            for spec in _cells(lines[i + 1]):
                aligns.append("center" if spec.startswith(":") and spec.endswith(":")
                              else "right" if spec.endswith(":") else "left")
            rows = []
            i += 2
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            blocks.append(("table", header, rows, aligns))
        else:
            body = [line]
            i += 1
            while i < n and lines[i].strip() and not _starts_block(lines, i):
                body.append(lines[i])
                i += 1
            blocks.append(("paragraph", "\n".join(s.strip() for s in body)))
    return blocks


class Renderer:
    """Turns parsed blocks into images, one per block."""

    def __init__(self, size: int, align: str = "left", load_image: ImageLoader | None = None) -> None:
        self.size = size
        self.align = align
        self.load_image = load_image
        self.body = Typesetter(size)

    def render(self, blocks: list[tuple], width: int) -> Image.Image:
        images = [self.block(b, width) for b in blocks]
        gap = self.size // 2
        height = sum(im.height for im in images) + gap * max(0, len(images) - 1)
        out = Image.new("L", (width, max(1, height)), 255)
        y = 0
        for im in images:
            out.paste(im, (0, y))
            y += im.height + gap
        return out

    def block(self, block: tuple, width: int) -> Image.Image:
        return getattr(self, f"_{block[0]}")(width, *block[1:])

    def text_block(self, ts: Typesetter, runs: list[Run], width: int, align: str = "left") -> Image.Image:
        lines = ts.wrap(runs, width)
        img = Image.new("L", (width, ts.line_height * len(lines)), 255)
        for i, line in enumerate(lines):
            w = ts.runs_width(line)
            x = {"center": (width - w) // 2, "right": width - w}.get(align, 0)
            ts.draw_line(img, max(0, x), i * ts.line_height, line)
        return img

    def _paragraph(self, width: int, text: str) -> Image.Image:
        return self.text_block(self.body, parse_inline(text), width, self.align)

    def _heading(self, width: int, level: int, text: str) -> Image.Image:
        scale = {1: 1.5, 2: 1.25}.get(level, 1.05)
        ts = Typesetter(round(self.size * scale))
        runs = parse_inline(text, Style(bold=True))
        img = self.text_block(ts, runs, width, "center" if level == 1 else "left")
        if level > 2:
            return img
        rule = max(2, self.size // 10)
        out = Image.new("L", (width, img.height + rule + 4), 255)
        out.paste(img, (0, 0))
        ImageDraw.Draw(out).rectangle([0, img.height + 2, width - 1, img.height + 1 + rule], fill=0)
        return out

    def _hr(self, width: int) -> Image.Image:
        thick = max(2, self.size // 10)
        img = Image.new("L", (width, self.size // 2 + thick), 255)
        draw = ImageDraw.Draw(img)
        y = self.size // 4
        for x in range(0, width, 14):
            draw.rectangle([x, y, min(x + 7, width - 1), y + thick - 1], fill=0)
        return img

    def _quote(self, width: int, blocks: list[tuple]) -> Image.Image:
        bar, pad = max(3, self.size // 7), max(10, self.size // 2)
        inner = self.render(blocks, width - bar - pad)
        img = Image.new("L", (width, inner.height), 255)
        ImageDraw.Draw(img).rectangle([0, 0, bar - 1, inner.height - 1], fill=0)
        img.paste(inner, (bar + pad, 0))
        return img

    def _code(self, width: int, text: str) -> Image.Image:
        ts = Typesetter(max(12, round(self.size * 0.8)))
        f = ts.font(Style(mono=True))
        border, pad = 2, 6
        per_line = max(1, (width - 2 * (border + pad)) // round(f.getlength("M")))
        # Monospace: keep spaces as typed and hard-wrap at the box edge.
        lines = [src[i:i + per_line] for src in text.split("\n")
                 for i in range(0, max(1, len(src)), per_line)]
        img = Image.new("L", (width, ts.line_height * len(lines) + 2 * (border + pad)), 255)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, width - 1, img.height - 1], outline=0, width=border)
        for i, line in enumerate(lines):
            draw.text((border + pad, border + pad + i * ts.line_height + ts.ascent),
                      line, font=f, fill=0, anchor="ls")
        return img

    def _list(self, width: int, items: list[ListItem]) -> Image.Image:
        ts = self.body
        indent = round(self.size * 1.1)
        numbered = [it for it in items if it.marker[0].isdigit()]
        marker_w = max([ts.width(it.marker, Style()) for it in numbered] or [0])
        marker_w = max(marker_w + self.size // 3, round(self.size * 0.9))
        if any(it.checked is not None for it in items):
            marker_w = max(marker_w, self._box_size() + self.size // 2)
        parts = []
        for item in items:
            x0 = item.level * indent
            text_x = x0 + marker_w
            body = self.text_block(ts, parse_inline(item.text), max(ts.size, width - text_x))
            img = Image.new("L", (width, max(body.height, ts.line_height)), 255)
            img.paste(body, (text_x, 0))
            self._marker(img, item, x0, marker_w)
            parts.append(img)
        gap = max(1, self.size // 8)
        out = Image.new("L", (width, sum(p.height for p in parts) + gap * (len(parts) - 1)), 255)
        y = 0
        for part in parts:
            out.paste(part, (0, y))
            y += part.height + gap
        return out

    def _box_size(self) -> int:
        return round(self.body.ascent * 0.95)

    def _marker(self, img: Image.Image, item: ListItem, x0: int, marker_w: int) -> None:
        ts = self.body
        draw = ImageDraw.Draw(img)
        cap = ts.ascent * 7 // 10  # roughly the height of a capital letter
        mid = ts.ascent - cap // 2
        if item.checked is not None:
            box = self._box_size()
            top = ts.ascent - box + max(1, self.size // 10)
            line = max(2, self.size // 12)
            draw.rectangle([x0, top, x0 + box, top + box], outline=0, width=line)
            if item.checked:
                pts = [(x0 + box * 0.2, top + box * 0.5), (x0 + box * 0.42, top + box * 0.75),
                       (x0 + box * 0.82, top + box * 0.22)]
                draw.line(pts, fill=0, width=line + 1, joint="curve")
        elif item.marker[0].isdigit():
            draw.text((x0, ts.ascent), item.marker, font=ts.font(Style()), fill=0, anchor="ls")
        else:
            r = max(2, self.size // 7)
            cx = x0 + r + 1
            shape = [cx - r, mid - r, cx + r, mid + r]
            if item.level % 3 == 0:
                draw.ellipse(shape, fill=0)
            elif item.level % 3 == 1:
                draw.ellipse(shape, outline=0, width=max(1, r // 2))
            else:
                draw.rectangle(shape, fill=0)

    def _table(self, width: int, header: list[str], rows: list[list[str]], aligns: list[str]) -> Image.Image:
        ts = Typesetter(max(12, round(self.size * 0.85)))
        cols = max(len(header), *(len(r) for r in rows)) if rows else len(header)
        grid = [header + [""] * (cols - len(header))] + [r[:cols] + [""] * (cols - len(r)) for r in rows]
        aligns = (aligns + ["left"] * cols)[:cols]
        pad, line = 4, 1
        runs = [[parse_inline(c, Style(bold=(r == 0))) for c in row] for r, row in enumerate(grid)]
        natural = [max(ts.runs_width(row[c]) for row in runs) + 2 * pad for c in range(cols)]
        avail = width - line * (cols + 1)
        col_w = _fit_columns(natural, avail, minimum=ts.size + 2 * pad)
        cells = [[self.text_block(ts, row[c], max(1, col_w[c] - 2 * pad), aligns[c]) for c in range(cols)]
                 for row in runs]
        heights = [max(cell.height for cell in row) + 2 * pad for row in cells]
        img = Image.new("L", (sum(col_w) + line * (cols + 1), sum(heights) + line * (len(heights) + 1)), 255)
        draw = ImageDraw.Draw(img)
        y = 0
        for r, row in enumerate(cells):
            draw.line([(0, y), (img.width, y)], fill=0, width=line * (2 if r == 1 else 1))
            x = 0
            for c, cell in enumerate(row):
                img.paste(cell, (x + line + pad, y + line + pad))
                x += col_w[c] + line
            y += heights[r] + line
        draw.line([(0, y), (img.width, y)], fill=0, width=line)
        x = 0
        for c in range(cols + 1):
            draw.line([(x, 0), (x, img.height)], fill=0, width=line)
            if c < cols:
                x += col_w[c] + line
        return img

    def _image(self, width: int, alt: str, src: str) -> Image.Image:
        if self.load_image is None:
            return self._paragraph(width, f"[{alt or 'picture'}]")
        picture = self.load_image(src).convert("RGBA")
        flat = Image.new("RGBA", picture.size, "white")
        flat.alpha_composite(picture)
        w = min(width, flat.width * 2) if flat.width < width // 2 else width
        h = max(1, round(flat.height * w / flat.width))
        img = Image.new("L", (width, h), 255)
        img.paste(flat.convert("L").resize((w, h), Image.LANCZOS), ((width - w) // 2, 0))
        return img


def _fit_columns(natural: list[int], avail: int, minimum: int) -> list[int]:
    """Shrink the widest columns first until the table fits."""
    widths = list(natural)
    while (excess := sum(widths) - avail) > 0:
        widest = max(range(len(widths)), key=widths.__getitem__)
        smaller = [w for w in widths if w < widths[widest]]
        new = max(minimum, max(smaller, default=minimum), widths[widest] - excess)
        if new >= widths[widest]:
            break  # every column is at the minimum already
        widths[widest] = new
    if sum(widths) > avail:  # still too wide: scale everything evenly
        widths = [max(1, w * avail // sum(widths)) for w in widths]
    return widths


def render_markdown(text: str, width: int, size: int = 24, align: str = "left",
                    load_image: ImageLoader | None = None) -> Image.Image:
    """Render Markdown `text` into an image `width` px wide."""
    return Renderer(size, align, load_image).render(parse_blocks(text), width)
