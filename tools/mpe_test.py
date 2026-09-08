#!/usr/bin/env python3
"""
PreenFM2 MPE - deterministic multi channel MIDI test sender.

Companion to tools/polyat_test.py, same safety rules:

  * it NEVER picks a MIDI port on its own. Without an explicit --port nothing is sent;
  * no sysex, no MIDI reset, no panic message. The only control changes it can emit are
    CC74 (slide, on member channels) and CC64 (sustain, on the master channel, which is
    where MPE puts it);
  * --dry-run prints every event with its raw bytes and opens no port at all, so it runs
    on a bare Python 3 without mido;
  * every scenario releases the notes it started, and Ctrl-C releases what is still held.

It does NOT send the MPE Configuration Message. The firmware does not parse RPN at all
(see docs/MPE_IMPLEMENTATION_REPORT.md); the zone is configured in the PreenFM2 menu.
The default here matches the firmware default: master channel 1, members 2..16.

Dependency: mido (plus a backend) only to actually send.

    pip install mido python-rtmidi

Usage:

    python tools/mpe_test.py --list-ports
    python tools/mpe_test.py --scenario all --dry-run
    python tools/mpe_test.py --scenario A --port "PreenFM2 1"
    python tools/mpe_test.py --self-test
"""

import argparse
import sys
import time

NOTE_OFF = 0x80
NOTE_ON = 0x90
CONTROL_CHANGE = 0xB0
CHANNEL_PRESSURE = 0xD0
PITCH_BEND = 0xE0

CC_SUSTAIN = 64
CC_SLIDE = 74

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
C4, E4, G4, B4 = 60, 64, 67, 71


def note_name(note):
    return "%s%d" % (NOTE_NAMES[note % 12], (note // 12) - 1)


class Event(object):
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
        return {"note_on": NOTE_ON, "note_off": NOTE_OFF,
                "control_change": CONTROL_CHANGE, "aftertouch": CHANNEL_PRESSURE,
                "pitchwheel": PITCH_BEND}[self.kind] | (self.channel - 1)

    def raw(self):
        if self.kind == "aftertouch":
            return [self.status(), self.data1]
        return [self.status(), self.data1, self.data2]

    def describe(self):
        raw = " ".join("%02X" % b for b in self.raw())
        if self.kind == "aftertouch":
            what = "Press         value %5d" % self.data1
        elif self.kind == "pitchwheel":
            value = (self.data2 << 7) | self.data1
            what = "Glide         bend  %5d" % (value - 8192)
        elif self.kind == "note_on":
            what = "NoteOn   %-4s vel   %5d" % (note_name(self.data1), self.data2)
        elif self.kind == "note_off":
            what = "NoteOff  %-4s vel   %5d" % (note_name(self.data1), self.data2)
        elif self.data1 == CC_SLIDE:
            what = "Slide CC74    value %5d" % self.data2
        else:
            what = "CC %-3d        value %5d" % (self.data1, self.data2)
        line = "ch%-2d  %-30s  [%s]" % (self.channel, what, raw)
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
        if self.kind == "aftertouch":
            return mido.Message("aftertouch", channel=channel, value=self.data1)
        if self.kind == "pitchwheel":
            pitch = ((self.data2 << 7) | self.data1) - 8192
            return mido.Message("pitchwheel", channel=channel, pitch=pitch)
        if self.kind == "control_change":
            return mido.Message("control_change", channel=channel,
                                control=self.data1, value=self.data2)
        raise ValueError("unknown event kind %r" % self.kind)


def note_on(ch, note, velocity=100, delay=0.0, comment=""):
    return Event("note_on", ch, note, velocity, delay, comment)


def note_off(ch, note, delay=0.0, comment=""):
    return Event("note_off", ch, note, 0, delay, comment)


def press(ch, value, delay=0.0, comment=""):
    return Event("aftertouch", ch, value, None, delay, comment)


def slide(ch, value, delay=0.0, comment=""):
    return Event("control_change", ch, CC_SLIDE, value, delay, comment)


def sustain(ch, on, delay=0.0, comment=""):
    return Event("control_change", ch, CC_SUSTAIN, 127 if on else 0, delay, comment)


def glide(ch, semitones, bend_range, delay=0.0, comment=""):
    """Pitch bend expressed in semitones against the configured member range."""
    fraction = 0.0 if bend_range <= 0 else float(semitones) / bend_range
    fraction = max(-1.0, min(1.0, fraction))
    value = int(round(8192 + fraction * 8191))
    value = max(0, min(16383, value))
    return Event("pitchwheel", ch, value & 0x7F, (value >> 7) & 0x7F, delay, comment)


# ---------------------------------------------------------------------------
# scenarios. member(n) is the n-th member channel of the zone.
# ---------------------------------------------------------------------------

def members(opt, count):
    return [opt.master + 1 + i for i in range(count)]


def scenario_a(opt):
    """A. Two notes on two member channels, independent pressure."""
    m1, m2 = members(opt, 2)
    return [
        note_on(m1, C4, delay=opt.gap, comment="member 1 takes C4"),
        note_on(m2, E4, delay=opt.hold, comment="member 2 takes E4"),
        press(m1, 127, delay=opt.step, comment="only the C4 voice must move"),
        press(m2, 32, delay=opt.step, comment="only the E4 voice must move"),
        press(m1, 0, delay=opt.step, comment="C4 back to 0, E4 stays at 32"),
        note_off(m1, C4, delay=opt.gap),
        note_off(m2, E4, delay=opt.gap),
    ]


def scenario_b(opt):
    """B. Independent member pitch bend."""
    m1, m2 = members(opt, 2)
    return [
        note_on(m1, C4, delay=opt.gap),
        note_on(m2, E4, delay=opt.hold),
        glide(m1, 12, opt.bend, delay=opt.step, comment="C4 up one octave"),
        glide(m2, -7, opt.bend, delay=opt.step, comment="E4 down a fifth"),
        glide(m1, 0, opt.bend, delay=opt.step, comment="C4 back, E4 stays bent"),
        glide(m2, 0, opt.bend, delay=opt.step),
        note_off(m1, C4, delay=opt.gap),
        note_off(m2, E4, delay=opt.gap),
    ]


def scenario_c(opt):
    """C. Independent CC74 slide."""
    m1, m2 = members(opt, 2)
    return [
        note_on(m1, C4, delay=opt.gap),
        note_on(m2, G4, delay=opt.hold),
        slide(m1, 127, delay=opt.step, comment="only the C4 voice"),
        slide(m2, 16, delay=opt.step, comment="only the G4 voice"),
        slide(m1, 0, delay=opt.step),
        note_off(m1, C4, delay=opt.gap),
        note_off(m2, G4, delay=opt.gap),
    ]


def scenario_d(opt):
    """D. Press, Glide and Slide at the same time on two notes."""
    m1, m2 = members(opt, 2)
    events = [note_on(m1, C4, delay=opt.gap), note_on(m2, E4, delay=opt.hold)]
    for step in range(opt.sweep_steps + 1):
        f = float(step) / opt.sweep_steps
        events.append(press(m1, int(127 * f), delay=opt.sweep_delay))
        events.append(glide(m1, 12 * f, opt.bend, delay=opt.sweep_delay))
        events.append(slide(m1, int(127 * f), delay=opt.sweep_delay))
        events.append(press(m2, int(127 * (1 - f)), delay=opt.sweep_delay))
        events.append(glide(m2, -12 * f, opt.bend, delay=opt.sweep_delay))
        events.append(slide(m2, int(127 * (1 - f)), delay=opt.sweep_delay))
    for ch, note in ((m1, C4), (m2, E4)):
        events.append(press(ch, 0, delay=opt.step))
        events.append(glide(ch, 0, opt.bend, delay=opt.step))
        events.append(slide(ch, 0, delay=opt.step))
        events.append(note_off(ch, note, delay=opt.gap))
    return events


def scenario_e(opt):
    """E. Same note number on two member channels, then member channel reuse."""
    m1, m2 = members(opt, 2)
    return [
        note_on(m1, C4, delay=opt.gap, comment="both channels play the SAME note"),
        note_on(m2, C4, delay=opt.hold, comment="must be a second, separate voice"),
        press(m1, 127, delay=opt.step, comment="only one of the two must move"),
        press(m2, 0, delay=opt.step),
        note_off(m1, C4, delay=opt.gap),
        note_off(m2, C4, delay=opt.hold),
        note_on(m1, G4, delay=opt.hold,
                comment="member 1 reused: must start clean, no old pressure or bend"),
        note_off(m1, G4, delay=opt.gap),
    ]


def scenario_f(opt):
    """F. More notes than voices: the allocator has to steal."""
    chans = members(opt, opt.steal_notes)
    notes = [48 + 3 * i for i in range(opt.steal_notes)]
    events = []
    for ch, note in zip(chans, notes):
        events.append(note_on(ch, note, delay=opt.gap))
    events.append(press(chans[0], 127, delay=opt.hold,
                        comment="first note under full pressure before it is stolen"))
    for ch, note in zip(chans, notes):
        events.append(press(ch, 64, delay=opt.sweep_delay))
    for ch, note in zip(chans, notes):
        events.append(note_off(ch, note, delay=opt.gap))
    return events


def scenario_g(opt):
    """G. Sustain. MPE puts CC64 on the master channel."""
    m1, m2 = members(opt, 2)
    return [
        note_on(m1, C4, delay=opt.gap),
        note_on(m2, E4, delay=opt.hold),
        sustain(opt.master, True, delay=opt.step, comment="pedal down, master channel"),
        note_off(m1, C4, delay=opt.gap, comment="key up, voice keeps sounding"),
        note_off(m2, E4, delay=opt.hold),
        sustain(opt.master, False, delay=opt.gap, comment="pedal up, voices release"),
    ]


def scenario_h(opt):
    """H. Master channel expression is zone wide."""
    m1, m2 = members(opt, 2)
    return [
        note_on(m1, C4, delay=opt.gap),
        note_on(m2, E4, delay=opt.hold),
        press(m1, 127, delay=opt.step, comment="per note pressure on C4 only"),
        press(opt.master, 40, delay=opt.step,
              comment="MASTER pressure: every voice of the timbre must jump to 40"),
        press(opt.master, 0, delay=opt.step),
        note_off(m1, C4, delay=opt.gap),
        note_off(m2, E4, delay=opt.gap),
    ]


def scenario_i(opt):
    """I. Expression sent before the note on must apply to that note."""
    m1 = members(opt, 1)[0]
    return [
        press(m1, 100, delay=opt.step, comment="pressure BEFORE the note on"),
        slide(m1, 100, delay=opt.step, comment="slide BEFORE the note on"),
        glide(m1, 5, opt.bend, delay=opt.step, comment="bend BEFORE the note on"),
        note_on(m1, C4, delay=opt.hold,
                comment="the note must start already pressed, slid and bent"),
        note_off(m1, C4, delay=opt.gap),
    ]


def scenario_j(opt):
    """J. Four note chord across four member channels, each moving on its own."""
    chans = members(opt, 4)
    notes = [C4, E4, G4, B4]
    events = []
    for ch, note in zip(chans, notes):
        events.append(note_on(ch, note, delay=opt.gap))
    for index, (ch, note) in enumerate(zip(chans, notes)):
        events.append(press(ch, 31 * index + 34, delay=opt.step))
        events.append(glide(ch, index * 2 - 3, opt.bend, delay=opt.step))
        events.append(slide(ch, 127 - 31 * index, delay=opt.step))
    for ch, note in zip(chans, notes):
        events.append(press(ch, 0, delay=opt.sweep_delay))
        events.append(glide(ch, 0, opt.bend, delay=opt.sweep_delay))
        events.append(note_off(ch, note, delay=opt.gap))
    return events


SCENARIOS = {
    "A": ("two notes on two member channels, independent pressure", scenario_a),
    "B": ("independent member pitch bend", scenario_b),
    "C": ("independent CC74 slide", scenario_c),
    "D": ("press + glide + slide together on two notes", scenario_d),
    "E": ("same note on two channels, then member channel reuse", scenario_e),
    "F": ("more notes than voices, voice stealing", scenario_f),
    "G": ("sustain on the master channel", scenario_g),
    "H": ("master channel expression is zone wide", scenario_h),
    "I": ("expression sent before the note on", scenario_i),
    "J": ("four note chord, four member channels", scenario_j),
}


# ---------------------------------------------------------------------------
# ports
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
    return 0


def resolve_port(selector):
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


def run(events, port, dry_run):
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
        return 130
    return 0


# ---------------------------------------------------------------------------
# self test
# ---------------------------------------------------------------------------

def self_test():
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    # status bytes
    check(note_on(2, 60, 100).raw() == [0x91, 60, 100], "note on member channel 2")
    check(note_off(16, 60).raw() == [0x8F, 60, 0], "note off channel 16")
    check(press(2, 127).raw() == [0xD1, 127], "member pressure must be 2 bytes")
    check(len(press(2, 64).raw()) == 2, "channel pressure length")
    check(slide(2, 127).raw() == [0xB1, 74, 127], "slide is CC74")
    check(sustain(1, True).raw() == [0xB0, 64, 127], "sustain is CC64 on the master")

    # pitch bend encoding: centre, both extremes, and the 14 bit split
    centre = glide(2, 0, 48)
    check(centre.raw()[0] == 0xE1, "pitch bend status on channel 2")
    check(((centre.raw()[2] << 7) | centre.raw()[1]) == 8192, "bend centre must be 8192")
    up = glide(2, 48, 48)
    check(((up.raw()[2] << 7) | up.raw()[1]) == 16383, "full up bend must be 16383")
    down = glide(2, -48, 48)
    check(((down.raw()[2] << 7) | down.raw()[1]) == 1, "full down bend")
    half = glide(2, 24, 48)
    value = (half.raw()[2] << 7) | half.raw()[1]
    check(8192 + 4000 < value < 8192 + 4200, "half range bend landed at %d" % value)
    # out of range is clamped, never wrapped
    for semitones in (-999, 999):
        e = glide(2, semitones, 48)
        v = (e.raw()[2] << 7) | e.raw()[1]
        check(0 <= v <= 16383, "clamping failed for %d semitones" % semitones)
        check(all(0 <= b <= 127 for b in e.raw()[1:]), "bend data byte has bit 7 set")

    # ranges
    for bad in (-1, 128):
        try:
            press(2, bad)
            failures.append("pressure %d accepted" % bad)
        except ValueError:
            pass
    for bad in (0, 17):
        try:
            press(bad, 64)
            failures.append("channel %d accepted" % bad)
        except ValueError:
            pass

    class Opt(object):
        master = 1
        bend = 48
        hold = step = gap = sweep_delay = 0.0
        sweep_steps = 4
        steal_notes = 6

    allowed = {"note_on", "note_off", "aftertouch", "pitchwheel", "control_change"}
    for key, (_, builder) in sorted(SCENARIOS.items()):
        events = builder(Opt())
        held, pedal = set(), 0
        for e in events:
            check(e.kind in allowed, "scenario %s emits %r" % (key, e.kind))
            if e.kind == "control_change":
                check(e.data1 in (CC_SLIDE, CC_SUSTAIN),
                      "scenario %s sends CC%d; only CC74 and CC64 are allowed"
                      % (key, e.data1))
                if e.data1 == CC_SUSTAIN:
                    check(e.channel == Opt.master,
                          "scenario %s sends sustain off the master channel" % key)
                    pedal = e.data2
                else:
                    check(e.channel != Opt.master,
                          "scenario %s sends slide on the master channel" % key)
            if e.kind == "aftertouch" and e.channel != Opt.master:
                check(e.channel > Opt.master,
                      "scenario %s uses a channel below the master as a member" % key)
            check(all(0 <= b <= 127 for b in e.raw()[1:]),
                  "scenario %s produced a data byte with bit 7 set" % key)
            if e.kind == "note_on":
                held.add((e.channel, e.data1))
            elif e.kind == "note_off":
                held.discard((e.channel, e.data1))
        check(not held, "scenario %s leaves notes hanging: %s" % (key, sorted(held)))
        check(pedal == 0, "scenario %s leaves the sustain pedal down" % key)

    # scenario E really must use one note number on two different channels
    e_events = SCENARIOS["E"][1](Opt())
    ons = [(e.channel, e.data1) for e in e_events if e.kind == "note_on"]
    same = [c for c, n in ons if n == C4]
    check(len(set(same)) >= 2,
          "scenario E no longer plays the same note on two member channels")

    if failures:
        print("SELF TEST FAILED:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("self test passed: status bytes, 14 bit pitch bend incl. both extremes and "
          "clamping, channel/value ranges, master vs member channel use, no "
          "destructive message, no hanging note")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Send a deterministic MPE sequence to a PreenFM2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="scenarios:\n" + "\n".join(
            "  %s  %s" % (k, SCENARIOS[k][0]) for k in sorted(SCENARIOS)))
    parser.add_argument("--list-ports", action="store_true")
    parser.add_argument("--self-test", action="store_true",
                        help="verify the generated MIDI bytes and exit, no port needed")
    parser.add_argument("--scenario", metavar="ID", help="scenario letter, or 'all'")
    parser.add_argument("--port", metavar="NAME_OR_INDEX",
                        help="MIDI output port; required unless --dry-run")
    parser.add_argument("--dry-run", action="store_true",
                        help="print every event, open no port, send nothing")
    parser.add_argument("--master", type=int, default=1, metavar="N",
                        help="MPE master channel 1..16 (default 1)")
    parser.add_argument("--bend", type=int, default=48, metavar="ST",
                        help="member pitch bend range in semitones, 1..48; must match "
                             "the PreenFM2 'MPE bend st' setting (default 48). The "
                             "firmware clamps a member range to 48 because the pitch "
                             "path saturates above it, so sending more is not a test "
                             "of anything")
    parser.add_argument("--hold", type=float, default=0.6, metavar="S")
    parser.add_argument("--step", type=float, default=0.5, metavar="S")
    parser.add_argument("--gap", type=float, default=0.2, metavar="S")
    parser.add_argument("--sweep-delay", type=float, default=0.02, metavar="S")
    parser.add_argument("--sweep-steps", type=int, default=16, metavar="N")
    parser.add_argument("--steal-notes", type=int, default=8, metavar="N",
                        help="notes played in scenario F (default 8)")
    options = parser.parse_args()

    if options.self_test:
        return self_test()
    if options.list_ports:
        return list_ports()
    if not options.scenario:
        parser.print_help()
        print("\nNothing was sent. Choose a --scenario, or --list-ports first.")
        return 2
    if not 1 <= options.master <= 16:
        print("--master must be 1..16")
        return 2
    if not 1 <= options.bend <= 48:
        print("--bend must be 1..48 semitones: the firmware clamps a member range to "
              "48 (MPE_MEMBER_BEND_MAX), because the per voice frequency path cannot "
              "render more. See docs/MPE_IMPLEMENTATION_REPORT.md section 21.")
        return 2
    if options.sweep_steps < 1 or options.steal_notes < 1:
        print("--sweep-steps and --steal-notes must be >= 1")
        return 2
    if options.master + options.steal_notes > 16:
        print("master channel %d plus %d notes needs channels beyond 16; lower "
              "--steal-notes or --master" % (options.master, options.steal_notes))
        return 2

    keys = sorted(SCENARIOS) if options.scenario.lower() == "all" \
        else [options.scenario.upper()]
    for key in keys:
        if key not in SCENARIOS:
            print("Unknown scenario %r. Known: %s, or 'all'."
                  % (options.scenario, ", ".join(sorted(SCENARIOS))))
            return 2

    port = None
    if options.dry_run:
        print("DRY RUN - no MIDI port is opened, nothing is sent.")
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

    print("zone: master channel %d, members %d..%d, member bend range %d semitones"
          % (options.master, options.master + 1, 16, options.bend))
    print("the PreenFM2 must be set to the same master channel and bend range.")

    status = 0
    try:
        for key in keys:
            title, builder = SCENARIOS[key]
            print("\n=== scenario %s: %s ===" % (key, title))
            result = run(builder(options), port, options.dry_run)
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
