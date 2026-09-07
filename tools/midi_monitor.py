#!/usr/bin/env python3
"""
MIDI input monitor - shows what a controller actually transmits.

Read only: it opens an input port and prints what arrives. It never sends anything.

Its point for this project is to answer one question before trusting any hardware test
result: does the controller send polyphonic key pressure (0xAn), channel pressure
(0xDn), or both? The PreenFM2 treats those two very differently, and several
controllers can be configured either way.

Dependency: mido (plus a backend, e.g. python-rtmidi)

    pip install mido python-rtmidi

Usage:

    python tools/midi_monitor.py --list-ports
    python tools/midi_monitor.py --port "LUMI Keys"
    python tools/midi_monitor.py --port 0 --summary

Stop with Ctrl-C. With --summary a per-message-type count is printed on exit.
"""

import argparse
import collections
import sys
import time

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# The types this project cares about, in the order they are documented.
INTERESTING = ("note_on", "note_off", "polytouch", "aftertouch",
               "pitchwheel", "control_change")


def note_name(note):
    return "%s%d" % (NOTE_NAMES[note % 12], (note // 12) - 1)


def get_input_names():
    try:
        import mido
    except ImportError:
        print("mido is not installed.")
        print("Install it with:  pip install mido python-rtmidi")
        return None
    return mido.get_input_names()


def list_ports():
    names = get_input_names()
    if names is None:
        return 1
    if not names:
        print("No MIDI input port found.")
        return 1
    print("Available MIDI input ports:")
    for index, name in enumerate(names):
        print("  [%d] %s" % (index, name))
    return 0


def resolve_port(selector):
    """Resolve --port to exactly one name. Never guesses."""
    names = get_input_names()
    if names is None:
        return None
    if not names:
        print("No MIDI input port found.")
        return None
    if selector.isdigit():
        index = int(selector)
        if not 0 <= index < len(names):
            print("Port index %d is out of range, %d port(s) available."
                  % (index, len(names)))
            list_ports()
            return None
        return names[index]
    exact = [n for n in names if n == selector]
    if len(exact) == 1:
        return exact[0]
    partial = [n for n in names if selector.lower() in n.lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        print("No MIDI input port matches %r." % selector)
    else:
        print("%r is ambiguous, it matches several ports:" % selector)
        for name in partial:
            print("   %s" % name)
        print("Give the exact name or the index instead.")
    list_ports()
    return None


def describe(message):
    """One line per message: type, channel, and whatever payload it carries."""
    kind = message.type
    channel = getattr(message, "channel", None)
    channel_text = "ch%-2d" % (channel + 1) if channel is not None else "--  "

    if kind in ("note_on", "note_off"):
        detail = "%-4s velocity %3d" % (note_name(message.note), message.velocity)
    elif kind == "polytouch":
        detail = "%-4s pressure %3d" % (note_name(message.note), message.value)
    elif kind == "aftertouch":
        detail = "     pressure %3d" % message.value
    elif kind == "pitchwheel":
        detail = "     value %6d" % message.pitch
    elif kind == "control_change":
        detail = "CC%-3d value    %3d" % (message.control, message.value)
    else:
        detail = str(message)

    raw = " ".join("%02X" % b for b in message.bytes())
    return "%s  %-15s %-22s [%s]" % (channel_text, kind, detail, raw)


def main():
    parser = argparse.ArgumentParser(
        description="Print incoming MIDI messages. Read only, sends nothing.")
    parser.add_argument("--list-ports", action="store_true",
                        help="list MIDI input ports and exit")
    parser.add_argument("--port", metavar="NAME_OR_INDEX",
                        help="MIDI input port to listen on")
    parser.add_argument("--all", action="store_true",
                        help="also show clock, active sensing and other traffic "
                             "(hidden by default, they flood the output)")
    parser.add_argument("--summary", action="store_true",
                        help="print a per-type count on exit")
    options = parser.parse_args()

    if options.list_ports:
        return list_ports()
    if not options.port:
        parser.print_help()
        print("\nNo port given, nothing was opened.")
        list_ports()
        return 2

    name = resolve_port(options.port)
    if name is None:
        return 2

    import mido
    counts = collections.Counter()
    started = time.time()
    print("Listening on: %s" % name)
    print("Ctrl-C to stop.\n")
    print("%-8s %-5s %-15s %-22s %s" % ("time", "chan", "type", "detail", "raw"))
    try:
        with mido.open_input(name) as port:
            for message in port:
                counts[message.type] += 1
                if not options.all and message.type not in INTERESTING:
                    continue
                print("%7.3f  %s" % (time.time() - started, describe(message)))
    except KeyboardInterrupt:
        print("\nstopped")

    if options.summary:
        print("\nmessage counts:")
        for kind in INTERESTING:
            if counts[kind]:
                print("  %-15s %d" % (kind, counts[kind]))
        other = {k: v for k, v in counts.items() if k not in INTERESTING}
        for kind in sorted(other):
            print("  %-15s %d" % (kind, other[kind]))
        if counts["polytouch"] and not counts["aftertouch"]:
            print("\n-> polyphonic key pressure only")
        elif counts["aftertouch"] and not counts["polytouch"]:
            print("\n-> channel pressure only; this controller will NOT exercise "
                  "the polyphonic path")
        elif counts["polytouch"] and counts["aftertouch"]:
            print("\n-> BOTH poly and channel pressure. On the PreenFM2 the later "
                  "message wins, and channel pressure is a broadcast, so it will "
                  "overwrite per-note values.")
        else:
            print("\n-> no pressure message of either kind was received")
    return 0


if __name__ == "__main__":
    sys.exit(main())
