"""Print on an LX-D02 thermal receipt printer.

Examples:
    lxd02-print --info
    lxd02-print --text "Buy milk"
    lxd02-print --title Shopping --file list.txt
    lxd02-print --file todo.md                       # Markdown: lists, [ ] checklists, tables...
    lxd02-print --text "- [ ] Milk\\n- [ ] Bread"
    lxd02-print --icon mdi:coffee --title "Coffee break" --text "15 min"
    lxd02-print --qr "WIFI:T:WPA;S:Home;P:secret;;" --text "Guest Wi-Fi"
    lxd02-print --image photo.jpg --energy 4
    lxd02-print --text "Test" --preview previews
    lxd02-print --feed 20                            # just advance 20 mm of paper
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

from PIL import Image

from .client import LXD02
from .protocol import AUTO_FEED_MM, ENERGY_MAX, ENERGY_MIN, PrinterError
from .receipt import render_receipt


async def run(args) -> None:
    text = args.text
    if args.file:
        text = "\n".join(filter(None, [text, Path(args.file).read_text(encoding="utf-8").rstrip()]))
    elif args.text == "-":
        text = sys.stdin.read().rstrip()
    picture = Image.open(args.image) if args.image else None
    # Markdown pictures, ![alt](path), are relative to the Markdown file.
    base = Path(args.file).parent if args.file else Path.cwd()
    img = None
    if args.title or text or args.qr or args.icon or picture:
        img = render_receipt(title=args.title, text=text, qr=args.qr, icon=args.icon,
                             picture=picture, size=args.size, align=args.align,
                             markdown=not args.plain, load_image=lambda path: Image.open(base / path))

    if args.preview:
        out = Path(args.preview)
        out.mkdir(parents=True, exist_ok=True)
        if img is None:
            raise ValueError("nothing to preview")
        img.save(out / "receipt.png")
        print(f"saved {out / 'receipt.png'} ({img.height / 8:.0f} mm long)")
        return

    feed_only = img is None and args.feed > 0
    if img is None and not args.info and not feed_only:
        raise ValueError("nothing to print: use --text, --title, --file, --qr, --icon, --image or --feed")

    async with LXD02(args.address) as printer:
        if args.info:
            st = await printer.wait_status()
            print(f"printer {printer.address} (MAC {printer.mac_address}): battery {st.battery}%, "
                  f"{'no paper' if st.no_paper else 'paper OK'}, energy {st.energy}"
                  + (", charging" if st.charging else ", charged" if st.charged else "")
                  + (", OVERHEATED" if st.overheated else "")
                  + (", LOW VOLTAGE" if st.low_voltage else ""))
        if img is not None:
            for _ in range(args.copies):
                await printer.print_image(img, energy=args.energy, feed_mm=args.feed,
                                          dither=not args.no_dither)
            print(f"printed {img.height / 8:.0f} mm" + (f" x{args.copies}" if args.copies > 1 else ""))
        elif feed_only:
            await printer.feed(args.feed)
            print(f"fed {max(args.feed, AUTO_FEED_MM):g} mm")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="lxd02-print", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--title", default="", help="large centred heading")
    p.add_argument("--text", default="", help="body text in Markdown (use \\n in the shell for new lines, - for stdin)")
    p.add_argument("--file", help="read body text from a UTF-8 file (Markdown)")
    p.add_argument("--plain", action="store_true", help="print the text as is, without Markdown formatting")
    p.add_argument("--qr", help="text to encode as a QR code")
    p.add_argument("--icon", help="MDI icon, e.g. mdi:coffee (https://pictogrammers.com/library/mdi/)")
    p.add_argument("--image", help="picture to print, scaled to the paper width")
    p.add_argument("--size", default="normal", choices=["small", "normal", "large"], help="text size")
    p.add_argument("--align", default="left", choices=["left", "center", "right"], help="body text alignment")
    p.add_argument("--energy", type=int, choices=range(ENERGY_MIN, ENERGY_MAX + 1),
                   help="darkness 1-6 (the printer remembers it)")
    p.add_argument("--feed", type=float, default=0,
                   help=f"extra blank paper after the print in mm; the printer always adds {AUTO_FEED_MM} mm "
                        "by itself (default 0). On its own: advance this much paper")
    p.add_argument("--no-dither", action="store_true", help="hard black/white instead of dithering pictures")
    p.add_argument("--copies", type=int, default=1)
    p.add_argument("--preview", metavar="DIR", help="save the rendered receipt as a PNG instead of printing")
    p.add_argument("--address", help="printer BLE address (default: scan for it)")
    p.add_argument("--info", action="store_true", help="show battery, paper and energy")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    args.text = args.text.replace("\\n", "\n")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(message)s")
    try:
        asyncio.run(run(args))
    except (PrinterError, ValueError, OSError) as e:
        raise SystemExit(f"error: {e}") from None


if __name__ == "__main__":
    main()
