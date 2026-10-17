# AJAZZ AK820 MAX HE: analog key data over USB HID

Reverse-engineered notes and a working Python bridge that reads the per-key analog
(Hall-effect) values from the **AJAZZ AK820 MAX HE** over USB and exposes them as a
virtual Xbox 360 gamepad on Windows.

> **Status:** works on one unit (firmware V1.13.17, wired USB, Windows). Everything below
> is based on USB captures of the official web driver plus live testing. It is **not**
> an official spec. Items marked *(hypothesis)* are inferred and not confirmed.
> Not affiliated with AJAZZ. Use at your own risk.

## Device

| Field | Value |
|---|---|
| Vendor ID | `0x0C45` (Sonix Technology) |
| Product ID | `0x80B1` |
| Product name | `AK820MAX` |
| Internal model | `SG8994HERGB` / `GK8260HERGB` |
| Firmware tested | `V1.13.17` |

## HID interfaces

Report sizes were read with `HidP_GetCaps` (see `hidcaps.py`), so these are what Windows
actually sees, including the report ID byte.

| Interface | Usage page | IN | OUT | FEATURE | Purpose |
|---|---|---|---|---|---|
| MI_00 | `0x0001` | n/a | n/a | n/a | Boot keyboard |
| MI_01 | `0x0001` / `0x000C` | n/a | n/a | n/a | Standard keyboard, consumer, mouse collections |
| **MI_02** | **`0xFF68`** | **65** | **65** | 0 | **Commands out, acks and analog reports in** |
| MI_03 | `0xFF67` | 0 | 4097 | 65 | Not used here. 4097-byte output report, likely bulk/firmware. **Do not write to it.** |

**Correction to early notes:** an early reading of the endpoint capture suggested analog
data arrives on `0xFF67`. It does not. That interface has no input reports, and reading
it fails with `OSError: read error`. All traffic used here goes through `0xFF68`.

## Protocol

All packets are 64 bytes. With hidapi on Windows, prefix a report ID byte `0x00` when
writing (65 bytes on the wire).

### Commands (host to keyboard)

Captured from the official driver. Only the first four were needed for analog streaming.

| Bytes | Purpose |
|---|---|
| `aa 10 30 00 00 00 01 00 ...` | Handshake |
| `aa 11 38 00 00 00 01 00 ...` | Get version |
| `aa 64 00 ...` | **Enter analog / calibration mode.** Keyboard stops typing. |
| `aa 65 00 ...` | **Exit** that mode. Keyboard types again. |
| `aa 12 38 [addr] ...` through `aa 18 38 [addr] ...` | Config block reads (address steps by `0x38`) |
| `aa 13 10 00 00 00 01 00 ...` | Read another config block |
| `aa 27 10 f0 03 00 01 00 ...` | Unknown (not analog). Not sent by the bridge. |
| `aa 0f 05 00 ...` | Seen during official calibration. Effect unknown. Not sent by the bridge. |

The bridge sends only `aa 10`, `aa 11`, `aa 64`, and `aa 65` on exit.

### Analog report (keyboard to host)

```
55 fb [key_id] 00 [s1_L s1_H] [s2_L s2_H] [s3_L s3_H] 00 00 22 00 ... (64 bytes)
```

| Offset | Size | Meaning |
|---|---|---|
| 0 | 1 | `0x55` header |
| 1 | 1 | `0xFB` analog report |
| 2 | 1 | key ID (table below) |
| 4-5 | 2 | **s1**, LE uint16 |
| 6-7 | 2 | **s2**, LE uint16 |
| 8-9 | 2 | **s3**, LE uint16 |
| 12 | 1 | `0x22` (constant in captures) |

Ack reports look like `55 [cmd] 00 00 ...`.

### What the three values do

Observed over 82 keys during a test session:

| Value | Behaviour | Interpretation |
|---|---|---|
| **s1** | About 2420-2560 per key, moves only a few counts, does not follow presses | Per-key rest baseline |
| **s3** | About `s1 - 1` at rest, falls to about 1300 on a full press | **Live sensor reading** |
| **s2** | Constant on most keys (commonly 1500, otherwise values like 1343-1494). Pressed keys showed values of 34000+ | Status / stored value, **not live depth** |

**Depth in counts = `s1 - s3`.** Measured results:

- Idle jitter on untouched keys: **at most 9 counts**.
- Full press: roughly **1050-1180 counts** (Esc 1162, W 1178, L-Ctrl 1056).
- The board reports **every key continuously**, not only keys that change, so keep a per-key state table.

*(hypothesis)* s2 is a 15-bit value with bit `0x8000` as a flag on keys pressed during
the session: subtracting 32768 from the high values (34036, 34268, 34087, 34041) gives
1268, 1500, 1319, 1273, which fall in the same range as the other s2 values. s2 may be a
stored calibration value. Not confirmed.

### Key IDs

The ID is the byte at offset 2. The map comes from the official web driver's keyboard
config (`ajazz.driveall.cn/src/config/keyboards/ajazz/3141-32945-AK820MAX.ts`).

| Row | `ID = key` |
|---|---|
| F-row | 0 Esc, 1-12 F1-F12, 106 Del |
| Numbers | 16 \`, 17-26 1-0, 27 -, 28 =, 92 Backspace, 104 Home |
| Q-row | 32 Tab, 33 Q, 34 W, 35 E, 36 R, 37 T, 38 Y, 39 U, 40 I, 41 O, 42 P, 43 [, 44 ], 60 \\, 105 PgUp |
| A-row | 48 Caps, 49 A, 50 S, 51 D, 52 F, 53 G, 54 H, 55 J, 56 K, 57 L, 58 ;, 59 ', 76 Enter, 108 PgDn |
| Z-row | 64 L-Shift, 65 Z, 66 X, 67 C, 68 V, 69 B, 70 N, 71 M, 72 ",", 73 ., 74 /, 75 R-Shift, 90 Up, 107 End |
| Bottom | 80 L-Ctrl, 81 L-Win, 82 L-Alt, 83 Space, 84 R-Alt, 85 Fn, 87 R-Ctrl, 88 Left, 89 Down, 91 Right |

## Caveats and risks

- **`aa 64` is a calibration mode, not just a stream.** The keyboard stops typing, and the
  firmware may be recording per-key values while it runs. The author did not see
  misbehaviour in normal typing afterwards, but it is not proven that nothing is written.
  If a key's feel changes afterwards, rerun the official driver's calibration.
- **Always send `aa 65` on exit.** The bridge does this, but if it is killed the keyboard
  stays in that mode until replugged.
- Tested on one unit, one firmware version, wired only, Windows only.
- The response from Hall sensors is generally non-linear with respect to travel. No curve
  is applied by default.

## Using the bridge

```
pip install hidapi vgamepad          # vgamepad needs the ViGEmBus driver
python ak820_analog.py list          # show HID interfaces
python ak820_analog.py dump 20       # log presses for 20 s, writes calib.json
python ak820_analog.py dump 20 34    # watch one key (34 = W)
python ak820_analog.py pad           # virtual Xbox 360 pad; hold Esc 0.5 s to quit
```

The keyboard does not type while `pad` or `dump` runs. `dump` exits on a timer and `pad`
exits when Esc is held fully down. Default mapping: WASD to left stick, arrows to right
stick, Q/E to triggers, Space/C/X/V to A/B/X/Y, L-Shift/R to bumpers. Edit `AXES`,
`TRIGGERS`, and `BUTTONS` at the top of the script.

`hidcaps.py` prints each interface's report sizes on Windows.

## Open questions

- What `s2` really is, and what the `0x8000` flag means.
- What `aa 27`, `aa 0f`, and the `aa 12`-`aa 18` config reads do, and whether any write
  commands exist for actuation points, rapid trigger, etc.
- Whether Bluetooth or 2.4 GHz modes expose the same stream.
- Whether other firmware versions or the non-HE AK820 MAX share this protocol.

Corrections and captures from other units are welcome.
