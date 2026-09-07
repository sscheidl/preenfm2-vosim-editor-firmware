#!/usr/bin/env python3
"""
Polyphonic key pressure (shared AftT source) - static / simulated check.

This is NOT a firmware unit test and it does NOT run any firmware code, for the same
reason as protocol_sim_test.py next to it: there is no host C++ toolchain in this
project.

What it does instead:

  1. it asserts, against the real sources, the things the design is not allowed to
     change: no new matrix source, SourceEnum and MATRIX_SOURCE_MAX untouched, no
     'PolA' display entry, no preset structure change;
  2. it asserts that the pieces the design does need are actually present in the real
     sources: the timbre baseline member, the broadcast/selective split, the note-on
     restore, the reset on new parameter load, the negative voice index guard, and
     that the poly pressure write is unconditional (no unison special case, no early
     return, so every active voice playing the note is addressed);
  3. it replays the event sequences of the assignment through a transcription of the
     C++ control flow (Timbre::setMatrixChannelAfterTouch, setMatrixPolyAfterTouch,
     preenNoteOn/preenNoteOnUpdateMatrix, afterNewParamsLoad) and checks the resulting
     per-voice AftT values.

Point 3 is a simulation of the control flow, so it can only catch state model and
control flow mistakes, never a compiler or hardware level problem. Every real hardware
test is still open - see docs/POLYAT_IMPLEMENTATION_REPORT.md.

Run:  python test/host/polyat_state_test.py
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
COMMON_H = os.path.join(REPO, "src", "synth", "Common.h")
TIMBRE_H = os.path.join(REPO, "src", "synth", "Timbre.h")
TIMBRE_CPP = os.path.join(REPO, "src", "synth", "Timbre.cpp")
DECODER_CPP = os.path.join(REPO, "src", "midi", "MidiDecoder.cpp")
STATE_CPP = os.path.join(REPO, "src", "synth", "SynthState.cpp")

failures = []
checks = [0]


def check(condition, message):
    checks[0] += 1
    if not condition:
        failures.append(message)


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# 1. what must NOT have changed
# ---------------------------------------------------------------------------

def test_no_new_matrix_source():
    common = read(COMMON_H)
    state = read(STATE_CPP)

    check("MATRIX_SOURCE_POLYPHONIC_AFTERTOUCH" not in common,
          "Common.h gained MATRIX_SOURCE_POLYPHONIC_AFTERTOUCH, option B forbids it")
    check("MATRIX_SOURCE_POLYPHONIC_AFTERTOUCH" not in state,
          "SynthState.cpp references a polyphonic aftertouch source")
    check('"PolA"' not in state and '"pAT ' not in state,
          "a PolA entry appeared in the matrix source display names")

    # The enum must still end exactly as before: ... MPESLIDE, RANDOM, MAX
    enum_body = common.split("enum SourceEnum")[1].split("}")[0]
    enum_body = "\n".join(l for l in enum_body.splitlines()
                          if not l.strip().startswith("#"))
    names = [n.strip().split("=")[0].strip()
             for n in enum_body.split("{")[1].split(",") if n.strip()]
    names = [n for n in names if n.startswith("MATRIX_SOURCE")]
    check(names[-3:] == ["MATRIX_SOURCE_MPESLIDE",
                         "MATRIX_SOURCE_RANDOM",
                         "MATRIX_SOURCE_MAX"],
          "SourceEnum no longer ends with MPESLIDE, RANDOM, MAX but with %s" % names[-3:])
    check(names.index("MATRIX_SOURCE_AFTERTOUCH") == 10,
          "MATRIX_SOURCE_AFTERTOUCH is no longer source id 10")

    # display tables keep their length: 21 entries without CVIN, 25 with CVIN
    orders = re.findall(r"matrixSourceOrder\[\]\s*=\s*\{([^}]*)\}", state)
    positions = re.findall(r"matrixSourcePosition\[\]\s*=\s*\{([^}]*)\}", state)
    check(len(orders) == 2 and len(positions) == 2,
          "expected the two #ifdef CVIN variants of the matrix source tables")
    lengths = sorted(len([x for x in o.split(",") if x.strip()]) for o in orders)
    check(lengths == [21, 25],
          "matrix source order tables changed length: %s" % lengths)


def test_no_preset_format_change():
    common = read(COMMON_H)
    row = common.split("struct MatrixRowParams")[1].split("}")[0]
    fields = [f for f in ("source", "mul", "dest1", "dest2") if f in row]
    check(len(fields) == 4,
          "struct MatrixRowParams fields changed: %s" % row.strip())


# ---------------------------------------------------------------------------
# 2. what must be present
# ---------------------------------------------------------------------------

def test_implementation_present():
    timbre_h = read(TIMBRE_H)
    timbre = read(TIMBRE_CPP)
    decoder = read(DECODER_CPP)

    check("float lastChannelAfterTouch_;" in timbre_h,
          "Timbre.h has no lastChannelAfterTouch_ member")
    check("void setMatrixChannelAfterTouch(float newValue);" in timbre_h,
          "Timbre.h does not declare setMatrixChannelAfterTouch")
    check("void setMatrixPolyAfterTouch(uint8_t note, float newValue);" in timbre_h,
          "Timbre.h does not declare setMatrixPolyAfterTouch")

    check("this->lastChannelAfterTouch_ = 0.0f;" in timbre.split("Timbre::Timbre")[1][:800],
          "the constructor does not initialise lastChannelAfterTouch_")

    # channel pressure: remembers AND broadcasts
    chan = timbre.split("void Timbre::setMatrixChannelAfterTouch")[1].split("\n}")[0]
    check("lastChannelAfterTouch_ = newValue" in chan,
          "setMatrixChannelAfterTouch does not store the baseline")
    check("setMatrixSource(MATRIX_SOURCE_AFTERTOUCH, newValue)" in chan,
          "setMatrixChannelAfterTouch does not broadcast to the voices")

    # poly pressure: selective, guarded, unison aware, does NOT touch the baseline
    poly = timbre.split("void Timbre::setMatrixPolyAfterTouch")[1].split("\n}\n")[0]
    check("lastChannelAfterTouch_" not in poly,
          "setMatrixPolyAfterTouch must not modify the channel baseline")
    check("if (unlikely(n < 0))" in poly,
          "setMatrixPolyAfterTouch does not guard a negative voice index")
    check("isPlaying()" in poly,
          "setMatrixPolyAfterTouch does not require the voice to be playing")
    check("getNote() == note" in poly,
          "setMatrixPolyAfterTouch does not match on the voice note")
    check("isUnison" not in poly,
          "setMatrixPolyAfterTouch still special cases unison; the rule is unconditional")
    check("return;" not in poly,
          "setMatrixPolyAfterTouch still returns early; every matching voice must be written")
    check("MATRIX_SOURCE_AFTERTOUCH" in poly,
          "setMatrixPolyAfterTouch does not write the shared aftertouch source")

    # note on restore
    upd = timbre.split("void Timbre::preenNoteOnUpdateMatrix")[1].split("\n}")[0]
    check("setSource(MATRIX_SOURCE_AFTERTOUCH, this->lastChannelAfterTouch_)" in upd,
          "preenNoteOnUpdateMatrix does not restore the channel baseline")

    # reset with the matrix sources
    load = timbre.split("void Timbre::afterNewParamsLoad")[1].split("\n}")[0]
    check("lastChannelAfterTouch_ = 0.0f" in load,
          "afterNewParamsLoad does not reset the channel baseline")

    # midi dispatch
    dispatch = decoder.split("void MidiDecoder::midiEventReceived")[1]
    poly_case = dispatch.split("case MIDI_POLY_AFTER_TOUCH:")[1].split("break;")[0]
    check("setMatrixPolyAfterTouch" in poly_case,
          "MIDI_POLY_AFTER_TOUCH is still a no-op")
    check("midiEvent.value[0]" in poly_case and "INV127*midiEvent.value[1]" in poly_case,
          "MIDI_POLY_AFTER_TOUCH does not use note=value[0], pressure=value[1]")
    check("timbres[tk]" in poly_case,
          "MIDI_POLY_AFTER_TOUCH does not use the addressed timbre list")
    check("shiftNote" not in poly_case,
          "preenfm3 shiftNote logic leaked into the pfm2 poly aftertouch path")

    chan_case = dispatch.split("case MIDI_AFTER_TOUCH:")[1].split("break;")[0]
    check("setMatrixChannelAfterTouch(INV127*midiEvent.value[0])" in chan_case,
          "MIDI_AFTER_TOUCH does not go through setMatrixChannelAfterTouch")


def test_no_mpe_added():
    timbre = read(TIMBRE_CPP)
    decoder = read(DECODER_CPP)
    for name in ("noteOnMPE", "noteOffMPE", "setMatrixSourceMPE",
                 "AFTERTOUCH_MPE", "PITCHBEND_MPE"):
        check(name not in timbre and name not in decoder,
              "MPE symbol %s appeared, MPE is not part of this task" % name)


def test_editor_protocol_untouched():
    decoder = read(DECODER_CPP)
    header = read(os.path.join(REPO, "src", "midi", "MidiDecoder.h"))
    check("EDITOR_CAPABILITIES (EDITOR_CAPABILITY_STORE | EDITOR_CAPABILITY_POSITION_QUERY)"
          in header, "the editor capability bitmask changed")
    check("#define EDITOR_PROTOCOL_VERSION 1" in header,
          "the editor protocol version changed")
    check("editorCommandDoneThisEvent" in decoder and "currentEventTimbreCount" in decoder,
          "editor remote protocol bookkeeping disappeared from midiEventReceived")


# ---------------------------------------------------------------------------
# 3. state model simulation
# ---------------------------------------------------------------------------

INV127 = 1.0 / 127.0


class Voice(object):
    """Only what the aftertouch paths look at."""

    def __init__(self):
        self.playing = False
        self.released = False
        self.newNotePending = False
        self.note = 0
        self.index = 0
        self.aftertouch = 0.0          # matrix.sources[MATRIX_SOURCE_AFTERTOUCH]

    def isPlaying(self):
        return self.playing

    def isReleased(self):
        return self.released

    def isNewNotePending(self):
        return self.newNotePending

    def getNote(self):
        return self.note


class Timbre(object):
    """Transcription of the C++ control flow, one timbre."""

    def __init__(self, numberOfVoice=4, unison=False):
        self.voices = [Voice() for _ in range(numberOfVoice)]
        self.voiceNumber = list(range(numberOfVoice)) + [-1] * (14 - numberOfVoice)
        self.numberOfVoice = numberOfVoice
        self.unison = unison
        self.voiceIndex = 1
        self.holdPedal = False
        self.lastChannelAfterTouch_ = 0.0      # Timbre::Timbre()

    # -- Timbre::setMatrixSource -------------------------------------------
    def setMatrixSource_aftertouch(self, value):
        for k in range(self.numberOfVoice):
            self.voices[self.voiceNumber[k]].aftertouch = value

    # -- Timbre::setMatrixChannelAfterTouch --------------------------------
    def setMatrixChannelAfterTouch(self, value):
        self.lastChannelAfterTouch_ = value
        self.setMatrixSource_aftertouch(value)

    # -- Timbre::setMatrixPolyAfterTouch -----------------------------------
    def setMatrixPolyAfterTouch(self, note, value):
        for k in range(self.numberOfVoice):
            n = self.voiceNumber[k]
            if n < 0:
                continue
            if not self.voices[n].isPlaying():
                continue
            if self.voices[n].getNote() == note:
                self.voices[n].aftertouch = value

    # -- Timbre::preenNoteOnUpdateMatrix (aftertouch part) -----------------
    def preenNoteOnUpdateMatrix(self, n):
        self.voices[n].aftertouch = self.lastChannelAfterTouch_

    # -- Timbre::preenNoteOn (allocation policy) ---------------------------
    def noteOn(self, note):
        iNov = 1 if self.unison else self.numberOfVoice
        voiceToUse = -1
        indexMin = 2147483647
        for k in range(iNov):
            n = self.voiceNumber[k]
            if self.voices[n].isNewNotePending():
                continue
            if self.voices[n].isPlaying() and self.voices[n].getNote() == note:
                # same note = priority 1, reuse that very voice
                targets = range(self.numberOfVoice) if self.unison else [k]
                for t in targets:
                    m = self.voiceNumber[t]
                    self.preenNoteOnUpdateMatrix(m)
                    self._start(m, note)
                return
            if voiceToUse == -1 or True:
                if not self.voices[n].isPlaying():
                    if voiceToUse == -1:
                        voiceToUse = n
                elif self.voices[n].isReleased():
                    if self.voices[n].index < indexMin:
                        indexMin = self.voices[n].index
                        voiceToUse = n
        if voiceToUse == -1:
            # steal the oldest
            for k in range(iNov):
                n = self.voiceNumber[k]
                if self.voices[n].index < indexMin:
                    indexMin = self.voices[n].index
                    voiceToUse = n
        if voiceToUse != -1:
            targets = range(self.numberOfVoice) if self.unison else [
                self.voiceNumber.index(voiceToUse)]
            for t in targets:
                m = self.voiceNumber[t]
                self.preenNoteOnUpdateMatrix(m)
                self._start(m, note)

    def _start(self, n, note):
        v = self.voices[n]
        v.playing = True
        v.released = False
        v.note = note
        v.index = self.voiceIndex
        self.voiceIndex += 1

    def noteOff(self, note):
        for k in range(self.numberOfVoice):
            n = self.voiceNumber[k]
            if not self.voices[n].isPlaying():
                continue
            if self.voices[n].getNote() == note:
                if self.holdPedal:
                    pass                       # stays playing, held by pedal
                else:
                    self.voices[n].released = True
                if not self.unison:
                    return

    # -- Timbre::afterNewParamsLoad ----------------------------------------
    def afterNewParamsLoad(self):
        self.lastChannelAfterTouch_ = 0.0
        for k in range(self.numberOfVoice):
            self.voices[self.voiceNumber[k]].aftertouch = 0.0   # matrix.resetSources()

    def at(self, note):
        for k in range(self.numberOfVoice):
            n = self.voiceNumber[k]
            if self.voices[n].isPlaying() and self.voices[n].getNote() == note:
                return self.voices[n].aftertouch
        return None


def near(a, b):
    return a is not None and abs(a - b) < 1e-6


def scenario_A():
    t = Timbre()
    t.noteOn(60)
    t.setMatrixChannelAfterTouch(INV127 * 80)
    for k in range(t.numberOfVoice):
        check(near(t.voices[k].aftertouch, INV127 * 80),
              "A: voice %d did not follow channel pressure" % k)


def scenario_B():
    t = Timbre()
    t.noteOn(60)
    t.noteOn(64)
    t.noteOn(67)
    t.setMatrixChannelAfterTouch(INV127 * 10)
    t.setMatrixPolyAfterTouch(60, INV127 * 127)
    t.setMatrixPolyAfterTouch(64, INV127 * 32)
    check(near(t.at(60), INV127 * 127), "B: C4 pressure wrong")
    check(near(t.at(64), INV127 * 32), "B: E4 pressure wrong")
    check(near(t.at(67), INV127 * 10), "B: G4 left the channel baseline")


def scenario_C():
    t = Timbre()
    t.noteOn(60)
    t.noteOn(64)
    t.setMatrixPolyAfterTouch(60, INV127 * 127)
    t.setMatrixChannelAfterTouch(INV127 * 50)
    check(near(t.at(60), INV127 * 50), "C: channel pressure did not override poly on C4")
    check(near(t.at(64), INV127 * 50), "C: channel pressure did not reach E4")


def scenario_D():
    t = Timbre()
    t.setMatrixChannelAfterTouch(INV127 * 70)
    t.noteOn(62)
    check(near(t.at(62), INV127 * 70), "D: new voice did not start at the channel baseline")


def scenario_E():
    t = Timbre(numberOfVoice=1)
    t.setMatrixChannelAfterTouch(INV127 * 40)
    t.noteOn(60)
    t.setMatrixPolyAfterTouch(60, INV127 * 127)
    check(near(t.at(60), INV127 * 127), "E: poly pressure did not reach C4")
    t.noteOn(62)                       # steals the only voice
    check(near(t.at(62), INV127 * 40),
          "E: reused voice inherited the poly pressure of the previous note")


def scenario_F():
    t = Timbre()
    t.setMatrixChannelAfterTouch(INV127 * 70)
    t.afterNewParamsLoad()
    check(near(t.lastChannelAfterTouch_, 0.0), "F: baseline survived a new parameter load")
    t.noteOn(60)
    check(near(t.at(60), 0.0), "F: note after a preset load did not start at 0")
    t.setMatrixChannelAfterTouch(INV127 * 70)
    check(near(t.at(60), INV127 * 70), "F: channel pressure no longer reaches the voice")


def scenario_G():
    t = Timbre(numberOfVoice=4, unison=True)
    t.noteOn(60)
    t.setMatrixPolyAfterTouch(60, INV127 * 100)
    for k in range(t.numberOfVoice):
        check(near(t.voices[k].aftertouch, INV127 * 100),
              "G: unison voice %d did not follow the note pressure" % k)


def scenario_H():
    t = Timbre()
    t.setMatrixChannelAfterTouch(INV127 * 20)
    t.noteOn(60)
    t.holdPedal = True
    t.noteOff(60)
    check(t.voices[0].isPlaying(), "H: held voice stopped playing")
    t.setMatrixPolyAfterTouch(60, INV127 * 90)
    check(near(t.at(60), INV127 * 90), "H: held voice is no longer a poly pressure target")
    t.setMatrixChannelAfterTouch(INV127 * 30)
    check(near(t.at(60), INV127 * 30), "H: channel pressure did not broadcast over the held voice")


def scenario_I():
    a = Timbre()
    b = Timbre()
    a.noteOn(60)
    b.noteOn(60)
    a.setMatrixPolyAfterTouch(60, INV127 * 127)
    check(near(a.at(60), INV127 * 127), "I: poly pressure did not reach timbre A")
    check(near(b.at(60), 0.0), "I: poly pressure leaked into another timbre")


def scenario_J():
    t = Timbre()
    t.noteOn(60)
    for v in (0, 127, 1, 126, 64, 0, 127):
        t.setMatrixPolyAfterTouch(60, INV127 * v)
        value = t.at(60)
        check(value is not None and 0.0 <= value <= 1.0,
              "J: pressure %d produced an out of range source value %r" % (v, value))
    t.setMatrixPolyAfterTouch(60, INV127 * 0)
    check(near(t.at(60), 0.0), "J: pressure 0 did not reach the voice")
    t.setMatrixPolyAfterTouch(60, INV127 * 127)
    check(near(t.at(60), 1.0), "J: pressure 127 did not normalise to 1.0")


def scenario_repeated_note():
    t = Timbre()
    t.setMatrixChannelAfterTouch(INV127 * 40)
    t.noteOn(60)
    t.setMatrixPolyAfterTouch(60, INV127 * 127)
    t.noteOn(60)                       # same note again, same voice
    check(near(t.at(60), INV127 * 40),
          "repeated note did not restart from the channel baseline")


def scenario_duplicate_note_outside_unison():
    """R1 regression.

    preenNoteOn() skips a voice that is isNewNotePending() (Timbre.cpp:646) and can then
    allocate a second voice for the same midi note. Both are isPlaying() and both report
    that note, outside unison. Poly pressure carries no voice instance identity, so both
    must receive it.
    """
    t = Timbre(numberOfVoice=4, unison=False)
    t.setMatrixChannelAfterTouch(INV127 * 10)

    # voice 0 is finishing note 60 and is pending a new note, voice 1 was allocated for
    # the very same note 60 because preenNoteOn() skipped voice 0.
    t.voices[0].playing = True
    t.voices[0].note = 60
    t.voices[0].newNotePending = True
    t.voices[1].playing = True
    t.voices[1].note = 60

    matching = [k for k in range(t.numberOfVoice)
                if t.voices[k].isPlaying() and t.voices[k].getNote() == 60]
    check(len(matching) == 2,
          "regression setup broken: expected two active voices on note 60, got %d"
          % len(matching))

    t.setMatrixPolyAfterTouch(60, INV127 * 111)
    for k in matching:
        check(near(t.voices[k].aftertouch, INV127 * 111),
              "duplicate note outside unison: voice %d did not receive poly pressure" % k)

    # and nothing else moved
    for k in (2, 3):
        check(near(t.voices[k].aftertouch, INV127 * 10),
              "duplicate note outside unison: voice %d was written although it does not "
              "play the note" % k)


def scenario_three_voices_same_note():
    """Same rule with three matching voices, still outside unison."""
    t = Timbre(numberOfVoice=4, unison=False)
    for k in range(3):
        t.voices[k].playing = True
        t.voices[k].note = 64
    t.setMatrixPolyAfterTouch(64, INV127 * 77)
    for k in range(3):
        check(near(t.voices[k].aftertouch, INV127 * 77),
              "three matching voices: voice %d missed the poly pressure" % k)
    check(near(t.voices[3].aftertouch, 0.0),
          "three matching voices: a non matching voice was written")


def main():
    test_no_new_matrix_source()
    test_no_preset_format_change()
    test_implementation_present()
    test_no_mpe_added()
    test_editor_protocol_untouched()

    for name, fn in (("A channel aftertouch regression", scenario_A),
                     ("B independent poly chord", scenario_B),
                     ("C broadcast after poly", scenario_C),
                     ("D new voice after channel", scenario_D),
                     ("E voice reuse", scenario_E),
                     ("F preset load", scenario_F),
                     ("G unison", scenario_G),
                     ("H sustain", scenario_H),
                     ("I multitimbral", scenario_I),
                     ("J boundary values", scenario_J),
                     ("K repeated same note", scenario_repeated_note),
                     ("L duplicate note outside unison (R1)",
                      scenario_duplicate_note_outside_unison),
                     ("M three voices on the same note (R1)",
                      scenario_three_voices_same_note)):
        before = len(failures)
        fn()
        status = "ok" if len(failures) == before else "FAILED"
        print("  scenario %-34s %s" % (name, status))

    print("\n%d checks" % checks[0])
    if failures:
        print("\nFAILED:")
        for f in failures:
            print("  - " + f)
        return 1
    print("all checks passed (simulated, no firmware code executed, no hardware test)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
