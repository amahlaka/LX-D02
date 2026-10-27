"""CLI tests that don't need a printer."""
import pytest
from PIL import Image

from lxd02 import cli


def test_preview_markdown_file(tmp_path) -> None:
    (tmp_path / "list.md").write_text("# Shopping\n- [ ] Milk\n- [x] Bread\n", encoding="utf-8")
    cli.main(["--title", "Weekend", "--file", str(tmp_path / "list.md"), "--preview", str(tmp_path / "out")])
    img = Image.open(tmp_path / "out" / "receipt.png")
    assert img.width == 384 and img.height > 100


def test_text_newlines_from_the_shell(tmp_path) -> None:
    cli.main(["--text", r"one\ntwo", "--plain", "--preview", str(tmp_path / "a")])
    cli.main(["--text", "one", "--plain", "--preview", str(tmp_path / "b")])
    assert Image.open(tmp_path / "a" / "receipt.png").height > Image.open(tmp_path / "b" / "receipt.png").height


def test_nothing_to_print() -> None:
    with pytest.raises(SystemExit, match="nothing to print"):
        cli.main([])
