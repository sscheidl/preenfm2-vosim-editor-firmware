#!/usr/bin/env python3
"""
MPE - static / simulated check.

This is NOT a firmware unit test and it DOES NOT EXECUTE ANY FIRMWARE CODE, exactly like
polyat_state_test.py and protocol_sim_test.py next to it. There is no host C++ toolchain
in this project.

What it does instead:

  1. structural assertions against the real sources: that MPE reuses the existing
     pressure and slide matrix sources, that no matrix source, source id or preset
     structure was added, that the PolyAT contract is still in place, and that the
     pieces MPE needs are actually present (member channel -> voice map, the
     reuseSameNote allocator switch, the per voice bend offset, the exp2_harm bound,
     the cleanup hooks);
  2. a SIMULATION of the C++ control flow - a transcription of MidiDecoder MPE routing,
     Timbre::mpeNoteOn/mpeNoteOff/mpeSetMatrixSource/mpeSetPitchBend,
     Timbre::preenNoteOn/preenNoteOnUpdateMatrix and the per member channel expression
     state - replaying the event sequences of the assignment.

Point 2 can only catch state model and control flow mistakes. It cannot catch a
compiler, timing, interrupt ordering or hardware problem, and it is not evidence that
the firmware behaves this way on a PreenFM2. Every hardware test is still open, see
docs/MPE_IMPLEMENTATION_REPORT.md.

Run:  python test/host/mpe_state_test.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
COMMON_H = os.path.join(REPO, "src", "synth", "Common.h")
TIMBRE_H = os.path.join(REPO, "src", "synth", "Timbre.h")
TIMBRE_CPP = os.path.join(REPO, "src", "synth", "Timbre.cpp")
VOICE_H = os.path.join(REPO, "src", "synth", "Voice.h")
VOICE_CPP = os.path.join(REPO, "src", "synth", "Voice.cpp")
DECODER_H = os.path.join(REPO, "src", "midi", "MidiDecoder.h")
DECODER_CPP = os.path.join(REPO, "src", "midi", "MidiDecoder.cpp")
SYNTH_CPP = os.path.join(REPO, "src", "synth", "Synth.cpp")
MENU_H = os.path.join(REPO, "src", "hardware", "Menu.h")

failures = []
checks = [0]


def check(condition, message):
    checks[0] += 1
    if not condition:
        failures.append(message)


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def body(text, signature, end="\n}"):
    return text.split(signature)[1].split(end)[0]


def code_only(text):
    """Drop // comments, so a 'must not contain' assertion cannot match prose."""
    out = []
    for line in text.splitlines():
        pos = line.find("//")
        out.append(line if pos < 0 else line[:pos])
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 1. structural assertions
# ---------------------------------------------------------------------------

def test_no_new_matrix_source():
    common = read(COMMON_H)
    for forbidden in ("MATRIX_SOURCE_AFTERTOUCH_MPE", "MATRIX_SOURCE_POLY_AFTERTOUCH_MPE",
                      "MATRIX_SOURCE_PITCHBEND_MPE", "MATRIX_SOURCE_POLYPHONIC_AFTERTOUCH"):
        check(forbidden not in common, "Common.h gained %s; MPE must reuse the existing "
                                       "sources" % forbidden)
    enum_body = common.split("enum SourceEnum")[1].split("}")[0]
    enum_body = "\n".join(l for l in enum_body.splitlines()
                          if not l.strip().startswith("#"))
    names = [n.strip().split("=")[0].strip()
             for n in enum_body.split("{")[1].split(",") if n.strip()]
    names = [n for n in names if n.startswith("MATRIX_SOURCE")]
    check(names[-3:] == ["MATRIX_SOURCE_MPESLIDE", "MATRIX_SOURCE_RANDOM",
                         "MATRIX_SOURCE_MAX"],
          "SourceEnum tail changed: %s" % names[-3:])
    check(names.index("MATRIX_SOURCE_AFTERTOUCH") == 10, "AftT is no longer source id 10")
    check(names.index("MATRIX_SOURCE_PITCHBEND") == 9, "PitB is no longer source id 9")
    row = common.split("struct MatrixRowParams")[1].split("}")[0]
    check(all(f in row for f in ("source", "mul", "dest1", "dest2")),
          "struct MatrixRowParams changed")


def test_polyat_contract_intact():
    timbre = read(TIMBRE_CPP)
    decoder = read(DECODER_CPP)
    poly = body(timbre, "void Timbre::setMatrixPolyAfterTouch", "\n}\n")
    check("isUnison" not in poly and "return;" not in poly,
          "the PolyAT unconditional all-matching-voices rule regressed")
    chan = body(timbre, "void Timbre::setMatrixChannelAfterTouch")
    check("lastChannelAfterTouch_ = newValue" in chan
          and "setMatrixSource(MATRIX_SOURCE_AFTERTOUCH, newValue)" in chan,
          "channel pressure no longer stores the baseline and broadcasts")
    upd = body(timbre, "void Timbre::preenNoteOnUpdateMatrix")
    check("setSource(MATRIX_SOURCE_AFTERTOUCH, this->lastChannelAfterTouch_)" in upd,
          "the voice reuse baseline restore disappeared")
    load = body(timbre, "void Timbre::afterNewParamsLoad")
    check("lastChannelAfterTouch_ = 0.0f" in load,
          "the preset load reset of the baseline disappeared")
    dispatch = decoder.split("void MidiDecoder::midiEventReceived")[1]
    poly_case = dispatch.split("case MIDI_POLY_AFTER_TOUCH:")[1].split("break;")[0]
    check("setMatrixPolyAfterTouch" in poly_case, "ordinary poly pressure regressed")


def test_mpe_pieces_present():
    timbre_h = read(TIMBRE_H)
    timbre = read(TIMBRE_CPP)
    voice_h = read(VOICE_H)
    voice = read(VOICE_CPP)
    decoder_h = read(DECODER_H)
    decoder = read(DECODER_CPP)
    synth = read(SYNTH_CPP)
    menu_h = read(MENU_H)

    # member channel -> voice identity
    check("int8_t mpeVoiceOfChannel_[16];" in timbre_h,
          "Timbre has no member channel -> voice map")
    check("bool reuseSameNote = true" in timbre_h,
          "preenNoteOn has no reuseSameNote switch")
    note_on = body(timbre, "void Timbre::mpeNoteOn")
    check("preenNoteOn(note, velocity, false)" in note_on,
          "mpeNoteOn reuses a same-note voice; two member channels would fight over it")
    check("mpeVoiceOfChannel_[channel] = voice" in note_on,
          "mpeNoteOn does not record the association")

    # the association is dropped everywhere a voice changes hands
    upd = body(timbre, "void Timbre::preenNoteOnUpdateMatrix")
    check("setMpeFreqOffset(0.0f)" in upd,
          "a recycled voice keeps the previous member channel bend")
    check("mpeVoiceOfChannel_[c] = -1" in upd,
          "a recycled voice keeps its previous member channel owner")
    for hook, name in ((body(timbre, "void Timbre::afterNewParamsLoad"), "afterNewParamsLoad"),
                       (body(timbre, "void Timbre::setVoiceNumber"), "setVoiceNumber")):
        check("mpeForgetAllChannels()" in hook,
              "%s does not drop the member channel associations" % name)
    for fn in ("void Synth::allNoteOff", "void Synth::allSoundOff(int timbre)",
               "void Synth::allSoundOff()"):
        check("mpeForgetAllChannels" in body(synth, fn),
              "%s does not drop the member channel associations" % fn)

    # note off addresses the voice, never the note number
    note_off = body(timbre, "void Timbre::mpeNoteOff")
    check("mpeVoiceOf(channel)" in note_off,
          "mpeNoteOff does not resolve the voice from the member channel")
    check("preenNoteOff" not in note_off,
          "mpeNoteOff falls back to a note number search; under MPE several channels "
          "hold the same note")
    check("holdPedal" in note_off, "mpeNoteOff ignores the sustain pedal")

    # pressure reuses AftT and must not touch the timbre baseline
    setsrc = body(timbre, "void Timbre::mpeSetMatrixSource")
    check("lastChannelAfterTouch_" not in setsrc,
          "member channel expression must not modify the timbre baseline")
    check("matrix.setSource(source, newValue)" in setsrc,
          "member channel expression does not write the matrix source")

    # pitch bend is per voice and bypasses the matrix
    bend = body(timbre, "void Timbre::mpeSetPitchBend")
    check("setMpeFreqOffset" in bend, "member pitch bend does not use the voice offset")
    check("MATRIX_SOURCE_PITCHBEND" not in bend,
          "member pitch bend goes through the broadcast matrix source")
    check("float mpeFreqOffset;" in voice_h, "Voice has no per voice bend offset")
    check("mpeFreqOffset" in body(voice, "void Voice::init"),
          "Voice::init does not initialise the bend offset")
    freq = voice.split("float newFreqHarm =")[1].split("exp2_harm[index]")[0]
    check("mpeFreqOffset" in freq, "the bend offset is not applied to the frequency")
    check("1022.0f" in freq and "findex = 0.0f" in freq,
          "the exp2_harm index is not bounded although a second contributor feeds it")

    # decoder routing
    check("int getMpeTimbre();" in decoder_h, "MidiDecoder has no getMpeTimbre")
    check("float mpePressure[16];" in decoder_h and "float mpeSlide[16];" in decoder_h
          and "float mpeBend[16];" in decoder_h,
          "MidiDecoder has no per member channel expression state")
    received = decoder.split("void MidiDecoder::midiEventReceived")[1][:1400]
    check("isMpeMemberChannel(midiEvent.channel)" in received
          and "mpeEventReceived" in received and "return;" in received,
          "member channels are not intercepted before the ordinary routing")
    mpe_evt = decoder.split("void MidiDecoder::mpeEventReceived")[1].split("\nvoid ")[0]
    check("MATRIX_SOURCE_AFTERTOUCH" in mpe_evt,
          "member pressure does not use the shared pressure source")
    check("MATRIX_SOURCE_MPESLIDE" in mpe_evt,
          "member CC74 does not use the existing slide source")
    check("CC_MPE_SLIDE_CC74" in mpe_evt,
          "member CC74 is not recognised")
    check("controlChange" not in code_only(mpe_evt),
          "a member channel reaches the ordinary CC path and could edit the patch")
    check("mpeForgetChannelState(channel)" in mpe_evt,
          "member channel expression is not cleared when the note ends")

    # config, appended so the name keyed file stays compatible
    for key in ("MIDICONFIG_MPE_INST", "MIDICONFIG_MPE_MASTER",
                "MIDICONFIG_MPE_MEMBERS", "MIDICONFIG_MPE_BEND"):
        check(key in menu_h, "%s missing from the midi configuration" % key)
    tail = menu_h.split("MIDICONFIG_MPE_INST")[1]
    check("MIDICONFIG_SIZE" in tail and "MIDICONFIG_SYSEX" not in tail,
          "the MPE settings were not appended at the end of the enum")


def test_no_editor_or_preset_change():
    decoder_h = read(DECODER_H)
    check("#define EDITOR_PROTOCOL_VERSION 1" in decoder_h,
          "the editor protocol version changed")
    check("EDITOR_CAPABILITIES (EDITOR_CAPABILITY_STORE | EDITOR_CAPABILITY_POSITION_QUERY)"
          in decoder_h, "the editor capability bitmask changed")


# ---------------------------------------------------------------------------
# 2. control flow simulation
# ---------------------------------------------------------------------------

INV127 = 1.0 / 127.0


class Voice(object):
    def __init__(self):
        self.playing = False
        self.released = False
        self.newNotePending = False
        self.holdedByPedal = False
        self.note = 0
        self.index = 0
        self.aftertouch = 0.0     # matrix source AftT
        self.slide = 0.0          # matrix source MPESLIDE
        self.mpeFreqOffset = 0.0  # per voice, outside the matrix

    def isPlaying(self):
        return self.playing

    def getNote(self):
        return self.note

    def noteOff(self):
        self.released = True


class Timbre(object):
    """Transcription of the C++ control flow for one timbre."""

    def __init__(self, numberOfVoice=6):
        self.voices = [Voice() for _ in range(numberOfVoice)]
        self.voiceNumber = list(range(numberOfVoice)) + [-1] * (14 - numberOfVoice)
        self.numberOfVoice = numberOfVoice
        self.voiceIndex = 1
        self.holdPedal = False
        self.lastChannelAfterTouch_ = 0.0
        self.mpeVoiceOfChannel_ = [-1] * 16

    # -- Timbre::mpeForgetAllChannels --------------------------------------
    def mpeForgetAllChannels(self):
        self.mpeVoiceOfChannel_ = [-1] * 16

    # -- Timbre::setMatrixChannelAfterTouch --------------------------------
    def setMatrixChannelAfterTouch(self, value):
        self.lastChannelAfterTouch_ = value
        for k in range(self.numberOfVoice):
            self.voices[self.voiceNumber[k]].aftertouch = value

    # -- Timbre::setMatrixPolyAfterTouch -----------------------------------
    def setMatrixPolyAfterTouch(self, note, value):
        for k in range(self.numberOfVoice):
            n = self.voiceNumber[k]
            if n < 0 or not self.voices[n].isPlaying():
                continue
            if self.voices[n].getNote() == note:
                self.voices[n].aftertouch = value

    # -- Timbre::preenNoteOnUpdateMatrix -----------------------------------
    def preenNoteOnUpdateMatrix(self, n):
        self.voices[n].aftertouch = self.lastChannelAfterTouch_
        self.voices[n].mpeFreqOffset = 0.0
        for c in range(16):
            if self.mpeVoiceOfChannel_[c] == n:
                self.mpeVoiceOfChannel_[c] = -1

    # -- Timbre::preenNoteOn -----------------------------------------------
    def preenNoteOn(self, note, velocity, reuseSameNote=True):
        voiceToUse = -1
        indexMin = 2147483647
        for k in range(self.numberOfVoice):
            n = self.voiceNumber[k]
            if self.voices[n].newNotePending:
                continue
            if reuseSameNote and self.voices[n].isPlaying() \
                    and self.voices[n].getNote() == note:
                self.preenNoteOnUpdateMatrix(n)
                self._start(n, note)
                return n
            if not self.voices[n].isPlaying():
                if voiceToUse == -1:
                    voiceToUse = n
            elif self.voices[n].released:
                if self.voices[n].index < indexMin:
                    indexMin = self.voices[n].index
                    voiceToUse = n
        if voiceToUse == -1:
            for k in range(self.numberOfVoice):
                n = self.voiceNumber[k]
                if self.voices[n].index < indexMin:
                    indexMin = self.voices[n].index
                    voiceToUse = n
        if voiceToUse != -1:
            self.preenNoteOnUpdateMatrix(voiceToUse)
            self._start(voiceToUse, note)
        return voiceToUse

    def _start(self, n, note):
        v = self.voices[n]
        v.playing = True
        v.released = False
        v.holdedByPedal = False
        v.note = note
        v.index = self.voiceIndex
        self.voiceIndex += 1

    # -- Timbre::preenNoteOff (ordinary, note number based) -----------------
    def preenNoteOff(self, note):
        for k in range(self.numberOfVoice):
            n = self.voiceNumber[k]
            if not self.voices[n].isPlaying():
                continue
            if self.voices[n].getNote() == note:
                if self.holdPedal:
                    self.voices[n].holdedByPedal = True
                else:
                    self.voices[n].noteOff()
                return

    # -- Timbre::mpeVoiceOf -------------------------------------------------
    def mpeVoiceOf(self, channel):
        if channel > 15:
            return -1
        n = self.mpeVoiceOfChannel_[channel]
        if n < 0:
            return -1
        if not self.voices[n].isPlaying():
            self.mpeVoiceOfChannel_[channel] = -1
            return -1
        return n

    # -- Timbre::mpeNoteOn --------------------------------------------------
    def mpeNoteOn(self, channel, note, velocity):
        if channel > 15:
            return
        previous = self.mpeVoiceOf(channel)
        if previous >= 0:
            self.voices[previous].noteOff()
            self.mpeVoiceOfChannel_[channel] = -1
        voice = self.preenNoteOn(note, velocity, False)
        if voice < 0:
            return
        self.mpeVoiceOfChannel_[channel] = voice

    # -- Timbre::mpeNoteOff -------------------------------------------------
    def mpeNoteOff(self, channel, note):
        voice = self.mpeVoiceOf(channel)
        if voice < 0:
            return
        if self.holdPedal:
            self.voices[voice].holdedByPedal = True
        else:
            self.voices[voice].noteOff()
        self.mpeVoiceOfChannel_[channel] = -1

    # -- Timbre::mpeSetMatrixSource ----------------------------------------
    def mpeSetMatrixSource(self, channel, source, value):
        voice = self.mpeVoiceOf(channel)
        if voice < 0:
            return
        setattr(self.voices[voice], source, value)

    # -- Timbre::mpeSetPitchBend -------------------------------------------
    def mpeSetPitchBend(self, channel, bend, rangeInSemitones):
        voice = self.mpeVoiceOf(channel)
        if voice < 0:
            return
        self.voices[voice].mpeFreqOffset = bend * rangeInSemitones * 0.5

    # -- Timbre::afterNewParamsLoad ----------------------------------------
    def afterNewParamsLoad(self):
        self.lastChannelAfterTouch_ = 0.0
        self.mpeForgetAllChannels()
        for k in range(self.numberOfVoice):
            v = self.voices[self.voiceNumber[k]]
            v.aftertouch = 0.0
            v.slide = 0.0
            v.mpeFreqOffset = 0.0

    def voiceOfChannel(self, channel):
        return self.mpeVoiceOfChannel_[channel]


class Decoder(object):
    """Transcription of the MidiDecoder MPE routing for one MPE timbre."""

    def __init__(self, timbres, mpeInst=1, master=0, members=15, bendRange=48):
        self.timbres = timbres
        self.mpeInst = mpeInst          # 0 = off, else timbre number 1..4
        self.master = master            # 0 based midi channel
        self.members = members
        self.bendRange = bendRange
        self.mpePressure = [0.0] * 16
        self.mpeSlide = [0.0] * 16
        self.mpeBend = [0.0] * 16
        self.routedToOrdinary = []      # what fell through to the normal routing

    def getMpeTimbre(self):
        return -1 if self.mpeInst == 0 else self.mpeInst - 1

    def isMpeMemberChannel(self, channel):
        members = max(1, min(15, self.members))
        return self.master < channel <= min(15, self.master + members)

    def mpeForgetChannelState(self, channel):
        self.mpePressure[channel] = 0.0
        self.mpeSlide[channel] = 0.0
        self.mpeBend[channel] = 0.0

    def midiEventReceived(self, kind, channel, d1, d2=0):
        mpe = self.getMpeTimbre()
        if mpe >= 0 and self.isMpeMemberChannel(channel):
            self.mpeEventReceived(mpe, kind, channel, d1, d2)
            return
        self.routedToOrdinary.append((kind, channel, d1, d2))

    def mpeEventReceived(self, timbre, kind, channel, d1, d2):
        t = self.timbres[timbre]
        if kind == "note_off" or (kind == "note_on" and d2 == 0):
            t.mpeNoteOff(channel, d1)
            self.mpeForgetChannelState(channel)
        elif kind == "note_on":
            t.mpeNoteOn(channel, d1, d2)
            t.mpeSetMatrixSource(channel, "aftertouch", self.mpePressure[channel])
            t.mpeSetMatrixSource(channel, "slide", self.mpeSlide[channel])
            t.mpeSetPitchBend(channel, self.mpeBend[channel], self.bendRange)
        elif kind == "aftertouch":
            self.mpePressure[channel] = INV127 * d1
            t.mpeSetMatrixSource(channel, "aftertouch", self.mpePressure[channel])
        elif kind == "pitchwheel":
            self.mpeBend[channel] = d1
            t.mpeSetPitchBend(channel, d1, self.bendRange)
        elif kind == "control_change" and d1 == 74:
            self.mpeSlide[channel] = INV127 * d2
            t.mpeSetMatrixSource(channel, "slide", self.mpeSlide[channel])


def near(a, b):
    return a is not None and abs(a - b) < 1e-6


def voice_of(d, ch):
    return d.timbres[d.getMpeTimbre()].voiceOfChannel(ch)


def setup(**kw):
    t = Timbre(kw.pop("voices", 6))
    return t, Decoder([t], **kw)


# --- A ---------------------------------------------------------------------
def scenario_A():
    t, d = setup()
    d.midiEventReceived("note_on", 1, 60, 100)
    d.midiEventReceived("note_on", 2, 64, 100)
    d.midiEventReceived("aftertouch", 1, 127)
    d.midiEventReceived("aftertouch", 2, 32)
    v1, v2 = voice_of(d, 1), voice_of(d, 2)
    check(v1 >= 0 and v2 >= 0 and v1 != v2,
          "A: the two member channels did not get two distinct voices")
    check(near(t.voices[v1].aftertouch, 1.0), "A: channel 1 pressure wrong")
    check(near(t.voices[v2].aftertouch, INV127 * 32), "A: channel 2 pressure wrong")
    check(near(t.lastChannelAfterTouch_, 0.0),
          "A: member pressure modified the timbre baseline")


# --- B ---------------------------------------------------------------------
def scenario_B():
    t, d = setup()
    d.midiEventReceived("note_on", 1, 60, 100)
    d.midiEventReceived("note_on", 2, 64, 100)
    d.midiEventReceived("pitchwheel", 1, 1.0)
    d.midiEventReceived("pitchwheel", 2, -0.5)
    v1, v2 = voice_of(d, 1), voice_of(d, 2)
    check(near(t.voices[v1].mpeFreqOffset, 24.0),
          "B: full bend with a 48 semitone range must be 24.0 freqHarm units")
    check(near(t.voices[v2].mpeFreqOffset, -12.0), "B: channel 2 bend wrong")
    for k in range(t.numberOfVoice):
        if k not in (v1, v2):
            check(near(t.voices[k].mpeFreqOffset, 0.0),
                  "B: an unrelated voice was bent")


# --- C ---------------------------------------------------------------------
def scenario_C():
    t, d = setup()
    d.midiEventReceived("note_on", 1, 60, 100)
    d.midiEventReceived("note_on", 2, 64, 100)
    d.midiEventReceived("control_change", 1, 74, 127)
    d.midiEventReceived("control_change", 2, 74, 10)
    v1, v2 = voice_of(d, 1), voice_of(d, 2)
    check(near(t.voices[v1].slide, 1.0), "C: channel 1 slide wrong")
    check(near(t.voices[v2].slide, INV127 * 10), "C: channel 2 slide wrong")


# --- D ---------------------------------------------------------------------
def scenario_D():
    t, d = setup()
    d.midiEventReceived("note_on", 1, 60, 100)
    d.midiEventReceived("note_on", 2, 60, 100)      # same note number on purpose
    v1, v2 = voice_of(d, 1), voice_of(d, 2)
    check(v1 != v2, "D: the same note number on two member channels shared one voice")
    d.midiEventReceived("aftertouch", 1, 127)
    d.midiEventReceived("pitchwheel", 1, 0.5)
    d.midiEventReceived("control_change", 1, 74, 127)
    d.midiEventReceived("aftertouch", 2, 20)
    d.midiEventReceived("pitchwheel", 2, -1.0)
    d.midiEventReceived("control_change", 2, 74, 5)
    check(near(t.voices[v1].aftertouch, 1.0) and near(t.voices[v1].mpeFreqOffset, 12.0)
          and near(t.voices[v1].slide, 1.0), "D: channel 1 dimensions crossed over")
    check(near(t.voices[v2].aftertouch, INV127 * 20)
          and near(t.voices[v2].mpeFreqOffset, -24.0)
          and near(t.voices[v2].slide, INV127 * 5),
          "D: channel 2 dimensions crossed over")


# --- E ---------------------------------------------------------------------
def scenario_E():
    t, d = setup()
    d.midiEventReceived("note_on", 1, 60, 100)
    d.midiEventReceived("aftertouch", 1, 127)
    d.midiEventReceived("pitchwheel", 1, 1.0)
    d.midiEventReceived("control_change", 1, 74, 127)
    d.midiEventReceived("note_off", 1, 60)
    check(voice_of(d, 1) == -1, "E: the association survived the note off")
    check(near(d.mpePressure[1], 0.0) and near(d.mpeSlide[1], 0.0)
          and near(d.mpeBend[1], 0.0),
          "E: the member channel expression state survived the note off")
    d.midiEventReceived("note_on", 1, 67, 100)
    v = voice_of(d, 1)
    check(near(t.voices[v].aftertouch, 0.0), "E: the reused channel leaked old pressure")
    check(near(t.voices[v].mpeFreqOffset, 0.0), "E: the reused channel leaked old bend")
    check(near(t.voices[v].slide, 0.0), "E: the reused channel leaked old slide")


# --- F ---------------------------------------------------------------------
def scenario_F():
    t, d = setup(voices=2)
    d.midiEventReceived("note_on", 1, 60, 100)
    d.midiEventReceived("note_on", 2, 62, 100)
    d.midiEventReceived("aftertouch", 1, 127)
    d.midiEventReceived("pitchwheel", 1, 1.0)
    stolen = voice_of(d, 1)
    d.midiEventReceived("note_on", 3, 64, 100)      # no free voice, must steal
    v3 = voice_of(d, 3)
    check(v3 >= 0, "F: the third note got no voice at all")
    check(voice_of(d, 1) == -1 or voice_of(d, 1) != v3,
          "F: two member channels ended up owning the same voice")
    if v3 == stolen:
        check(near(t.voices[v3].aftertouch, 0.0),
              "F: the stolen voice kept the previous member pressure")
        check(near(t.voices[v3].mpeFreqOffset, 0.0),
              "F: the stolen voice kept the previous member bend")
        check(voice_of(d, 1) == -1,
              "F: the robbed member channel still claims the voice")


# --- G ---------------------------------------------------------------------
def scenario_G():
    t, d = setup()
    d.midiEventReceived("note_on", 1, 60, 100)
    v = voice_of(d, 1)
    t.holdPedal = True
    d.midiEventReceived("note_off", 1, 60)
    check(t.voices[v].holdedByPedal, "G: the pedal did not hold the voice")
    check(not t.voices[v].released, "G: the voice was released although the pedal is down")
    check(voice_of(d, 1) == -1,
          "G: the member channel still owns a voice it released")


# --- H ---------------------------------------------------------------------
def scenario_H():
    t, d = setup()
    d.midiEventReceived("note_on", 1, 60, 100)
    d.midiEventReceived("aftertouch", 1, 127)
    first = voice_of(d, 1)
    d.midiEventReceived("note_on", 1, 60, 100)   # same channel, same note, no note off
    second = voice_of(d, 1)
    check(second >= 0, "H: the repeated note got no voice")
    check(t.voices[first].released or first == second,
          "H: the previous note of that member channel was left hanging")
    check(near(t.voices[second].aftertouch, d.mpePressure[1]),
          "H: the repeated note did not start from the remembered channel expression")


# --- I ---------------------------------------------------------------------
def scenario_I():
    t, d = setup()
    # master channel 1 (index 0) is NOT a member channel, it falls through to the
    # ordinary routing, which is the zone wide behaviour
    check(not d.isMpeMemberChannel(0), "I: the master channel was treated as a member")
    d.midiEventReceived("aftertouch", 0, 70)
    check(d.routedToOrdinary and d.routedToOrdinary[-1][0] == "aftertouch",
          "I: master channel pressure did not reach the ordinary routing")
    # the ordinary path is the reviewed PolyAT behaviour: broadcast plus baseline
    d.midiEventReceived("note_on", 1, 60, 100)
    t.setMatrixChannelAfterTouch(INV127 * 70)
    check(near(t.lastChannelAfterTouch_, INV127 * 70),
          "I: master channel pressure did not set the timbre baseline")
    for k in range(t.numberOfVoice):
        check(near(t.voices[k].aftertouch, INV127 * 70),
              "I: master channel pressure did not broadcast to voice %d" % k)


# --- J ---------------------------------------------------------------------
def scenario_J():
    t, d = setup(mpeInst=0)          # MPE off
    for ch in (0, 1, 5, 15):
        d.midiEventReceived("note_on", ch, 60, 100)
        d.midiEventReceived("aftertouch", ch, 100)
        d.midiEventReceived("pitchwheel", ch, 1.0)
        d.midiEventReceived("control_change", ch, 74, 100)
    check(len(d.routedToOrdinary) == 16,
          "J: with MPE off every message must reach the ordinary routing, got %d"
          % len(d.routedToOrdinary))
    check(all(v == -1 for v in t.mpeVoiceOfChannel_),
          "J: MPE state was touched although MPE is off")


# --- K ---------------------------------------------------------------------
def scenario_K():
    t, _ = setup(mpeInst=0)
    t.setMatrixChannelAfterTouch(INV127 * 40)
    t.preenNoteOn(60, 100)
    t.preenNoteOn(64, 100)
    t.setMatrixPolyAfterTouch(60, 1.0)
    v60 = [k for k in range(t.numberOfVoice)
           if t.voices[k].playing and t.voices[k].note == 60]
    v64 = [k for k in range(t.numberOfVoice)
           if t.voices[k].playing and t.voices[k].note == 64]
    check(all(near(t.voices[k].aftertouch, 1.0) for k in v60),
          "K: ordinary poly pressure regressed")
    check(all(near(t.voices[k].aftertouch, INV127 * 40) for k in v64),
          "K: ordinary poly pressure leaked onto another note")
    t.preenNoteOn(62, 100)
    v62 = [k for k in range(t.numberOfVoice)
           if t.voices[k].playing and t.voices[k].note == 62]
    check(all(near(t.voices[k].aftertouch, INV127 * 40) for k in v62),
          "K: a new note no longer starts from the channel baseline")


# --- L ---------------------------------------------------------------------
def scenario_L():
    mpe = Timbre(4)
    other = Timbre(4)
    d = Decoder([mpe, other], mpeInst=1, master=0, members=15)
    d.midiEventReceived("note_on", 1, 60, 100)
    d.midiEventReceived("aftertouch", 1, 127)
    check(voice_of(d, 1) >= 0, "L: the MPE timbre did not take the note")
    check(all(v == -1 for v in other.mpeVoiceOfChannel_),
          "L: a member channel reached a second timbre")
    check(all(near(v.aftertouch, 0.0) for v in other.voices),
          "L: member expression leaked into another timbre")
    # a channel inside the zone never reaches the ordinary routing, so a second timbre
    # configured on channel 2 is deliberately shadowed while MPE is on
    check(not d.routedToOrdinary,
          "L: a member channel was also handed to the ordinary routing")


# --- M ---------------------------------------------------------------------
def scenario_M():
    t, d = setup()
    d.midiEventReceived("note_on", 1, 60, 100)
    d.midiEventReceived("note_on", 2, 64, 100)
    d.midiEventReceived("aftertouch", 1, 127)
    t.mpeForgetAllChannels()            # Synth::allNoteOff / allSoundOff
    check(all(v == -1 for v in t.mpeVoiceOfChannel_),
          "M: all notes off left member channel associations behind")
    d.midiEventReceived("aftertouch", 1, 64)
    check(near(t.voices[0].aftertouch, 1.0),
          "M: expression still reached a voice after the associations were dropped")
    t.afterNewParamsLoad()
    check(all(v == -1 for v in t.mpeVoiceOfChannel_)
          and near(t.lastChannelAfterTouch_, 0.0),
          "M: a parameter load did not clear the MPE and baseline state")
    check(all(near(v.mpeFreqOffset, 0.0) for v in t.voices),
          "M: a parameter load left a member bend behind")


# --- N ---------------------------------------------------------------------
def scenario_N():
    t, d = setup(master=0, members=15)
    check(not d.isMpeMemberChannel(0), "N: channel 1 must be the master, not a member")
    check(d.isMpeMemberChannel(1), "N: channel 2 must be a member")
    check(d.isMpeMemberChannel(15), "N: channel 16 must be a member")
    t2, d2 = setup(master=0, members=3)
    check(d2.isMpeMemberChannel(3) and not d2.isMpeMemberChannel(4),
          "N: the member count does not bound the zone")
    t3, d3 = setup(master=5, members=15)
    check(not d3.isMpeMemberChannel(5) and d3.isMpeMemberChannel(6)
          and d3.isMpeMemberChannel(15),
          "N: a moved master channel does not move the zone")
    check(not d3.isMpeMemberChannel(4), "N: the zone reaches below the master channel")

    # value extremes
    d.midiEventReceived("note_on", 1, 0, 1)
    v = voice_of(d, 1)
    for pressure in (0, 127):
        d.midiEventReceived("aftertouch", 1, pressure)
        check(0.0 <= t.voices[v].aftertouch <= 1.0,
              "N: pressure %d left the 0..1 range" % pressure)
    for bend in (-1.0, 0.0, 1.0):
        d.midiEventReceived("pitchwheel", 1, bend)
        offset = t.voices[v].mpeFreqOffset
        check(abs(offset) <= 24.0 + 1e-6,
              "N: bend %.1f gave freqHarm offset %r, beyond +/-48 semitones"
              % (bend, offset))
        findex = 512 + offset * 20
        check(0.0 <= findex <= 1022.0,
              "N: bend %.1f gives exp2_harm index %.1f, outside the bounded range"
              % (bend, findex))
    for slide in (0, 127):
        d.midiEventReceived("control_change", 1, 74, slide)
        check(0.0 <= t.voices[v].slide <= 1.0, "N: slide %d left the 0..1 range" % slide)


def main():
    test_no_new_matrix_source()
    test_polyat_contract_intact()
    test_mpe_pieces_present()
    test_no_editor_or_preset_change()

    for name, fn in (
            ("A two notes, independent pressure", scenario_A),
            ("B independent member pitch bend", scenario_B),
            ("C independent CC74", scenario_C),
            ("D pressure + pitch + CC74 on two notes", scenario_D),
            ("E member channel reuse", scenario_E),
            ("F voice stealing", scenario_F),
            ("G sustain", scenario_G),
            ("H repeated note on one channel", scenario_H),
            ("I master channel expression", scenario_I),
            ("J ordinary non-MPE regression", scenario_J),
            ("K normal PolyAT regression", scenario_K),
            ("L timbre isolation", scenario_L),
            ("M all notes off / reset cleanup", scenario_M),
            ("N boundary channels and values", scenario_N)):
        before = len(failures)
        fn()
        print("  scenario %-42s %s"
              % (name, "ok" if len(failures) == before else "FAILED"))

    print("\n%d checks" % checks[0])
    if failures:
        print("\nFAILED:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("all checks passed (SIMULATED control flow, no firmware code executed, "
          "no hardware test)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
