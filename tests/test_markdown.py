"""Tests for the LX-D02 Markdown renderer."""
from PIL import Image
import pytest

from lxd02.markdown import (
    ListItem,
    Style,
    Typesetter,
    parse_blocks,
    parse_inline,
    render_markdown,
)
from lxd02.receipt import WIDTH, render_receipt

B, I, C, S, U = (Style(bold=True), Style(italic=True), Style(mono=True), Style(strike=True),
                 Style(underline=True))


def test_inline_styles() -> None:
    assert parse_inline("a **b** *c* _d_ `e` ~~f~~ [g](http://x)") == [
        ("a ", Style()), ("b", B), (" ", Style()), ("c", I), (" ", Style()), ("d", I),
        (" ", Style()), ("e", C), (" ", Style()), ("f", S), (" ", Style()), ("g", U)]


def test_inline_nesting_and_literals() -> None:
    assert parse_inline("**bold *both***") == [("bold ", B), ("both", Style(bold=True, italic=True))]
    # Underscores inside words, lone asterisks and escapes stay literal.
    assert "".join(t for t, _ in parse_inline(r"snake_case_name 2 * 3 \*x\*")) == "snake_case_name 2 * 3 *x*"
    assert parse_inline("`**not bold**`") == [("**not bold**", C)]


def test_blocks() -> None:
    blocks = parse_blocks(
        "# Title\n\nSome *text*\nnext line\n\n- [ ] todo\n- [x] done\n  - nested\n1. one\n\n"
        "> quoted\n\n---\n```\ncode  here\n```\n| a | b |\n|---|--:|\n| 1 | 2 |\n![logo](logo.png)")
    kinds = [b[0] for b in blocks]
    assert kinds == ["heading", "paragraph", "list", "quote", "hr", "code", "table", "image"]
    assert blocks[0] == ("heading", 1, "Title")
    assert blocks[1] == ("paragraph", "Some *text*\nnext line")
    assert blocks[2][1] == [ListItem(0, "-", False, "todo"), ListItem(0, "-", True, "done"),
                            ListItem(1, "-", None, "nested"), ListItem(0, "1.", None, "one")]
    assert blocks[3] == ("quote", [("paragraph", "quoted")])
    assert blocks[5] == ("code", "code  here")
    assert blocks[6] == ("table", ["a", "b"], [["1", "2"]], ["left", "right"])
    assert blocks[7] == ("image", "logo", "logo.png")


def test_list_continuation_and_four_space_nesting() -> None:
    (_, items), = parse_blocks("* one\n    * two\n      more of two\n* three")
    assert [(it.level, it.text) for it in items] == [(0, "one"), (1, "two\nmore of two"), (0, "three")]


def test_wrap_keeps_line_breaks_and_breaks_long_words() -> None:
    ts = Typesetter(24)
    lines = ts.wrap(parse_inline("short\n" + "x" * 80), 200)
    assert lines[0] == [("short", Style())]
    assert len(lines) >= 4 and all(ts.runs_width(line) <= 200 for line in lines)


def test_render_every_block() -> None:
    md = ("# Heading\n## Sub\n### Small\nPlain **bold** *it* ~~no~~ `code`\n\n"
          "- [ ] a\n- [x] b\n- c\n  - d\n    - e\n3. f\n\n> quote\n\n---\n\n"
          "```\nfor i in range(3):\n    print(i)\n```\n\n| Item | Qty |\n|:--|:-:|\n| Apples | 6 |\n\n"
          "![pic](p.png)")
    pic = Image.new("RGB", (100, 50), "black")
    img = render_markdown(md, 368, load_image=lambda path: pic)
    assert img.width == 368 and img.height > 600
    assert img.getextrema() == (0, 255)


def test_ticked_box_has_more_ink_than_empty_box() -> None:
    empty = render_markdown("- [ ] Milk", 368)
    ticked = render_markdown("- [x] Milk", 368)
    ink = lambda im: sum(im.histogram()[:128])  # noqa: E731
    assert ink(ticked) > ink(empty)


def test_wide_table_fits_the_paper() -> None:
    row = "| " + " | ".join(["a long cell value"] * 5) + " |"
    md = row + "\n|" + "---|" * 5 + "\n" + row
    assert render_markdown(md, 368).width == 368


def test_picture_without_loader_prints_alt_text() -> None:
    assert render_markdown("![logo](x.png)", 368).height < 60


def test_receipt_markdown_and_plain() -> None:
    md = render_receipt(text="- [ ] **Milk**")
    plain = render_receipt(text="- [ ] **Milk**", markdown=False)
    assert md.width == plain.width == WIDTH
    assert md.tobytes() != plain.tobytes()


def test_receipt_needs_content() -> None:
    with pytest.raises(ValueError, match="nothing to print"):
        render_receipt()
