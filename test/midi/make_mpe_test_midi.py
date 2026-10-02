#!/usr/bin/env python3
"""
Build Standard MIDI Files that replay the PreenFM2 Lower-Zone MPE test scenarios A-J.

The MIDI messages are NOT re-implemented here: the scenarios are imported from
tools/mpe_test.py (the self-tested sender), so the files contain exactly the bytes that
tool sends with --scenario A..J. Only the timing is laid out on a bar grid so a DAW can
show and loop each scenario.

Defaults match the firmware defaults: master channel 1, members 2..16, member pitch bend
range 48 semitones. No RPN/MCM and no CC other than CC74 (slide) and CC64 (sustain on the
master channel) is written: outside the configured zone RPN CC100/101 can reach the
arpeggiator, so the zone is configured in the PreenFM2 menu instead.

    python test/midi/make_mpe_test_midi.py            # (re)write the two files here
    python test/midi/make_mpe_test_midi.py --check    # exit 1 if the files on disk are
                                                      # not what the scenarios generate

Needs mido (pip install mido) for writing and for the read-back check. Sending nothing,
opening no MIDI port.
"""

import argparse
import importlib.util
import os
import sys
import tempfile

import mido

sys.dont_write_bytecode = True               # never drop __pycache__ into the repository

PPQ = 480
BPM = 120
SECONDS_TO_TICKS = PPQ * BPM / 60.0          # 960 ticks per second at 120 bpm
TICKS_PER_BAR = PPQ * 4

TITLES = {
    "A": "A  Press: 2 notes, independent pressure",
    "B": "B  Glide: 2 notes, independent pitch bend (CHECK FIRST)",
    "C": "C  Slide CC74: 2 notes, independent",
    "D": "D  Press+Glide+Slide together, opposite directions",
    "E": "E  Same note on two channels, then channel reuse",
    "F": "F  Voice stealing: 15 notes",
    "G": "G  Sustain pedal on master ch 1",
    "H": "H  Master ch 1 pressure is zone wide",
    "I": "I  Expression BEFORE note on",
    "J": "J  Four-note chord, four channels",
}

FILES = ("PreenFM2_MPE_Test.mid", "PreenFM2_MPE_Test_PerChannel.mid")


def load_mpe_test(repo):
    path = os.path.join(repo, "tools", "mpe_test.py")
    if not os.path.isfile(path):
        sys.exit("tools/mpe_test.py not found under %s (use --repo)" % repo)
    spec = importlib.util.spec_from_file_location("mpe_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Opt(object):
    """Timing/parameters handed to the scenario builders (seconds)."""
    master = 1
    bend = 48            # must equal the PreenFM2 'MPE bend st' setting
    hold = 1.0
    step = 1.0
    gap = 0.5
    sweep_delay = 0.05
    sweep_steps = 24
    steal_notes = 15     # all 15 member channels: more notes than the timbre has voices


def to_ticks(seconds):
    return int(round(seconds * SECONDS_TO_TICKS))


def layout(mpe_test):
    """Return [(key, start_tick, [(abs_tick, Event)])] with every scenario on a bar line."""
    sections = []
    cursor = 1 * TICKS_PER_BAR                 # one bar of silence before the first scenario
    for key in sorted(mpe_test.SCENARIOS):
        events = mpe_test.SCENARIOS[key][1](Opt())
        timed, t = [], 0.0
        for e in events:                       # Event.delay is the pause AFTER the event
            timed.append((cursor + to_ticks(t), e))
            t += e.delay
        end = cursor + to_ticks(t)
        sections.append((key, cursor, timed))
        tail = end + 2 * TICKS_PER_BAR         # two bars of silence between scenarios
        cursor = ((tail + TICKS_PER_BAR - 1) // TICKS_PER_BAR) * TICKS_PER_BAR
    return sections, cursor


def meta_track(sections, end_tick):
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Conductor / markers", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(BPM), time=0))
    track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4,
                                  clocks_per_click=24, notated_32nd_notes_per_beat=8, time=0))
    stamped = [(0, mido.MetaMessage("marker", text="PreenFM2 MPE test, master ch1, members ch2-16, bend 48"))]
    for key, start, _ in sections:
        stamped.append((start, mido.MetaMessage("marker", text=TITLES[key])))
    stamped.append((end_tick, mido.MetaMessage("end_of_track")))
    return finish(track, stamped)


def finish(track, stamped):
    stamped.sort(key=lambda x: x[0])           # stable: keeps scenario order for equal ticks
    last = 0
    for tick, msg in stamped:
        track.append(msg.copy(time=tick - last))
        last = tick
    return track


def note_events(sections, channel_filter=None):
    stamped = []
    for _, _, timed in sections:
        for tick, e in timed:
            if channel_filter is None or e.channel == channel_filter:
                stamped.append((tick, e.to_mido()))
    return stamped


def write_single_track(sections, end_tick, path):
    mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    mid.tracks.append(meta_track(sections, end_tick))
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="PreenFM2 MPE (all channels)", time=0))
    stamped = note_events(sections)
    stamped.append((end_tick, mido.MetaMessage("end_of_track")))
    mid.tracks.append(finish(track, stamped))
    mid.save(path)


def write_per_channel(sections, end_tick, path):
    mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    mid.tracks.append(meta_track(sections, end_tick))
    used = sorted({e.channel for _, _, timed in sections for _, e in timed})
    for ch in used:
        track = mido.MidiTrack()
        role = "master" if ch == Opt.master else "member %d" % (ch - Opt.master)
        track.append(mido.MetaMessage("track_name", name="MPE ch %02d %s" % (ch, role), time=0))
        stamped = note_events(sections, ch)
        stamped.append((end_tick, mido.MetaMessage("end_of_track")))
        mid.tracks.append(finish(track, stamped))
    mid.save(path)


WRITERS = dict(zip(FILES, (write_single_track, write_per_channel)))


def read_back(path):
    """Absolute-tick list of (tick, raw bytes) for every channel message in the file."""
    mid = mido.MidiFile(path)
    out = []
    for track in mid.tracks:
        t = 0
        for msg in track:
            t += msg.time
            if not msg.is_meta:
                out.append((t, tuple(msg.bytes())))
    return sorted(out)


def verify(sections, path):
    expected = sorted((tick, tuple(e.raw())) for _, _, timed in sections for tick, e in timed)
    got = read_back(path)
    if expected != got:
        sys.exit("READ-BACK MISMATCH in %s: expected %d events, got %d"
                 % (path, len(expected), len(got)))
    return len(got)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_repo = os.path.dirname(os.path.dirname(here))      # test/midi -> repository root
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=default_repo,
                        help="repository root that contains tools/mpe_test.py")
    parser.add_argument("--out", default=here, help="directory of the .mid files")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 if the files in --out differ from what "
                             "tools/mpe_test.py scenarios generate")
    args = parser.parse_args()

    mpe_test = load_mpe_test(args.repo)
    sections, end_tick = layout(mpe_test)

    if args.check:
        bad = 0
        with tempfile.TemporaryDirectory() as tmp:
            for name in FILES:
                fresh = os.path.join(tmp, name)
                WRITERS[name](sections, end_tick, fresh)
                verify(sections, fresh)
                disk = os.path.join(args.out, name)
                if not os.path.isfile(disk):
                    print("MISSING  %s" % name)
                    bad += 1
                elif open(disk, "rb").read() != open(fresh, "rb").read():
                    print("DIFFERS  %s (regenerate with this script)" % name)
                    bad += 1
                else:
                    print("OK       %s" % name)
        sys.exit(1 if bad else 0)

    for name in FILES:
        path = os.path.join(args.out, name)
        WRITERS[name](sections, end_tick, path)
        n = verify(sections, path)
        print("%-34s %4d channel events, %.0f s, read-back identical to tools/mpe_test.py"
              % (name, n, end_tick / SECONDS_TO_TICKS))
    print("\nscenario start (bar, 4/4 @ %d bpm):" % BPM)
    for key, start, timed in sections:
        print("  %s  bar %3d   %3d events   %s" % (key, start // TICKS_PER_BAR + 1, len(timed), TITLES[key][3:]))


if __name__ == "__main__":
    main()
