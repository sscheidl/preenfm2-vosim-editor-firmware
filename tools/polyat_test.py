#!/usr/bin/env python3
"""
PreenFM2 polyphonic key pressure - MIDI test sender.

Sends controlled MIDI 1.0 sequences to a PreenFM2 running the true PolyAT firmware
(branch feature/true-poly-aftertouch) so the behaviour can be checked on real hardware.

Safety rules built into this tool:

  * it NEVER picks a MIDI port on its own. You must name one with --port, and until you
    do, nothing is sent;
  * it never sends SysEx, and never sends a MIDI reset, all-notes-off-as-panic or any
    other destructive message. The only control change it can send is CC64 (sustain),
    and only in the scenario that is about sustain;
  * --dry-run prints every event, with its raw MIDI bytes, and opens no port at all;
  * every scenario ends by releasing the notes it started, and Ctrl-C releases whatever
    is still held.

Dependency: mido (plus a backend, e.g. python-rtmidi) - only needed to actually send.
--dry-run, --self-test and --list-ports without mido installed all work on a bare
Python 3, except that --list-ports obviously needs mido to see any port.

    pip install mido python-rtmidi

Usage:

    python tools/polyat_test.py --list-ports
    python tools/polyat_test.py --scenario A --dry-run
    python tools/polyat_test.py --scenario all --dry-run
    python tools/polyat_test.py --scenario A --port "PreenFM2 1"
    python tools/polyat_test.py --scenario B --port 1 --channel 2
    python tools/polyat_test.py --self-test

See docs/POLYAT_HARDWARE_TEST_CHECKLIST.md for what to listen for in each scenario.
"""

import argparse
import sys
import time

# ---------------------------------------------------------------------------
# MIDI status bytes. Kept explicit so the wire format is reviewable here.
# ---------------------------------------------------------------------------

NOTE_OFF = 0x80
NOTE_ON = 0x90
POLY_PRESSURE = 0xA0      # data1 = note, data2 = pressure   <- polyphonic key pressure
CONTROL_CHANGE = 0xB0
CHANNEL_PRESSURE = 0xD0   # data1 = pressure, NO second data byte

CC_SUSTAIN = 64

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Middle C = C4 = 60, the convention the PreenFM2 display uses.
C4, E4, G4, D4, A4 = 60, 64, 67, 62, 69


def note_name(note):
    return "%s%d" % (NOTE_NAMES[note % 12], (note // 12) - 1)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class Event(object):
    """One MIDI event, plus the delay to wait after sending it."""

    def __init__(self, kind, channel, data1, data2=None, delay=0.0, comment=""):
        if not 1 <= channel <= 16:
            raise ValueError("channel must be 1..16, got %r" % channel)
        for value in (data1, data2):
            if value is not None and not 0 <= value <= 127:
                raise ValueError("data byte out of range 0..127: %r" % value)
        self.kind = kind
        self.channel = channel
        self.data1 = data1
        self.data2 = data2
        self.delay = delay
        self.comment = comment

    def status(self):
        return {
            "note_on": NOTE_ON,
            "note_off": NOTE_OFF,
            "polytouch": POLY_PRESSURE,
            "aftertouch": CHANNEL_PRESSURE,
            "control_change": CONTROL_CHANGE,
        }[self.kind] | (self.channel - 1)

    def raw(self):
        """The bytes that go on the wire. Channel pressure is two bytes, not three."""
        if self.kind == "aftertouch":
            return [self.status(), self.data1]
        return [self.status(), self.data1, self.data2]

    def describe(self):
        raw = " ".join("%02X" % b for b in self.raw())
        if self.kind == "polytouch":
            what = "PolyAT   %-4s pressure %3d" % (note_name(self.data1), self.data2)
        elif self.kind == "aftertouch":
            what = "ChanAT        pressure %3d" % self.data1
        elif self.kind == "note_on":
            what = "NoteOn   %-4s velocity %3d" % (note_name(self.data1), self.data2)
        elif self.kind == "note_off":
            what = "NoteOff  %-4s velocity %3d" % (note_name(self.data1), self.data2)
        else:
            what = "CC %-3d        value    %3d" % (self.data1, self.data2)
        line = "ch%-2d  %-31s  [%s]" % (self.channel, what, raw)
        if self.comment:
            line += "   # " + self.comment
        return line

    def to_mido(self):
        import mido
        channel = self.channel - 1
        if self.kind == "note_on":
            return mido.Message("note_on", channel=channel, note=self.data1,
                                velocity=self.data2)
        if self.kind == "note_off":
            return mido.Message("note_off", channel=channel, note=self.data1,
                                velocity=self.data2)
        if self.kind == "polytouch":
            return mido.Message("polytouch", channel=channel, note=self.data1,
                                value=self.data2)
        if self.kind == "aftertouch":
            return mido.Message("aftertouch", channel=channel, value=self.data1)
        if self.kind == "control_change":
            return mido.Message("control_change", channel=channel,
                                control=self.data1, value=self.data2)
        raise ValueError("unknown event kind %r" % self.kind)


def note_on(ch, note, velocity=100, delay=0.0, comment=""):
    return Event("note_on", ch, note, velocity, delay, comment)


def note_off(ch, note, delay=0.0, comment=""):
    return Event("note_off", ch, note, 0, delay, comment)


def poly(ch, note, pressure, delay=0.0, comment=""):
    return Event("polytouch", ch, note, pressure, delay, comment)


def chan(ch, pressure, delay=0.0, comment=""):
    return Event("aftertouch", ch, pressure, None, delay, comment)


def sustain(ch, on, delay=0.0, comment=""):
    return Event("control_change", ch, CC_SUSTAIN, 127 if on else 0, delay, comment)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def scenario_a(ch, opt):
    """A. Single note, poly pressure stepped through 0 / 32 / 64 / 96 / 127."""
    events = [note_on(ch, C4, delay=opt.hold, comment="hold C4")]
    for pressure in (0, 32, 64, 96, 127):
        events.append(poly(ch, C4, pressure, delay=opt.step,
                           comment="expect only C4 to change"))
    events.append(poly(ch, C4, 0, delay=opt.step, comment="back to no pressure"))
    events.append(note_off(ch, C4, delay=opt.gap))
    return events


def scenario_b(ch, opt):
    """B. Three note chord, independent pressure per note."""
    events = [
        note_on(ch, C4, delay=opt.gap),
        note_on(ch, E4, delay=opt.gap),
        note_on(ch, G4, delay=opt.hold, comment="C4 E4 G4 held"),
        poly(ch, C4, 127, delay=opt.step, comment="only C4 must move"),
        poly(ch, E4, 32, delay=opt.step, comment="only E4 must move"),
        poly(ch, G4, 96, delay=opt.step, comment="only G4 must move"),
        poly(ch, E4, 0, delay=opt.step, comment="E4 back to 0, C4 and G4 unchanged"),
    ]
    for note in (C4, E4, G4):
        events.append(note_off(ch, note, delay=opt.gap))
    return events


def scenario_c(ch, opt):
    """C. Channel pressure baseline, then a note, then poly pressure on it."""
    return [
        chan(ch, 70, delay=opt.step, comment="baseline before any note"),
        note_on(ch, D4, delay=opt.hold, comment="D4 must START at the 70 baseline"),
        note_on(ch, A4, delay=opt.hold, comment="A4 also starts at 70"),
        poly(ch, D4, 127, delay=opt.step, comment="only D4 moves, A4 stays at 70"),
        note_off(ch, D4, delay=opt.gap),
        note_off(ch, A4, delay=opt.gap),
        chan(ch, 0, delay=opt.step, comment="release the baseline"),
    ]


def scenario_d(ch, opt):
    """D. Channel pressure after poly pressure: broadcast must override."""
    return [
        note_on(ch, C4, delay=opt.gap),
        note_on(ch, E4, delay=opt.hold),
        poly(ch, C4, 127, delay=opt.step, comment="C4 up, E4 untouched"),
        poly(ch, E4, 16, delay=opt.step, comment="E4 down"),
        chan(ch, 50, delay=opt.step, comment="BOTH voices must jump to 50"),
        chan(ch, 0, delay=opt.step, comment="both back to 0"),
        note_off(ch, C4, delay=opt.gap),
        note_off(ch, E4, delay=opt.gap),
    ]


def scenario_e(ch, opt):
    """E. Rapid poly pressure sweep 0 -> 127 -> 0 on one note."""
    events = [note_on(ch, C4, delay=opt.hold, comment="sweep follows")]
    values = list(range(0, 128, opt.sweep_stride))
    if values[-1] != 127:
        values.append(127)
    for pressure in values + list(reversed(values)):
        events.append(poly(ch, C4, pressure, delay=opt.sweep_delay))
    events.append(poly(ch, C4, 0, delay=opt.step))
    events.append(note_off(ch, C4, delay=opt.gap))
    return events


def scenario_f(ch, opt):
    """F. Repeated same note: each new strike restarts from the channel baseline."""
    return [
        chan(ch, 40, delay=opt.step, comment="baseline 40"),
        note_on(ch, C4, delay=opt.hold),
        poly(ch, C4, 127, delay=opt.hold, comment="C4 pressed hard"),
        note_on(ch, C4, delay=opt.hold,
                comment="SAME note again: must fall back to 40, not stay at 127"),
        poly(ch, C4, 100, delay=opt.hold, comment="fresh poly pressure takes over"),
        note_off(ch, C4, delay=opt.gap),
        chan(ch, 0, delay=opt.step),
    ]


def scenario_g(ch, opt):
    """G. Sustain: a held voice stays addressable, channel pressure broadcasts over it."""
    return [
        note_on(ch, C4, delay=opt.gap),
        note_on(ch, E4, delay=opt.hold),
        sustain(ch, True, delay=opt.step, comment="pedal down"),
        note_off(ch, C4, delay=opt.gap, comment="key up, voice still sounding"),
        note_off(ch, E4, delay=opt.hold),
        poly(ch, C4, 127, delay=opt.hold,
             comment="held C4 voice must still follow its poly pressure"),
        chan(ch, 30, delay=opt.hold, comment="channel pressure broadcasts over it"),
        chan(ch, 0, delay=opt.step),
        sustain(ch, False, delay=opt.gap, comment="pedal up, voices release"),
    ]


def scenario_h(ch, opt):
    """H. High polyphony stress: several notes, interleaved poly pressure."""
    notes = [48, 52, 55, 60, 64, 67, 72, 76]
    events = []
    for note in notes:
        events.append(note_on(ch, note, delay=opt.gap))
    events.append(note_on(ch, 79, delay=opt.hold, comment="9 notes held"))
    notes = notes + [79]
    for cycle in range(opt.stress_cycles):
        for index, note in enumerate(notes):
            pressure = (cycle * 37 + index * 14) % 128
            events.append(poly(ch, note, pressure, delay=opt.stress_delay))
    for note in notes:
        events.append(poly(ch, note, 0, delay=opt.stress_delay))
    for note in notes:
        events.append(note_off(ch, note, delay=opt.gap))
    return events


SCENARIOS = {
    "A": ("single note, poly pressure 0/32/64/96/127", scenario_a),
    "B": ("three note chord, independent pressure", scenario_b),
    "C": ("channel pressure baseline, then note on", scenario_c),
    "D": ("channel pressure after poly pressure (broadcast override)", scenario_d),
    "E": ("rapid poly pressure sweep 0 -> 127 -> 0", scenario_e),
    "F": ("repeated same note", scenario_f),
    "G": ("sustain pedal (CC64)", scenario_g),
    "H": ("high polyphony poly pressure stress", scenario_h),
}


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------

def get_output_names():
    try:
        import mido
    except ImportError:
        print("mido is not installed, so no MIDI port can be listed.")
        print("Install it with:  pip install mido python-rtmidi")
        return None
    return mido.get_output_names()


def list_ports():
    names = get_output_names()
    if names is None:
        return 1
    if not names:
        print("No MIDI output port found.")
        return 1
    print("Available MIDI output ports:")
    for index, name in enumerate(names):
        print("  [%d] %s" % (index, name))
    print("\nSelect one explicitly, by index or by name:")
    print('  python tools/polyat_test.py --scenario A --port %d' % 0)
    print('  python tools/polyat_test.py --scenario A --port "%s"' % names[0])
    return 0


def resolve_port(selector):
    """Resolve --port to exactly one name. Never guesses."""
    names = get_output_names()
    if names is None:
        return None
    if not names:
        print("No MIDI output port found.")
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
        print("No MIDI output port matches %r." % selector)
    else:
        print("%r is ambiguous, it matches several ports:" % selector)
        for name in partial:
            print("   %s" % name)
        print("Give the exact name or the index instead.")
    list_ports()
    return None


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

def run(events, port, dry_run):
    """Send (or print) the events. Releases held notes on Ctrl-C."""
    held = set()
    try:
        for event in events:
            print("  " + event.describe())
            if not dry_run:
                port.send(event.to_mido())
            if event.kind == "note_on":
                held.add((event.channel, event.data1))
            elif event.kind == "note_off":
                held.discard((event.channel, event.data1))
            if event.delay > 0 and not dry_run:
                time.sleep(event.delay)
    except KeyboardInterrupt:
        print("\ninterrupted, releasing held notes")
        if not dry_run:
            for channel, note in sorted(held):
                port.send(note_off(channel, note).to_mido())
            held.clear()
        return 130
    return 0


# ---------------------------------------------------------------------------
# Self test - verifies the wire format without any MIDI device
# ---------------------------------------------------------------------------

def self_test():
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    # poly pressure: 0xA0 | channel, note, pressure
    check(poly(1, 60, 0).raw() == [0xA0, 60, 0], "poly ch1 min")
    check(poly(1, 60, 127).raw() == [0xA0, 60, 127], "poly ch1 max")
    check(poly(16, 60, 64).raw() == [0xAF, 60, 64], "poly ch16 status must be 0xAF")
    check(poly(2, 0, 127).raw() == [0xA1, 0, 127], "poly ch2 note 0")
    check(poly(1, 127, 127).raw() == [0xA0, 127, 127], "poly note 127")

    # channel pressure: 0xD0 | channel, pressure  -- TWO bytes only
    check(chan(1, 0).raw() == [0xD0, 0], "channel pressure ch1 min")
    check(chan(1, 127).raw() == [0xD0, 127], "channel pressure ch1 max")
    check(chan(16, 80).raw() == [0xDF, 80], "channel pressure ch16 status must be 0xDF")
    check(len(chan(1, 64).raw()) == 2, "channel pressure must be 2 bytes, not 3")
    check(len(poly(1, 60, 64).raw()) == 3, "poly pressure must be 3 bytes")

    # the two must never be confused
    check(poly(1, 60, 64).raw()[0] != chan(1, 64).raw()[0],
          "poly pressure and channel pressure share a status byte")

    # notes and CC
    check(note_on(1, 60, 100).raw() == [0x90, 60, 100], "note on")
    check(note_off(1, 60).raw() == [0x80, 60, 0], "note off")
    check(note_on(16, 60, 1).raw() == [0x9F, 60, 1], "note on ch16")
    check(sustain(1, True).raw() == [0xB0, 64, 127], "sustain on")
    check(sustain(1, False).raw() == [0xB0, 64, 0], "sustain off")

    # range guards
    for bad in (-1, 128):
        try:
            poly(1, 60, bad)
            failures.append("pressure %d was accepted" % bad)
        except ValueError:
            pass
    for bad in (0, 17):
        try:
            poly(bad, 60, 64)
            failures.append("channel %d was accepted" % bad)
        except ValueError:
            pass

    # no scenario may emit anything destructive
    class Opt(object):
        hold = step = gap = sweep_delay = stress_delay = 0.0
        sweep_stride = 16
        stress_cycles = 2

    allowed = {"note_on", "note_off", "polytouch", "aftertouch", "control_change"}
    for key, (_, builder) in sorted(SCENARIOS.items()):
        for channel in (1, 16):
            for event in builder(channel, Opt()):
                check(event.kind in allowed,
                      "scenario %s emits unexpected kind %r" % (key, event.kind))
                if event.kind == "control_change":
                    check(event.data1 == CC_SUSTAIN,
                          "scenario %s sends CC%d, only CC64 is allowed"
                          % (key, event.data1))
                check(event.channel == channel,
                      "scenario %s ignored the channel argument" % key)
                raw = event.raw()
                check(all(0 <= b <= 255 for b in raw),
                      "scenario %s produced a bad byte" % key)
                check(all(0 <= b <= 127 for b in raw[1:]),
                      "scenario %s produced a data byte with bit 7 set" % key)

    # every scenario must release what it pressed
    for key, (_, builder) in sorted(SCENARIOS.items()):
        held = set()
        for event in builder(1, Opt()):
            if event.kind == "note_on":
                held.add(event.data1)
            elif event.kind == "note_off":
                held.discard(event.data1)
        check(not held, "scenario %s leaves notes hanging: %s" % (key, sorted(held)))

    # sustain must always be released again
    for key, (_, builder) in sorted(SCENARIOS.items()):
        pedal = 0
        for event in builder(1, Opt()):
            if event.kind == "control_change" and event.data1 == CC_SUSTAIN:
                pedal = event.data2
        check(pedal == 0, "scenario %s leaves the sustain pedal down" % key)

    if failures:
        print("SELF TEST FAILED:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("self test passed: wire format, ranges, channels 1 and 16, "
          "no destructive message, no hanging note")
    return 0


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Send controlled MIDI polyphonic key pressure to a PreenFM2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="scenarios:\n" + "\n".join(
            "  %s  %s" % (k, SCENARIOS[k][0]) for k in sorted(SCENARIOS)))
    parser.add_argument("--list-ports", action="store_true",
                        help="list MIDI output ports and exit")
    parser.add_argument("--self-test", action="store_true",
                        help="verify the generated MIDI bytes and exit, no port needed")
    parser.add_argument("--scenario", metavar="ID",
                        help="scenario letter, or 'all'")
    parser.add_argument("--port", metavar="NAME_OR_INDEX",
                        help="MIDI output port; required unless --dry-run")
    parser.add_argument("--channel", type=int, default=1, metavar="N",
                        help="MIDI channel 1..16 (default 1)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print every event, open no port, send nothing")
    parser.add_argument("--hold", type=float, default=0.6, metavar="S",
                        help="pause after a note on (default 0.6)")
    parser.add_argument("--step", type=float, default=0.5, metavar="S",
                        help="pause after a pressure step (default 0.5)")
    parser.add_argument("--gap", type=float, default=0.2, metavar="S",
                        help="pause after a note off (default 0.2)")
    parser.add_argument("--sweep-delay", type=float, default=0.02, metavar="S",
                        help="pause between sweep steps in E (default 0.02)")
    parser.add_argument("--sweep-stride", type=int, default=4, metavar="N",
                        help="pressure increment in E (default 4)")
    parser.add_argument("--stress-delay", type=float, default=0.01, metavar="S",
                        help="pause between stress events in H (default 0.01)")
    parser.add_argument("--stress-cycles", type=int, default=4, metavar="N",
                        help="pressure cycles in H (default 4)")
    options = parser.parse_args()

    if options.self_test:
        return self_test()
    if options.list_ports:
        return list_ports()
    if not options.scenario:
        parser.print_help()
        print("\nNothing was sent. Choose a --scenario, or --list-ports first.")
        return 2
    if not 1 <= options.channel <= 16:
        print("--channel must be 1..16")
        return 2
    if options.sweep_stride < 1 or options.stress_cycles < 1:
        print("--sweep-stride and --stress-cycles must be >= 1")
        return 2

    keys = sorted(SCENARIOS) if options.scenario.lower() == "all" else \
        [options.scenario.upper()]
    for key in keys:
        if key not in SCENARIOS:
            print("Unknown scenario %r. Known: %s, or 'all'."
                  % (options.scenario, ", ".join(sorted(SCENARIOS))))
            return 2

    port = None
    if options.dry_run:
        print("DRY RUN - no MIDI port is opened, nothing is sent.\n")
    else:
        if not options.port:
            print("Refusing to send without an explicit --port.")
            print("This tool never picks a port on its own.\n")
            list_ports()
            return 2
        name = resolve_port(options.port)
        if name is None:
            return 2
        import mido
        print("Opening MIDI output: %s" % name)
        port = mido.open_output(name)

    status = 0
    try:
        for key in keys:
            title, builder = SCENARIOS[key]
            print("\n=== scenario %s: %s  (channel %d) ==="
                  % (key, title, options.channel))
            events = builder(options.channel, options)
            result = run(events, port, options.dry_run)
            if result:
                status = result
                break
    finally:
        if port is not None:
            port.close()
            print("\nMIDI port closed.")
    return status


if __name__ == "__main__":
    sys.exit(main())
