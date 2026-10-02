#!/usr/bin/env python3
"""
Static check of the USB-MIDI receive switch in src/usb/usbd_midi_core.c.

Every USB-MIDI event packet starts with a Code Index Number (CIN). usbd_midi_DataOut()
sorts packets by CIN and forwards the MIDI bytes to the decoder; a CIN that has no case
label is dropped without any trace. Polyphonic key pressure (CIN 0xA) was missing, so
polyphonic aftertouch never reached MidiDecoder over USB while channel pressure (CIN 0xD)
did. The decoder simulations cannot see this, they start behind the packetization.

This test reads the C source and checks that every channel voice message has a label in
the group with the right data byte count:

    3 bytes : 0x8 note off, 0x9 note on, 0xA poly key pressure, 0xB control change,
              0xE pitch bend
    2 bytes : 0xC program change, 0xD channel pressure

It does not execute firmware code. Usage:

    python test/host/usb_midi_cin_test.py [path/to/usbd_midi_core.c]
"""

import os
import re
import sys

THREE_BYTES = {0x8: "note off", 0x9: "note on", 0xA: "polyphonic key pressure",
               0xB: "control change", 0xE: "pitch bend"}
TWO_BYTES = {0xC: "program change", 0xD: "channel pressure"}


def default_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "..", "src", "usb", "usbd_midi_core.c")


def groups(source):
    """Return {'3': set(cins), '2': set(cins)} from the DataOut switch."""
    start = source.index("usbd_midi_DataOut(void *pdev, uint8_t epnum) {")
    body = source[start:]
    body = body[:body.index("usbd_midi_SOF")]
    result = {"3": set(), "2": set()}
    current = None
    for line in body.splitlines():
        if "3 bytes" in line:
            current = "3"
        elif "2 bytes" in line:
            current = "2"
        elif "1 byte" in line:
            current = None
        for match in re.finditer(r"case\s+0x([0-9a-fA-F]+)\s*:", line):
            if current:
                result[current].add(int(match.group(1), 16))
    return result


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else default_path()
    source = open(path, encoding="utf-8", errors="replace").read()
    found = groups(source)
    failures = []
    for cin, name in sorted(THREE_BYTES.items()):
        if cin not in found["3"]:
            failures.append("CIN 0x%X (%s) is not handled as a 3 byte packet" % (cin, name))
    for cin, name in sorted(TWO_BYTES.items()):
        if cin not in found["2"]:
            failures.append("CIN 0x%X (%s) is not handled as a 2 byte packet" % (cin, name))
    if failures:
        print("USB MIDI CIN TEST FAILED (%s):" % path)
        for failure in failures:
            print("  - " + failure)
        return 1
    print("usb midi cin test: %d channel voice CINs handled in the right byte count group"
          % (len(THREE_BYTES) + len(TWO_BYTES)))
    print("static source check only (no firmware code executed, no hardware test)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
