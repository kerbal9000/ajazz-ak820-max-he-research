#!/usr/bin/env python3
"""
AJAZZ AK820 MAX HE analog bridge (experimental, based on captured protocol).

  python ak820_analog.py dump [seconds] [key_id]   # log presses, write calib.json
  python ak820_analog.py list             # show HID interfaces
  python ak820_analog.py pad              # virtual Xbox 360 pad from analog keys

Install:  pip install hidapi vgamepad      (vgamepad needs the ViGEmBus driver)

SAFETY: while analog mode (aa 64) is active the keyboard stops typing.
 - `dump` auto-exits after N seconds (default 20).
 - `pad` exits when you press Esc (key id 0) fully for ~0.5 s.
 - If the script dies without sending aa 65, unplug/replug the keyboard.
"""
import sys, time, json, atexit
import hid

VID, PID = 0x0C45, 0x80B1
PAGE_CMD, PAGE_DATA = 0xFF68, 0xFF67

# From your dump: s1 = per-key rest baseline (~2420-2560), s3 = live raw reading
# (falls to ~1300 on a full press), s2 = status/stored value (NOT live depth).
# depth counts = s1 - s3   (0 at rest, ~1100-1200 at bottom-out)
DEFAULT_FULL = 1000   # counts of (s1 - s3) treated as full deflection
NOISE = 40            # drops below this are treated as 0 (idle jitter was <= ~10)
CALIB_FILE = "calib.json"


def pad64(*b):
    return bytes(b).ljust(64, b"\x00")


# Edit this list to replay your captured sequence exactly if streaming doesn't start.
START = [
    pad64(0xAA, 0x10, 0x30, 0, 0, 0, 0x01, 0),   # handshake
    pad64(0xAA, 0x11, 0x38, 0, 0, 0, 0x01, 0),   # get version
    pad64(0xAA, 0x64, 0x00),                      # enter analog/calibration mode
]
STOP = pad64(0xAA, 0x65, 0x00)                    # exit

# ---- gamepad mapping (key ids from your map) -------------------------------
# axis = (negative key, positive key)
AXES = {
    "lx": (49, 51),   # A / D
    "ly": (50, 34),   # S / W
    "rx": (88, 91),   # Left / Right arrow
    "ry": (89, 90),   # Down / Up arrow
}
TRIGGERS = {"lt": 33, "rt": 35}   # Q, E
BUTTONS = {                        # key id -> vgamepad button name
    83: "XUSB_GAMEPAD_A",          # Space
    67: "XUSB_GAMEPAD_B",          # C
    66: "XUSB_GAMEPAD_X",          # X
    68: "XUSB_GAMEPAD_Y",          # V
    64: "XUSB_GAMEPAD_LEFT_SHOULDER",   # L-Shift
    36: "XUSB_GAMEPAD_RIGHT_SHOULDER",  # R
}
EXIT_KEY = 0   # Esc
BUTTON_THRESHOLD = 0.5


def list_ifaces():
    for d in hid.enumerate(VID, PID):
        print(f"iface={d['interface_number']} usage_page={d['usage_page']:#06x} "
              f"usage={d['usage']:#06x} path={d['path']}")


def open_iface(page):
    for d in hid.enumerate(VID, PID):
        if d["usage_page"] == page:
            h = hid.device()
            h.open_path(d["path"])
            return h
    raise SystemExit(f"HID interface with usage page {page:#06x} not found")


class Board:
    def __init__(self):
        # 0xff68 carries commands (OUT) and analog/ack reports (IN).
        # 0xff67 has no input reports (and a 4097-byte output report, likely
        # firmware/bulk): deliberately NOT opened.
        self.cmd = open_iface(PAGE_CMD)
        self.data = self.cmd
        self.active = False
        atexit.register(self.stop)

    def send(self, pkt):
        self.cmd.write(b"\x00" + pkt)   # report id 0 + 64 bytes
        time.sleep(0.05)
        try:
            while self.data.read(65, 10):   # drain acks
                pass
        except OSError as e:
            print(f"[warn] read error while draining acks after {pkt[:3].hex(' ')}: {e}")

    def start(self):
        for p in START:
            self.send(p)
        self.active = True

    def stop(self):
        if self.active:
            self.active = False
            try:
                self.send(STOP)
            except Exception:
                pass

    def packets(self):
        """Yield (key_id, (s1, s2, s3)) for analog reports, None otherwise."""
        while True:
            try:
                r = self.data.read(65, 5)
            except OSError as e:
                raise SystemExit(
                    f"read error on data interface: {e}\n"
                    "Device may have re-enumerated, or this interface has no IN reports.\n"
                    "Run `python ak820_analog.py list` and check the interfaces."
                )
            if r and len(r) >= 10 and r[0] == 0x55 and r[1] == 0xFB:
                s = tuple(r[4 + 2 * i] | (r[5 + 2 * i] << 8) for i in range(3))
                yield r[2], s
            else:
                yield None


def fmt(key, s):
    flag = "F" if s[1] & 0x8000 else "-"
    return (f"key {key:3d}  drop={s[0]-s[2]:5d}  s1={s[0]:5d}  s3={s[2]:5d}  "
            f"s2={flag}{s[1] & 0x7FFF}")


def dump(board, seconds, only_key=None):
    lo, hi, last = {}, {}, {}
    end = time.time() + seconds
    print(f"Streaming for {seconds}s"
          + (f", showing key {only_key} only" if only_key is not None else
             ", showing only keys with a real press")
          + ". Press keys slowly, fully, and release.")
    for item in board.packets():
        if time.time() > end:
            break
        if item is None:
            continue
        key, s = item
        drop = s[0] - s[2]
        lo[key] = min(lo.get(key, drop), drop)
        hi[key] = max(hi.get(key, drop), drop)
        if only_key is not None:
            show = key == only_key and abs(drop - last.get(key, -999)) >= 3
        else:
            show = drop >= NOISE and abs(drop - last.get(key, -999)) >= 20
        if show:
            last[key] = drop
            print(fmt(key, s))
    board.stop()
    idle = [max(abs(lo[k]), abs(hi[k])) for k in lo if hi[k] < NOISE]
    print(f"\nKeys seen: {len(lo)}. Worst idle jitter on untouched keys: "
          f"{max(idle) if idle else 'n/a'} counts")
    out = {str(k): {"mindrop": lo[k], "maxdrop": hi[k]} for k in lo}
    with open(CALIB_FILE, "w") as f:
        json.dump(out, f, indent=1)
    print(f"Saved to {CALIB_FILE}. Keys pressed (maxdrop >= {NOISE}):")
    for k in sorted(hi):
        if hi[k] >= NOISE:
            print(f"  key {k:3d}  maxdrop={hi[k]}")


def pad(board):
    import vgamepad as vg

    try:
        with open(CALIB_FILE) as f:
            raw = json.load(f)
    except FileNotFoundError:
        raw = {}
    full = {}
    for k, v in raw.items():
        md = v.get("maxdrop", 0)
        if md >= 900:               # only trust presses that really bottomed out
            full[int(k)] = md
    gp = vg.VX360Gamepad()
    depth = {}

    def norm(key, s):
        drop = s[0] - s[2]
        if drop < NOISE:
            return 0.0
        f = full.get(key, DEFAULT_FULL)
        return min(1.0, (drop - NOISE) / (f - NOISE))

    def d(key):
        return depth.get(key, 0.0)

    board.start()
    print("Running. Hold Esc fully for 0.5s to quit.")
    exit_since = None
    try:
        for item in board.packets():
            if item is not None:
                key, s = item
                depth[key] = norm(key, s)

            # exit combo
            if d(EXIT_KEY) > 0.8:
                exit_since = exit_since or time.time()
                if time.time() - exit_since > 0.5:
                    break
            else:
                exit_since = None

            gp.left_joystick_float(
                max(-1, min(1, d(AXES["lx"][1]) - d(AXES["lx"][0]))),
                max(-1, min(1, d(AXES["ly"][1]) - d(AXES["ly"][0]))),
            )
            gp.right_joystick_float(
                max(-1, min(1, d(AXES["rx"][1]) - d(AXES["rx"][0]))),
                max(-1, min(1, d(AXES["ry"][1]) - d(AXES["ry"][0]))),
            )
            gp.left_trigger_float(d(TRIGGERS["lt"]))
            gp.right_trigger_float(d(TRIGGERS["rt"]))
            for key, name in BUTTONS.items():
                btn = getattr(vg.XUSB_BUTTON, name)
                if d(key) > BUTTON_THRESHOLD:
                    gp.press_button(btn)
                else:
                    gp.release_button(btn)
            gp.update()
    finally:
        board.stop()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "list":
        list_ifaces()
        sys.exit()
    b = Board()
    if mode == "dump":
        b.start()
        secs = float(sys.argv[2]) if len(sys.argv) > 2 else 20
        only = int(sys.argv[3]) if len(sys.argv) > 3 else None
        dump(b, secs, only)
    elif mode == "pad":
        pad(b)
    else:
        print(__doc__)
