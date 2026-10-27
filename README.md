# LX-D02 receipt printer

Print on an **LX-D02** Bluetooth thermal receipt printer (58 mm paper, sold
e.g. as Dolebo, used with the Funny Print app) from a PC, without the phone
app. Receipts can be any length and stack an icon, a title, Markdown text
(lists, checklists, tables…), a QR code and a picture.

The printer's Bluetooth protocol, reverse engineered from the Funny Print app,
is documented in [PROTOCOL.md](PROTOCOL.md).

For Home Assistant, see the **HA-LXD02** integration, which carries a copy of
this package's protocol and renderer.

## Requirements

- Windows, macOS or Linux with Bluetooth LE
- Python 3.12+

## Install

```bash
python -m venv .venv
```
```bash
.venv\Scripts\python -m pip install -e .
```

On macOS/Linux use `.venv/bin/python` instead of `.venv\Scripts\python`. This
installs the `lxd02` package and the `lxd02-print` command.

The printer switches itself off after a few minutes idle, and accepts only one
connection: turn it on and close the phone app (and turn off Home Assistant's
**Stay connected**) before printing.

```bash
lxd02-print --info
```

## Printing

```bash
lxd02-print --icon mdi:coffee --title "Coffee break" --text "Back in 15 minutes"
```
```bash
lxd02-print --title Shopping --file list.md
```
```bash
lxd02-print --qr "WIFI:T:WPA;S:Home;P:secret;;" --title "Guest Wi-Fi"
```
```bash
lxd02-print --image photo.jpg --energy 5
```
```bash
lxd02-print --feed 20
```

| Option | Default | |
|---|---|---|
| `--title TEXT` | | large centred heading |
| `--text TEXT` | | body text in Markdown; `\n` starts a new line, `-` reads stdin |
| `--file PATH` | | body text from a UTF-8 (Markdown) file |
| `--plain` | | print the text exactly as written, without Markdown |
| `--qr TEXT` | | QR code content |
| `--icon mdi:NAME` | | MDI icon at the top |
| `--image PATH` | | picture, scaled to the paper width and dithered |
| `--size` | `normal` | text size: `small`, `normal`, `large` |
| `--align` | `left` | body text: `left`, `center`, `right` |
| `--energy 1-6` | printer's | darkness; the printer remembers it |
| `--feed MM` | 0 | extra blank paper after the print. On its own (nothing else to print), just advance that much paper |
| `--no-dither` | | hard black/white pictures |
| `--copies N` | 1 | |
| `--preview DIR` | | save `receipt.png` instead of printing |
| `--address MAC` | scan | printer address, e.g. `C0:00:00:00:00:48` |
| `--info` | | battery, paper and darkness |

`python -m lxd02` works the same as `lxd02-print`.

After every job the printer advances about **10 mm** of paper by itself, so
the last printed line clears the tear bar. Feeds count it: `--feed 12` gives
12 mm, and 10 mm is the shortest feed. The first 10–20 mm of a print can come
out faint while the print head warms up.

## Markdown: lists, checklists, tables

The text is Markdown, so shopping lists, to-dos and notes print formatted:

````markdown
# Weekend
## Groceries
- [ ] Milk
- [x] Bread
- [ ] Coffee, **dark roast**

## Hardware store
1. Screws *(4 mm)*
2. Wood glue
   - the ~~cheap~~ good one

> Remember the bags!

| Item   | Qty | Price |
|:-------|:---:|------:|
| Apples |  6  |  2.40 |

---
```
Total   6.38
```
````

| Markdown | Prints as |
|---|---|
| `# H1`, `## H2`, `### H3` | headings (H1 centred, H1/H2 underlined) |
| `**bold**`, `*italic*` / `_italic_`, `~~strike~~`, `` `code` `` | inline styles (`code` on a light gray background) |
| `[text](url)` | underlined text |
| `- item`, `* item`, `1. item` | bullet and numbered lists; indent to nest |
| `- [ ] task`, `- [x] done` | checkboxes, empty or ticked |
| `> text` | quote with a bar on the left |
| ```` ``` ```` fenced block | monospace box, spaces kept |
| `---` | dashed tear line |
| `\| a \| b \|` + `\|---\|---\|` | table with a bold header; `:--`, `:-:`, `--:` align columns |
| `![alt](path)` | picture, relative to the `--file` |

Unlike standard Markdown, every line break is kept. Text uses the bundled
DejaVu fonts, so every machine prints identically.

## Using it from Python

```python
import asyncio
from lxd02 import LXD02, render_receipt

async def main():
    img = render_receipt(title="Shopping", text="- [ ] Milk\n- [ ] Bread", icon="mdi:cart")
    async with LXD02() as printer:  # scans for the printer; or LXD02("C0:00:00:00:00:48")
        status = await printer.wait_status()
        print(status.battery, status.no_paper)
        await printer.print_image(img, energy=3)
        await printer.feed(20)

asyncio.run(main())
```

`lxd02.protocol.LXD02` is the protocol on its own: give it any connected
bleak client (Home Assistant's, for example).

## Development

`src/lxd02/protocol.py`, `receipt.py`, `markdown.py` and `assets/` are copied
unchanged into the HA-LXD02 integration. Change them here, then copy them
there; HA-LXD02's tests check the copies match when both projects sit side by
side.

The tests cover the protocol against a simulated printer (handshake,
hold/resend, out of paper, the automatic feed), the Markdown renderer and the
CLI:

```bash
pip install -e . pytest pytest-asyncio
```
```bash
pytest
```

## Files

| Path | What it is |
|---|---|
| `src/lxd02/protocol.py` | the Bluetooth protocol: handshake, status, darkness, printing, feed |
| `src/lxd02/receipt.py`, `markdown.py` | the receipt and Markdown renderer |
| `src/lxd02/client.py` | a plain `bleak` connection (scan, connect, unlock) |
| `src/lxd02/cli.py` | the `lxd02-print` command |
| `src/lxd02/assets/` | DejaVu Sans / Sans Mono fonts and license, MDI icon font 7.4.47 and license |
| `tests/` | protocol, renderer and CLI tests |
| `PROTOCOL.md` | the reverse-engineered printer protocol |
