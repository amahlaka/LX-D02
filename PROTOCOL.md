# LX-D02 Bluetooth LE protocol

This describes how the LX-D02 thermal receipt printer (sold under brand names such as
Dolebo) talks over Bluetooth LE. The printer is used with the **Funny Print** app.

The protocol was reverse engineered from that app's Android build (v2.8.1, package
`com.lailaixiong.funnyprint`), which is React Native: the logic is Hermes bytecode in
`assets/index.android.bundle`. It was checked against a real printer.
[rusq/thermoprint](https://github.com/rusq/thermoprint), an open-source Go driver,
filled in the one thing the app code did not make obvious: the data timing (see
[Printing](#printing)).

The code is in [custom_components/lxd02/protocol.py](custom_components/lxd02/protocol.py).

Every detail below has been seen working on the printer unless it is marked
**(from app code, untested)**.

## Device

| Property | Value |
|---|---|
| Advertised name | `LX-D02` (the GATT device name characteristic reads `LX-D01…` on the test unit) |
| Manufacturer data | company ID `0x00C0` (192), value = last 4 bytes of the MAC, e.g. `00000048` |
| Address | `C0:00:00:00:00:48` on the test unit; the printer also reports it in its hardware info |
| Print head | 384 dots = 48 mm at 203 dpi (8 dots/mm), on 58 mm paper |
| ATT MTU | 128 (writes of up to 125 bytes) |
| Pairing | none; connect directly over BLE |
| Power | switches itself off after a few minutes idle; then it stops advertising |

The printer only advertises while nothing else (such as the phone app) is connected.

## GATT

| Characteristic | UUID | Properties | Use |
|---|---|---|---|
| Service | `0000ffe6-0000-1000-8000-00805f9b34fb` | | printer service |
| Write | `0000ffe1-0000-1000-8000-00805f9b34fb` | write without response | commands and image data |
| Notify | `0000ffe2-0000-1000-8000-00805f9b34fb` | notify | replies and status |

A second service, `5833ff01-9b8b-5191-6142-22a4536ef123` (`…ff02` write, `…ff03` notify),
is present but the app never uses it.

## Frames

Commands and replies start with `5A` and a command byte, followed by the payload. The
app pads most commands with zeros to 12 bytes; the printer also accepts them
unpadded. Replies are always 12 bytes.

| Command | Sent | Reply | Meaning |
|---|---|---|---|
| `01` | `5A 01` + 10 × `00` | `5A 01 00 03 [MAC 6 bytes] [type] 00` | hardware info |
| `0A` | `5A 0A` + 10 random bytes | `5A 0A` + 10 bytes | handshake, step 1 |
| `0B` | `5A 0B` + 10 bytes | `5A 0B [result]` | handshake, step 2 (`01` = accepted) |
| `0C` | `5A 0C [energy]` | `5A 0C [energy]` | print darkness, remembered by the printer |
| `04` | `5A 04 [packets u16 BE] 00 00` | echo | start a print job |
| `04` | `5A 04 [packets u16 BE] 01 00` | `5A 04 … 01 00 FF FF FF FF FF FF` | end the job (also cancels) |
| `03` | `5A 03 82 00 03 00 …` | | move paper; only for other models **(from app code, untested)** |

Unsolicited messages from the printer:

| Message | Meaning |
|---|---|
| `5A 02 …` | status, every 2–3 s while connected (below) |
| `5A 05 [index u16 BE]` | resend data from packet `index` (also ends a hold) |
| `5A 06 [packets u16 BE] [n]` | the job has finished printing |
| `5A 07 14` | "cooldown", sent at job start and during long jobs; the job carries on, nothing to do |
| `5A 08` | hold: stop sending data until a `5A 05` arrives |

### Status (`5A 02`)

| Byte | Meaning |
|---|---|
| 2 | battery, percent |
| 3 | `1` = out of paper |
| 4 | `1` = charging, `2` = fully charged |
| 5 | `1` = overheated |
| 6 | `1` = low voltage |
| 7 | energy (print darkness) setting |
| 8–9 | unknown; it falls while printing (`0C DB` → `0A 2F`), so possibly a supply voltage |
| 10–11 | unknown (`00 40`) |

## Handshake

The printer prints nothing until the app proves it knows the algorithm:

1. Send `5A 01` and take the MAC from bytes 4–9 of the reply (`C0 00 00 00 00 48`).
2. Pick 10 random bytes `r[0..9]` (the app uses values 1–254) and send `5A 0A r…`.
3. For each `r[i]`, compute CRC-16/XMODEM (poly `0x1021`, init `0`, no reflection)
   over the 7 bytes `r[i] MAC`. The printer answers with the **low** bytes of the
   10 CRCs; check them.
4. Send `5A 0B` + the 10 **high** bytes. A reply of `5A 0B 01` means unlocked.

The CRC is the standard one: CRC-16/XMODEM of `"123456789"` is `0x31C3`.

## Image data

- 384 dots per row = 48 bytes, MSB first, bit `1` = black. No row padding.
- Packets carry 2 rows (96 bytes):

  ```
  55 [packet index u16 BE] [96 data bytes] 00
  ```

  Indexes start at 0. The last packet is padded with zeros to 96 bytes.
- The app turns pictures into this format with grayscale conversion (`0.299 R +
  0.587 G + 0.114 B`), then Floyd–Steinberg dithering at threshold 128.
- For several copies the app concatenates the image data with 48 blank rows
  between copies, in a single job.

## Printing

```
-> 5A 0C 03                       energy (optional; the printer remembers it)
<- 5A 0C 03 …
-> 5A 04 00 78 00 00              start, 120 packets
<- 5A 07 14 …                     cooldown notice
<- 5A 04 00 78 00 00 …            start accepted
-> 55 00 00 [96 bytes] 00         packets 0 … 119, immediately, ~7 ms apart
-> 55 00 01 [96 bytes] 00
   …
<- 5A 06 00 78 01 …               finished
-> 5A 04 00 78 01 00              end of job
<- 5A 04 00 78 01 00 FF FF FF FF FF FF
```

**Data must follow the start acknowledgement straight away.** The printer starts
moving paper as soon as it accepts the job and prints whatever has arrived by
then; rows that arrive late come out blank. Waiting about 1.5 s before the first
packet produced only a short strip of blank paper, even for a solid black image.
The `5A 05` (resend) and `5A 08` (hold) messages are only for flow control during
the job, not an invitation to start.

Other observations:

- The first 10–20 mm of a print can come out faint while the head warms up.
- Energy 1–6 is accepted. The app sends its darkness slider level minus one;
  [thermoprint](https://github.com/rusq/thermoprint) uses 1–6 with 2 as its default.
  The test unit came set to 3.
- There is no feed command for this model: print blank rows to advance paper.
- After every job the printer advances about **10 mm** of paper by itself, whether or not
  the end-of-job `5A 04 … 01 00` is sent, presumably so the last printed line clears the
  tear bar. Measured: a job of 12 mm of blank rows gave a 22 mm strip, with and without
  the end command. So the shortest possible feed is about 10 mm.

## Printer type in the app

The app chooses a code path from the device name. `LX-D02` is not in its list, so the
type comes from byte 10 of the hardware info reply (`00` here), giving the plain
receipt-printer path described above. Other models (label printers, wider "TB"
printers) add a copy count, width and height to the start command, and use `5A 03` to
move paper **(from app code, untested)**.
