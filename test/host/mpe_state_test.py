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
    check("setSource(MATRIX_SOURCE_MPESLIDE, this->lastSlide_)" in upd,
          "a recycled voice keeps the previous member channel slide; CC74 needs the "
          "same baseline restore as the pressure")
    check("float lastSlide_;" in timbre_h, "Timbre has no CC74 baseline")
    slide_setter = body(timbre, "void Timbre::setMatrixSlide")
    check("lastSlide_ = newValue" in slide_setter
          and "setMatrixSource(MATRIX_SOURCE_MPESLIDE, newValue)" in slide_setter,
          "setMatrixSlide does not both store and broadcast")
    check("setMatrixSlide(INV127 * midiEvent.value[1])" in decoder,
          "the ordinary CC74 path does not go through setMatrixSlide")
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


def test_review_findings_r1_to_r4():
    """Structural assertions for the four independent review findings."""
    voice_h = read(VOICE_H)
    decoder_h = read(DECODER_H)
    decoder = read(DECODER_CPP)

    # --- R1: the manager channel must reach the configured MPE timbre ---
    check("bool isMpeManagerChannel(uint8_t channel);" in decoder_h,
          "R1: no isMpeManagerChannel()")
    received = decoder.split("void MidiDecoder::midiEventReceived")[1].split(
        "if (timbreIndex == 0)")[0]
    check("isMpeManagerChannel(midiEvent.channel)" in received,
          "R1: the manager channel is still not recognised in the routing")
    # the last mention is the manager dispatch; the earlier one is the R6 zone gate
    manager_branch = received.split("isMpeManagerChannel(midiEvent.channel)")[-1][:200]
    check("timbres[timbreIndex++] = mpeTimbre" in manager_branch,
          "R1: the manager channel does not address the MPE timbre explicitly")
    check("timbreIndex > 0" in received,
          "R1: the ordinary channel match can still run for the manager channel")
    mgr = body(decoder, "bool MidiDecoder::isMpeManagerChannel")
    check("MIDICONFIG_MPE_MASTER" in mgr,
          "R1: the manager channel is not taken from the configuration")

    # --- R2: RPN must not reach the arp / parameter / nrpn paths ---
    check("bool mpeConsumeRpn(MidiEvent& midiEvent);" in decoder_h,
          "R2: no RPN state machine")
    check("uint8_t mpeRpnMsb[16];" in decoder_h and "uint8_t mpeRpnLsb[16];" in decoder_h,
          "R2: no per channel RPN selection state")
    check("mpeConsumeRpn(midiEvent)" in received and "return;" in received,
          "R2: RPN is not consumed in the routing")
    order = received.find("mpeConsumeRpn")
    member = received.find("mpeEventReceived(mpeTimbre, midiEvent)")
    check(0 < order < member,
          "R2: RPN must be consumed before the member channel dispatch")
    rpn = body(decoder, "bool MidiDecoder::mpeConsumeRpn")
    gmt_src = body(decoder, "int MidiDecoder::getMpeTimbre")
    for cc in ("101", "100"):
        check("case %s:" % cc in rpn, "R2: CC%s is not handled" % cc)
    check("case 6:" in rpn and "case 38:" in rpn, "R2: data entry is not handled")
    check("0x7F" in rpn,
          "R2: RPN Null is not handled, so CC6/CC38 would always be swallowed and the "
          "editor nrpn protocol would break")
    check("MIDICONFIG_MPE_MEMBERS" in rpn, "R2: RPN 6 does not set the member count")
    check("mpeRpnLsb[channel] == 6 && isMpeManagerChannel(channel)" in rpn,
          "R2: an MPE configuration message from a member channel could resize the zone")
    check("isMpeManagerChannel(midiEvent.channel)" in received
          and "isMpeMemberChannel(midiEvent.channel)" in received,
          "R6: the RPN state machine is not gated on the zone channels")
    gate = received.split("mpeConsumeRpn")[0][-400:]
    check("isMpeManagerChannel(midiEvent.channel)" in gate
          and "isMpeMemberChannel(midiEvent.channel)" in gate,
          "R6: RPN is consumed on channels outside the configured zone")

    # --- R5: pitch bend sensitivity is per channel ---
    check("uint8_t mpeBendRange[16];" in decoder_h,
          "R5: pitch bend sensitivity is not per channel")
    check("mpeBendRange[channel] = semitones" in rpn,
          "R5: RPN 0 does not set that channel's own bend range")
    check("MIDICONFIG_MPE_BEND" not in rpn,
          "R5: RPN 0 still writes the single shared bend setting, so a manager RPN 0 "
          "would become the member Glide range")
    check("96" in rpn, "R5: the RPN 0 range is not the 0..96 semitones MPE 1.1 allows")
    check("mpeBendRange[c] = MPE_MEMBER_BEND_MAX" in rpn
          and "mpeBendRange[channel] = 2" in rpn,
          "R5: an MPE configuration message does not restore the 2/48 defaults")
    check("int bendRange = mpeBendRange[channel];" in decoder,
          "R5: a member note does not use its own channel's bend range")

    # --- R8: a member bend range must be one the frequency path can render ---
    check("#define MPE_MEMBER_BEND_MAX 48" in decoder_h,
          "R8: the renderable member bend limit is not defined in the header")
    rpn_code = code_only(rpn)
    before_store = rpn_code.split("mpeBendRange[channel] = semitones")[0]
    check("!isMpeManagerChannel(channel)" in before_store
          and "semitones = MPE_MEMBER_BEND_MAX;" in before_store,
          "R8: RPN 0 does not clamp a member bend range before storing it")
    seed = code_only(body(decoder, "void MidiDecoder::mpeResetBendRanges"))
    check("MPE_MEMBER_BEND_MAX" in seed and "96" not in seed,
          "R8: the menu / config file seed is still bounded by the spec maximum "
          "instead of what the frequency path can render")
    # The clamp must live in the MPE RPN path only. The ordinary bend is the one in
    # the message switch, the one that writes MATRIX_SOURCE_PITCHBEND; the first
    # MIDI_PITCH_BEND label in the file belongs to the byte length parser.
    decoder_code = code_only(decoder)
    ordinary_bend = decoder_code.split("MATRIX_SOURCE_PITCHBEND")[0]
    ordinary_bend = ordinary_bend[ordinary_bend.rindex("case MIDI_PITCH_BEND:"):]
    check("MPE_MEMBER_BEND_MAX" not in ordinary_bend
          and "mpeBendRange" not in ordinary_bend,
          "R8: the member clamp reached the ordinary pitch bend path")
    check(decoder_code.count("MATRIX_SOURCE_PITCHBEND") == 1,
          "R8: MATRIX_SOURCE_PITCHBEND is written in more than one place now")

    # --- R7: the member count is part of the zone configuration ---
    check("mpeLastMembers" in decoder_h and "mpeLastBend" in decoder_h,
          "R7: a member count change is not detected")
    sync = body(decoder, "void MidiDecoder::mpeSyncZoneConfig")
    for field in ("mpeLastManager", "mpeLastMembers", "mpeLastBend"):
        check(field in sync, "R7: mpeSyncZoneConfig does not snapshot %s" % field)
    check("mpeForgetAllChannelState()" in sync and "mpeResetBendRanges()" in sync
          and "mpeForgetAllChannels()" in sync,
          "R7: a zone change does not drop expression, bend ranges and ownership")
    check("MIDICONFIG_MPE_MEMBERS" in gmt_src and "MIDICONFIG_MPE_BEND" in gmt_src,
          "R7: the member count and bend setting are not part of the change detection")

    # --- R3: unseen member expression must not overwrite the manager baseline ---
    check("bool mpePressureSeen[16];" in decoder_h and "bool mpeSlideSeen[16];" in decoder_h,
          "R3: no validity state for member pressure and slide")
    mpe_evt = decoder.split("void MidiDecoder::mpeEventReceived")[1].split("\nvoid ")[0]
    check("if (mpePressureSeen[channel])" in mpe_evt,
          "R3: an unseen member pressure is still applied on note on")
    check("if (mpeSlideSeen[channel])" in mpe_evt,
          "R3: an unseen member slide is still applied on note on")
    check("mpePressureSeen[channel] = true" in mpe_evt,
          "R3: receiving member pressure does not mark it seen")
    check("mpeSlideSeen[channel] = true" in mpe_evt,
          "R3: receiving member slide does not mark it seen")

    # --- R4: every reset path clears decoder AND voice MPE state ---
    forget = body(decoder, "void MidiDecoder::mpeForgetChannelState")
    for field in ("mpePressureSeen", "mpeSlideSeen"):
        check(field in forget, "R4: mpeForgetChannelState does not clear %s" % field)
    forget_all = body(decoder, "void MidiDecoder::mpeForgetAllChannelState")
    for field in ("mpePressureSeen", "mpeSlideSeen"):
        check(field in forget_all, "R4: mpeForgetAllChannelState does not clear %s" % field)
    # RPN selection is sticky in midi and must NOT be cleared by a note or a reset:
    # doing so would drop the selection between the data entry msb and lsb of an MPE
    # configuration message, and its lsb would fall through to the nrpn path.
    check("mpeRpnMsb" not in forget and "mpeRpnMsb" not in forget_all,
          "R4: a reset clears the RPN selection, which breaks a configuration message "
          "mid sequence")
    check("void afterNewParamsLoad(int timbre) { mpeForgetAllChannelState(); }" in decoder_h,
          "R4: a parameter load does not clear the decoder MPE state")
    check("void afterNewComboLoad() { mpeForgetAllChannelState(); }" in decoder_h,
          "R4: a combo load does not clear the decoder MPE state")
    cc = decoder.split("void MidiDecoder::controlChange")[1]
    for case in ("CC_ALL_NOTES_OFF", "CC_ALL_SOUND_OFF", "CC_RESET"):
        section = cc.split("case %s:" % case)[1].split("break;")[0]
        check("mpeForgetAllChannelState()" in section,
              "R4: %s does not clear the decoder MPE state" % case)
    check("mpeLastTimbre" in decoder_h and "mpeLastManager" in decoder_h,
          "R4: an MPE configuration change is not detected")
    check("mpeSyncZoneConfig()" in gmt_src,
          "R4: switching MPE off or moving the zone leaves stale expression behind")
    # the real Voice, not the simulation
    vload = voice_h.split("void afterNewParamsLoad()")[1].split("for (int j")[0]
    check("mpeFreqOffset = 0.0f" in vload,
          "R4: Voice::afterNewParamsLoad does not clear mpeFreqOffset; a preset load "
          "would leave a member bend on the voice")


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


def _member_bend_max():
    """R8: read the limit out of the real header so the simulation cannot drift.

    mpeSetPitchBend() turns a full bend into bend * range * 0.5f freqHarm units and
    Voice::nextBlock() indexes exp2_harm at 512 + freqHarm * 20 over a usable 0..1022,
    so the largest renderable range is (1022 - 512) / 20 * 2 = 51 semitones. 48 is the
    value the firmware settles on; anything larger saturates.
    """
    for line in read(DECODER_H).splitlines():
        if line.startswith("#define MPE_MEMBER_BEND_MAX"):
            return int(line.split()[2])
    check(False, "R8: MPE_MEMBER_BEND_MAX is not defined in MidiDecoder.h")
    return 48


MPE_MEMBER_BEND_MAX = _member_bend_max()


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
        self.lastSlide_ = 0.0
        # MATRIX_SOURCE_PITCHBEND, the ordinary timbre wide bend. R8 asserts that the
        # member bend clamp never reaches it.
        self.pitchBend = 0.0
        self.mpeVoiceOfChannel_ = [-1] * 16

    # -- Timbre::mpeForgetAllChannels --------------------------------------
    def mpeForgetAllChannels(self):
        self.mpeVoiceOfChannel_ = [-1] * 16

    # -- Timbre::setMatrixChannelAfterTouch --------------------------------
    def setMatrixChannelAfterTouch(self, value):
        self.lastChannelAfterTouch_ = value
        for k in range(self.numberOfVoice):
            self.voices[self.voiceNumber[k]].aftertouch = value

    # -- Timbre::setMatrixSource(MATRIX_SOURCE_MPESLIDE, ...) --------------
    def setMatrixSourceSlide(self, value):
        self.lastSlide_ = value
        for k in range(self.numberOfVoice):
            self.voices[self.voiceNumber[k]].slide = value

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
        self.voices[n].slide = self.lastSlide_
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
        self.lastSlide_ = 0.0
        self.mpeForgetAllChannels()
        for k in range(self.numberOfVoice):
            v = self.voices[self.voiceNumber[k]]
            v.aftertouch = 0.0
            v.slide = 0.0
            v.mpeFreqOffset = 0.0

    def voiceOfChannel(self, channel):
        return self.mpeVoiceOfChannel_[channel]


class Decoder(object):
    """Transcription of the MidiDecoder routing, MPE and the ordinary path it feeds."""

    def __init__(self, timbres, mpeInst=1, master=0, members=15, bendRange=48,
                 timbreChannel=None, globalChannel=0, currentChannel=0):
        self.timbres = timbres
        self.mpeInst = mpeInst          # 0 = off, else timbre number 1..4
        self.master = master            # 0 based midi channel
        self.members = members
        self.bendRange = bendRange
        # MIDICONFIG_CHANNELn, 1 based, 0 == "All"
        self.timbreChannel = timbreChannel or [1, 2, 3, 4]
        self.globalChannel = globalChannel          # MIDICONFIG_GLOBAL, 0 == none
        self.currentChannel = currentChannel        # MIDICONFIG_CURRENT_INSTRUMENT
        self.currentTimbre = 0
        self.mpePressure = [0.0] * 16
        self.mpeSlide = [0.0] * 16
        self.mpeBend = [0.0] * 16
        self.mpePressureSeen = [False] * 16
        self.mpeSlideSeen = [False] * 16
        self.mpeRpnMsb = [0x7F] * 16
        self.mpeRpnLsb = [0x7F] * 16
        self.mpeBendRange = [bendRange] * 16
        self.mpeLastTimbre = -1
        self.mpeLastManager = -1
        self.mpeLastMembers = -1
        self.mpeLastBend = -1
        # observation only, so the tests can see where an ordinary message landed
        self.ordinaryTimbres = []
        self.nrpnDataEntry = []         # CC6/CC38 that reached the nrpn path
        self.arpTouched = []            # CC100/CC101 that reached the arp mapping

    # -- MidiDecoder::getMpeTimbre, including the R4 configuration change check --
    def getMpeTimbre(self):
        timbre = -1 if self.mpeInst == 0 else self.mpeInst - 1
        if (timbre != self.mpeLastTimbre or self.master != self.mpeLastManager
                or self.members != self.mpeLastMembers
                or self.bendRange != self.mpeLastBend):
            self.mpeLastTimbre = timbre
            self.mpeSyncZoneConfig()
        return timbre

    # -- MidiDecoder::mpeSyncZoneConfig ------------------------------------
    def mpeSyncZoneConfig(self):
        self.mpeLastManager = self.master
        self.mpeLastMembers = self.members
        self.mpeLastBend = self.bendRange
        self.mpeForgetAllChannelState()
        self.mpeResetBendRanges()
        for t in self.timbres:
            t.mpeForgetAllChannels()

    # -- MidiDecoder::mpeResetBendRanges -----------------------------------
    def mpeResetBendRanges(self):
        # R8: this seeds the MEMBER channels, so it is bounded by what is renderable
        configured = max(0, min(MPE_MEMBER_BEND_MAX, self.bendRange))
        self.mpeBendRange = [configured] * 16
        if 0 <= self.master < 16:
            self.mpeBendRange[self.master] = 2

    def isMpeManagerChannel(self, channel):
        return channel == self.master

    def isMpeMemberChannel(self, channel):
        members = max(1, min(15, self.members))
        return self.master < channel <= min(15, self.master + members)

    def mpeForgetChannelState(self, channel):
        self.mpePressure[channel] = 0.0
        self.mpeSlide[channel] = 0.0
        self.mpeBend[channel] = 0.0
        self.mpePressureSeen[channel] = False
        self.mpeSlideSeen[channel] = False

    def mpeForgetAllChannelState(self):
        for c in range(16):
            self.mpeForgetChannelState(c)

    # -- MidiDecoder::mpeConsumeRpn ----------------------------------------
    def mpeConsumeRpn(self, channel, cc, value):
        if cc == 101:
            self.mpeRpnMsb[channel] = value
            return True
        if cc == 100:
            self.mpeRpnLsb[channel] = value
            return True
        if cc not in (6, 38):
            return False
        if self.mpeRpnMsb[channel] == 0x7F and self.mpeRpnLsb[channel] == 0x7F:
            return False                       # RPN Null: ordinary data entry
        if cc == 38:
            return True
        if self.mpeRpnMsb[channel] == 0:
            if self.mpeRpnLsb[channel] == 0:
                # R5: per channel, manager and member ranges are distinct
                semitones = min(96, value)
                # R8: a member range must be one the frequency path can render
                if not self.isMpeManagerChannel(channel):
                    semitones = min(MPE_MEMBER_BEND_MAX, semitones)
                self.mpeBendRange[channel] = semitones
            elif self.mpeRpnLsb[channel] == 6 and self.isMpeManagerChannel(channel):
                if value >= 1:
                    self.members = min(15, value)
                    self.mpeSyncZoneConfig()
                    # MPE 1.1: MCM resets manager to 2 and every member to 48
                    self.mpeBendRange[channel] = 2
                    for c in range(16):
                        if self.isMpeMemberChannel(c):
                            self.mpeBendRange[c] = MPE_MEMBER_BEND_MAX
        return True

    # -- MidiDecoder::midiEventReceived ------------------------------------
    def midiEventReceived(self, kind, channel, d1, d2=0):
        timbres = []
        mpe = self.getMpeTimbre()
        if mpe >= 0:
            # R6: only channels of the configured zone feed the RPN state machine
            inZone = self.isMpeManagerChannel(channel) or self.isMpeMemberChannel(channel)
            if kind == "control_change" and inZone and self.mpeConsumeRpn(channel, d1, d2):
                return
            if self.isMpeMemberChannel(channel):
                self.mpeEventReceived(mpe, kind, channel, d1, d2)
                return
            if self.isMpeManagerChannel(channel):
                timbres.append(mpe)
        if not timbres:
            if self.globalChannel and channel == self.globalChannel - 1:
                timbres = [0, 1, 2, 3]
            elif self.currentChannel and channel == self.currentChannel - 1:
                timbres = [self.currentTimbre]
            else:
                for t in range(len(self.timbres)):
                    ch = self.timbreChannel[t]
                    if ch == 0 or ch - 1 == channel:
                        timbres.append(t)
        if not timbres:
            return
        self.ordinaryTimbres.append((kind, channel, d1, d2, tuple(timbres)))
        self.ordinaryHandle(timbres, kind, channel, d1, d2)

    # -- the ordinary message switch, only what these tests observe ---------
    def ordinaryHandle(self, timbres, kind, channel, d1, d2):
        for t in timbres:
            timbre = self.timbres[t]
            if kind == "aftertouch":
                timbre.setMatrixChannelAfterTouch(INV127 * d1)
            elif kind == "pitchwheel":
                # setMatrixSource(MATRIX_SOURCE_PITCHBEND, pb / 8192.0f) - no semitone
                # range anywhere on this path, the matrix row multiplier is the range.
                timbre.pitchBend = d1
            elif kind == "note_on" and d2 > 0:
                timbre.preenNoteOn(d1, d2)
            elif kind == "note_off" or (kind == "note_on" and d2 == 0):
                timbre.preenNoteOff(d1)
            elif kind == "control_change":
                if d1 == 74:
                    timbre.setMatrixSourceSlide(INV127 * d2)
                elif d1 == 64:
                    timbre.holdPedal = d2 >= 64
                elif d1 in (120, 123, 127):
                    timbre.mpeForgetAllChannels()
                    if self.getMpeTimbre() == t:
                        self.mpeForgetAllChannelState()
                elif d1 in (100, 101):
                    self.arpTouched.append((d1, d2))
                elif d1 in (6, 38):
                    self.nrpnDataEntry.append((d1, d2))

    # -- MidiDecoder::mpeEventReceived -------------------------------------
    def mpeEventReceived(self, timbre, kind, channel, d1, d2):
        t = self.timbres[timbre]
        if kind == "note_off" or (kind == "note_on" and d2 == 0):
            t.mpeNoteOff(channel, d1)
            self.mpeForgetChannelState(channel)
        elif kind == "note_on":
            t.mpeNoteOn(channel, d1, d2)
            if self.mpePressureSeen[channel]:
                t.mpeSetMatrixSource(channel, "aftertouch", self.mpePressure[channel])
            if self.mpeSlideSeen[channel]:
                t.mpeSetMatrixSource(channel, "slide", self.mpeSlide[channel])
            t.mpeSetPitchBend(channel, self.mpeBend[channel], self.mpeBendRange[channel])
        elif kind == "aftertouch":
            self.mpePressure[channel] = INV127 * d1
            self.mpePressureSeen[channel] = True
            t.mpeSetMatrixSource(channel, "aftertouch", self.mpePressure[channel])
        elif kind == "pitchwheel":
            self.mpeBend[channel] = d1
            t.mpeSetPitchBend(channel, d1, self.mpeBendRange[channel])
        elif kind == "control_change" and d1 == 74:
            self.mpeSlide[channel] = INV127 * d2
            self.mpeSlideSeen[channel] = True
            t.mpeSetMatrixSource(channel, "slide", self.mpeSlide[channel])

    # -- SynthParamListener hooks ------------------------------------------
    def afterNewParamsLoad(self, timbre):
        self.timbres[timbre].afterNewParamsLoad()
        self.mpeForgetAllChannelState()


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
    check(not d.isMpeMemberChannel(0), "I: the master channel was treated as a member")
    check(d.isMpeManagerChannel(0), "I: channel 1 is not recognised as the manager")
    # the manager channel now addresses the MPE timbre explicitly and then runs the
    # ordinary switch: broadcast plus baseline, i.e. the reviewed PolyAT behaviour
    d.midiEventReceived("aftertouch", 0, 70)
    check(near(t.lastChannelAfterTouch_, INV127 * 70),
          "I: manager pressure did not set the timbre baseline")
    for k in range(t.numberOfVoice):
        check(near(t.voices[k].aftertouch, INV127 * 70),
              "I: manager pressure did not broadcast to voice %d" % k)
    d.midiEventReceived("control_change", 0, 74, 100)
    for k in range(t.numberOfVoice):
        check(near(t.voices[k].slide, INV127 * 100),
              "I: manager CC74 did not broadcast to voice %d" % k)
    d.midiEventReceived("control_change", 0, 64, 127)
    check(t.holdPedal, "I: manager CC64 did not reach the sustain pedal")
    d.midiEventReceived("control_change", 0, 64, 0)


# --- J ---------------------------------------------------------------------
def scenario_J():
    # four timbres on channels 1..4, MPE off: the ordinary routing must be untouched
    ts = [Timbre(3) for _ in range(4)]
    d = Decoder(ts, mpeInst=0, timbreChannel=[1, 2, 3, 4])
    for ch in (0, 1, 2, 3):
        d.midiEventReceived("note_on", ch, 60, 100)
        d.midiEventReceived("aftertouch", ch, 100)
        d.midiEventReceived("control_change", ch, 74, 100)
    check(len(d.ordinaryTimbres) == 12,
          "J: with MPE off every message must reach the ordinary routing, got %d"
          % len(d.ordinaryTimbres))
    for index, (kind, ch, d1, d2, targets) in enumerate(d.ordinaryTimbres):
        check(targets == (ch,),
              "J: channel %d was routed to %s instead of timbre %d" % (ch, targets, ch))
    for t in ts:
        check(all(v == -1 for v in t.mpeVoiceOfChannel_),
              "J: MPE state was touched although MPE is off")
    # a channel nobody listens to is still dropped
    before = len(d.ordinaryTimbres)
    d.midiEventReceived("aftertouch", 9, 100)
    check(len(d.ordinaryTimbres) == before,
          "J: an unmatched channel was routed somewhere")


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
    check(not d.ordinaryTimbres,
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


# --- R1 --------------------------------------------------------------------
def scenario_R1():
    """Manager channel must reach the configured MPE timbre, not the one that happens
    to be configured on that midi channel."""
    t1 = Timbre(4)          # ordinary timbre, midi channel 1
    t2 = Timbre(4)          # MPE timbre
    d = Decoder([t1, t2], mpeInst=2, master=0, members=15,
                timbreChannel=[1, 2, 3, 4])
    d.midiEventReceived("aftertouch", 0, 70)
    check(near(t2.lastChannelAfterTouch_, INV127 * 70),
          "R1: manager pressure did not reach the MPE timbre")
    check(near(t1.lastChannelAfterTouch_, 0.0),
          "R1: manager pressure reached timbre 1, which is not the MPE timbre")
    d.midiEventReceived("control_change", 0, 74, 100)
    check(near(t2.lastSlide_, INV127 * 100), "R1: manager CC74 missed the MPE timbre")
    check(near(t1.lastSlide_, 0.0), "R1: manager CC74 reached the wrong timbre")
    d.midiEventReceived("control_change", 0, 64, 127)
    check(t2.holdPedal, "R1: manager sustain missed the MPE timbre")
    check(not t1.holdPedal, "R1: manager sustain reached the wrong timbre")
    check(all(targets == (1,) for _, _, _, _, targets in d.ordinaryTimbres),
          "R1: a manager message was routed to a timbre other than the MPE one")
    # the global channel must not pull the manager channel back to everyone
    t1b, t2b = Timbre(4), Timbre(4)
    dg = Decoder([t1b, t2b], mpeInst=2, master=0, timbreChannel=[1, 2, 3, 4],
                 globalChannel=1)
    dg.midiEventReceived("aftertouch", 0, 90)
    check(near(t1b.lastChannelAfterTouch_, 0.0),
          "R1: the global channel dragged manager pressure into another timbre")
    check(near(t2b.lastChannelAfterTouch_, INV127 * 90),
          "R1: manager pressure did not reach the MPE timbre with a global channel set")


# --- R2 --------------------------------------------------------------------
def scenario_R2():
    """An RPN sequence must not touch the arp CCs or the nrpn data entry."""
    t, d = setup(members=15, bendRange=48)

    # RPN 6, MPE configuration message: 5 member channels
    d.midiEventReceived("control_change", 0, 101, 0)
    d.midiEventReceived("control_change", 0, 100, 6)
    d.midiEventReceived("control_change", 0, 6, 5)
    d.midiEventReceived("control_change", 0, 38, 0)
    check(d.members == 5, "R2: RPN 6 did not set the member count, got %r" % d.members)
    check(not d.arpTouched,
          "R2: CC100/CC101 reached the arpeggiator mapping: %r" % d.arpTouched)
    check(not d.nrpnDataEntry,
          "R2: CC6/CC38 reached the nrpn data entry: %r" % d.nrpnDataEntry)
    check(not d.ordinaryTimbres,
          "R2: an RPN byte reached the ordinary message switch")
    check(d.isMpeMemberChannel(5) and not d.isMpeMemberChannel(6),
          "R2: the zone did not shrink to the configured member count")

    # RPN 0 on a member channel sets that member channel's bend range
    d.midiEventReceived("control_change", 1, 101, 0)
    d.midiEventReceived("control_change", 1, 100, 0)
    d.midiEventReceived("control_change", 1, 6, 24)
    check(d.mpeBendRange[1] == 24,
          "R2: member RPN 0 did not set that channel's bend range, got %r"
          % d.mpeBendRange[1])
    d.midiEventReceived("note_on", 1, 60, 100)
    d.midiEventReceived("pitchwheel", 1, 1.0)
    v = voice_of(d, 1)
    check(near(t.voices[v].mpeFreqOffset, 12.0),
          "R2: the new bend range is not used, 24 semitones should give 12.0 units")

    # RPN Null: data entry must go back to the nrpn path, or the editor breaks
    d.midiEventReceived("control_change", 0, 101, 127)
    d.midiEventReceived("control_change", 0, 100, 127)
    d.midiEventReceived("control_change", 0, 6, 42)
    d.midiEventReceived("control_change", 0, 38, 7)
    check(d.nrpnDataEntry == [(6, 42), (38, 7)],
          "R2: after RPN Null the data entry bytes must reach the nrpn path, got %r"
          % d.nrpnDataEntry)
    check(d.members == 5 and d.mpeBendRange[1] == 24,
          "R2: data entry after RPN Null changed an MPE setting")

    # an upper zone configuration message on channel 16 must not resize the lower zone.
    # With a zone that covers channel 16 it is consumed; R6 makes the case where it is
    # OUTSIDE the zone an explicitly documented limitation, tested in scenario R6.
    d2 = Decoder([Timbre(4)], mpeInst=1, master=0, members=15)
    d2.midiEventReceived("control_change", 15, 101, 0)
    d2.midiEventReceived("control_change", 15, 100, 6)
    d2.midiEventReceived("control_change", 15, 6, 7)
    check(d2.members == 15,
          "R2: an upper zone configuration message resized the lower zone")
    check(not d2.arpTouched and not d2.nrpnDataEntry,
          "R2: an upper zone configuration message inside the zone leaked into the patch")

    # with MPE off nothing is consumed: ordinary routing is unchanged
    t3 = Timbre(4)
    d3 = Decoder([t3], mpeInst=0, timbreChannel=[1, 2, 3, 4])
    for cc, value in ((101, 0), (100, 6), (6, 5), (38, 0)):
        d3.midiEventReceived("control_change", 0, cc, value)
    check(d3.arpTouched == [(101, 0), (100, 6)],
          "R2: with MPE off CC100/101 must keep their ordinary arp meaning, got %r"
          % d3.arpTouched)
    check(d3.nrpnDataEntry == [(6, 5), (38, 0)],
          "R2: with MPE off CC6/CC38 must keep reaching the nrpn path, got %r"
          % d3.nrpnDataEntry)


# --- R3 --------------------------------------------------------------------
def scenario_R3():
    """An unseen member value must not overwrite the manager baseline."""
    t, d = setup()
    d.midiEventReceived("aftertouch", 0, 70)           # manager pressure baseline
    d.midiEventReceived("control_change", 0, 74, 90)   # manager slide baseline
    d.midiEventReceived("note_on", 1, 60, 100)         # member note, no member value yet
    v = voice_of(d, 1)
    check(near(t.voices[v].aftertouch, INV127 * 70),
          "R3: the new voice lost the manager pressure baseline, it is %r"
          % t.voices[v].aftertouch)
    check(near(t.voices[v].slide, INV127 * 90),
          "R3: the new voice lost the manager slide baseline, it is %r"
          % t.voices[v].slide)

    # an explicit member 0 must override
    d.midiEventReceived("aftertouch", 1, 0)
    check(near(t.voices[v].aftertouch, 0.0),
          "R3: an explicit member pressure of 0 did not override the baseline")
    d.midiEventReceived("control_change", 1, 74, 0)
    check(near(t.voices[v].slide, 0.0),
          "R3: an explicit member slide of 0 did not override the baseline")

    # and an explicit value above 0 still works
    d.midiEventReceived("aftertouch", 1, 127)
    check(near(t.voices[v].aftertouch, 1.0), "R3: explicit member pressure was lost")

    # once seen, a later note on that channel does start from the member value
    d.midiEventReceived("aftertouch", 2, 40)
    d.midiEventReceived("note_on", 2, 64, 100)
    v2 = voice_of(d, 2)
    check(near(t.voices[v2].aftertouch, INV127 * 40),
          "R3: expression sent before the note on was not applied")


# --- R4 --------------------------------------------------------------------
def scenario_R4():
    """Every reset path must clear decoder state and voice bend, not just ownership."""

    def loaded():
        t, d = setup()
        d.midiEventReceived("note_on", 1, 60, 100)
        d.midiEventReceived("aftertouch", 1, 127)
        d.midiEventReceived("control_change", 1, 74, 127)
        d.midiEventReceived("pitchwheel", 1, 1.0)
        return t, d

    def assert_clean(t, d, what, voicesReset=False):
        check(all(not f for f in d.mpePressureSeen), "%s: pressure validity survived" % what)
        check(all(not f for f in d.mpeSlideSeen), "%s: slide validity survived" % what)
        check(all(v == 0.0 for v in d.mpePressure), "%s: member pressure survived" % what)
        check(all(v == 0.0 for v in d.mpeSlide), "%s: member slide survived" % what)
        check(all(v == 0.0 for v in d.mpeBend), "%s: member bend survived" % what)
        check(all(v == -1 for v in t.mpeVoiceOfChannel_), "%s: ownership survived" % what)
        if voicesReset:
            # Only a parameter/preset load re-initialises the voices themselves. After
            # an all notes off the voices are releasing, and zeroing their bend there
            # would make the release tail jump in pitch; the invariant that matters is
            # enforced on the next note on, checked below.
            check(all(near(v.mpeFreqOffset, 0.0) for v in t.voices),
                  "%s: a voice kept its member bend" % what)
        d.midiEventReceived("note_on", 1, 67, 100)
        v = voice_of(d, 1)
        check(near(t.voices[v].aftertouch, t.lastChannelAfterTouch_),
              "%s: the next note inherited stale pressure" % what)
        check(near(t.voices[v].slide, t.lastSlide_),
              "%s: the next note inherited stale slide" % what)
        check(near(t.voices[v].mpeFreqOffset, 0.0),
              "%s: the next note inherited stale bend" % what)

    # CC123 all notes off, on the manager channel so it reaches the MPE timbre
    t, d = loaded()
    d.midiEventReceived("control_change", 0, 123, 0)
    assert_clean(t, d, "R4 all notes off")

    # CC120 all sound off
    t, d = loaded()
    d.midiEventReceived("control_change", 0, 120, 0)
    assert_clean(t, d, "R4 all sound off")

    # CC127 reset
    t, d = loaded()
    d.midiEventReceived("control_change", 0, 127, 0)
    assert_clean(t, d, "R4 reset")

    # parameter / preset load
    t, d = loaded()
    d.afterNewParamsLoad(0)
    assert_clean(t, d, "R4 parameter load", voicesReset=True)

    # switching MPE off and on again must not resurrect the old expression
    t, d = loaded()
    d.mpeInst = 0
    d.getMpeTimbre()
    d.mpeInst = 1
    d.getMpeTimbre()
    check(all(not f for f in d.mpePressureSeen),
          "R4: turning MPE off and on again kept the member expression")

    # moving the zone has the same effect
    t, d = loaded()
    d.master = 4
    d.getMpeTimbre()
    check(all(v == 0.0 for v in d.mpePressure),
          "R4: moving the manager channel kept the member expression")


# --- R5 --------------------------------------------------------------------
def scenario_R5():
    """MPE 1.1 keeps Manager and Member pitch bend sensitivity separate.

    The exact sequence the re-review asked for: Manager RPN 0 = 2, then Member
    RPN 0 = 48. Member Glide must use 48, not 2.
    """
    t, d = setup(bendRange=48)
    manager = 0
    member = 1

    d.midiEventReceived("control_change", manager, 101, 0)
    d.midiEventReceived("control_change", manager, 100, 0)
    d.midiEventReceived("control_change", manager, 6, 2)      # Manager RPN0 = 2
    check(d.mpeBendRange[manager] == 2,
          "R5: manager RPN 0 did not set the manager range, got %r"
          % d.mpeBendRange[manager])
    check(d.mpeBendRange[member] != 2,
          "R5: manager RPN 0 overwrote the member bend range")

    d.midiEventReceived("control_change", member, 101, 0)
    d.midiEventReceived("control_change", member, 100, 0)
    d.midiEventReceived("control_change", member, 6, 48)      # Member RPN0 = 48
    check(d.mpeBendRange[member] == 48,
          "R5: member RPN 0 did not set the member range, got %r"
          % d.mpeBendRange[member])
    check(d.mpeBendRange[manager] == 2,
          "R5: member RPN 0 overwrote the manager range")

    d.midiEventReceived("note_on", member, 60, 100)
    d.midiEventReceived("pitchwheel", member, 1.0)
    v = voice_of(d, member)
    # 48 semitones full bend == 24.0 freqHarm units; 2 semitones would be 1.0
    check(near(t.voices[v].mpeFreqOffset, 24.0),
          "R5: member Glide used %r freqHarm units; with the manager's 2 semitones it "
          "would be 1.0, with the member's 48 it must be 24.0"
          % t.voices[v].mpeFreqOffset)

    # each member channel keeps its own range
    d.midiEventReceived("control_change", 2, 101, 0)
    d.midiEventReceived("control_change", 2, 100, 0)
    d.midiEventReceived("control_change", 2, 6, 12)
    d.midiEventReceived("note_on", 2, 64, 100)
    d.midiEventReceived("pitchwheel", 2, 1.0)
    v2 = voice_of(d, 2)
    check(near(t.voices[v2].mpeFreqOffset, 6.0),
          "R5: a second member channel did not use its own 12 semitone range")
    check(near(t.voices[v].mpeFreqOffset, 24.0),
          "R5: setting one member's range changed another member's")

    # MPE 1.1: a configuration message restores manager 2 and every member 48
    d2 = Decoder([Timbre(6)], mpeInst=1, master=0, members=15, bendRange=12)
    d2.midiEventReceived("control_change", 0, 101, 0)
    d2.midiEventReceived("control_change", 0, 100, 6)
    d2.midiEventReceived("control_change", 0, 6, 5)
    check(d2.mpeBendRange[0] == 2,
          "R5: the configuration message did not restore the 2 semitone manager range")
    check(all(d2.mpeBendRange[c] == 48 for c in range(1, 6)),
          "R5: the configuration message did not restore the 48 semitone member range")

    # MPE 1.1 allows 0..96. R8 narrows that for MEMBER channels only, to what the
    # frequency path can render; the manager entry keeps the full spec range because
    # nothing reads it as semitones. Scenario R8 owns the member half of this.
    d3 = Decoder([Timbre(4)], mpeInst=1, master=0, members=15)
    d3.midiEventReceived("control_change", 0, 101, 0)
    d3.midiEventReceived("control_change", 0, 100, 0)
    d3.midiEventReceived("control_change", 0, 6, 96)
    check(d3.mpeBendRange[0] == 96,
          "R5/R8: the manager range must keep the full 0..96 spec range, got %r"
          % d3.mpeBendRange[0])
    check(all(d3.mpeBendRange[c] == MPE_MEMBER_BEND_MAX for c in range(1, 16)),
          "R5/R8: a manager RPN 0 of 96 changed a member range")


# --- R6 --------------------------------------------------------------------
def scenario_R6():
    """RPN must only be intercepted on channels of the configured zone.

    The exact sequence the re-review asked for: lower zone, manager 1, 4 members,
    an ordinary timbre on channel 10, CC100/CC101 on channel 10.
    """
    mpe = Timbre(4)
    other = Timbre(4)
    d = Decoder([mpe, other], mpeInst=1, master=0, members=4,
                timbreChannel=[1, 10, 3, 4])
    check(not d.isMpeMemberChannel(9) and not d.isMpeManagerChannel(9),
          "R6: channel 10 must be outside a 4 member lower zone")

    d.midiEventReceived("control_change", 9, 101, 3)
    d.midiEventReceived("control_change", 9, 100, 7)
    check(d.arpTouched == [(101, 3), (100, 7)],
          "R6: CC100/CC101 outside the zone were swallowed instead of keeping their "
          "ordinary preenfm2 meaning, got %r" % d.arpTouched)
    check(d.mpeRpnMsb[9] == 0x7F and d.mpeRpnLsb[9] == 0x7F,
          "R6: an out of zone channel entered the MPE RPN state")
    check(all(kind == "control_change" and targets == (1,)
              for kind, _, _, _, targets in d.ordinaryTimbres),
          "R6: the out of zone control changes did not reach the ordinary timbre")

    # data entry outside the zone still reaches the nrpn path
    d.midiEventReceived("control_change", 9, 6, 42)
    d.midiEventReceived("control_change", 9, 38, 7)
    check(d.nrpnDataEntry == [(6, 42), (38, 7)],
          "R6: CC6/CC38 outside the zone were swallowed, got %r" % d.nrpnDataEntry)

    # and inside the zone RPN is still consumed
    d.midiEventReceived("control_change", 0, 101, 0)
    d.midiEventReceived("control_change", 0, 100, 6)
    before = list(d.arpTouched)
    check(d.arpTouched == before and d.mpeRpnLsb[0] == 6,
          "R6: RPN on the manager channel is no longer consumed")


# --- R7 --------------------------------------------------------------------
def scenario_R7():
    """A member count change is a zone configuration change.

    The exact sequence the re-review asked for: 15 members, expression on channel
    16, shrink to 4, expand to 15, new channel 16 note.
    """
    t, d = setup(members=15)
    ch16 = 15
    d.midiEventReceived("note_on", ch16, 60, 100)
    d.midiEventReceived("aftertouch", ch16, 127)
    d.midiEventReceived("control_change", ch16, 74, 127)
    d.midiEventReceived("pitchwheel", ch16, 1.0)
    check(d.mpePressureSeen[ch16], "R7: setup failed, no expression was recorded")

    d.members = 4                      # menu change
    d.getMpeTimbre()
    check(not d.isMpeMemberChannel(ch16), "R7: channel 16 is still inside a 4 member zone")
    check(all(v == -1 for v in t.mpeVoiceOfChannel_),
          "R7: shrinking the zone kept the member channel ownership")
    check(not d.mpePressureSeen[ch16] and not d.mpeSlideSeen[ch16],
          "R7: shrinking the zone kept the member expression validity")
    check(d.mpePressure[ch16] == 0.0 and d.mpeSlide[ch16] == 0.0
          and d.mpeBend[ch16] == 0.0,
          "R7: shrinking the zone kept the member expression values")

    d.members = 15                     # expand again
    d.getMpeTimbre()
    d.midiEventReceived("note_on", ch16, 67, 100)
    v = voice_of(d, ch16)
    check(v >= 0, "R7: channel 16 got no voice after the zone was restored")
    check(near(t.voices[v].aftertouch, t.lastChannelAfterTouch_),
          "R7: the new note inherited the old pressure")
    check(near(t.voices[v].slide, t.lastSlide_), "R7: the new note inherited the old slide")
    check(near(t.voices[v].mpeFreqOffset, 0.0), "R7: the new note inherited the old bend")

    # the same through RPN 6 rather than the menu
    t2, d2 = setup(members=15)
    d2.midiEventReceived("note_on", ch16, 60, 100)
    d2.midiEventReceived("aftertouch", ch16, 127)
    d2.midiEventReceived("control_change", 0, 101, 0)
    d2.midiEventReceived("control_change", 0, 100, 6)
    d2.midiEventReceived("control_change", 0, 6, 4)
    check(d2.members == 4, "R7: RPN 6 did not resize the zone")
    check(not d2.mpePressureSeen[ch16],
          "R7: an RPN 6 resize kept the previous zone's member expression")
    check(all(v == -1 for v in t2.mpeVoiceOfChannel_),
          "R7: an RPN 6 resize kept the previous zone's ownership")

    # a bend setting change counts as a zone change too
    t3, d3 = setup(members=15)
    d3.midiEventReceived("aftertouch", 3, 100)
    d3.bendRange = 12
    d3.getMpeTimbre()
    check(not d3.mpePressureSeen[3],
          "R7: changing the bend setting did not resynchronise the zone")
    check(d3.mpeBendRange[3] == 12,
          "R7: the new bend setting was not seeded into the member ranges")


# --- R8 --------------------------------------------------------------------
def findex(freqHarmOffset):
    """Voice::nextBlock(): findex = 512 + targetFreqHarm * 20, usable 0 .. 1022."""
    return 512.0 + freqHarmOffset * 20.0


def scenario_R8():
    """A member pitch bend range must be one the frequency path can actually render.

    RPN 0 used to accept the full MPE 1.1 range of 0..96 on a member channel, but
    mpeSetPitchBend() turns a full bend into bend * range * 0.5f freqHarm units and
    Voice::nextBlock() indexes exp2_harm at 512 + freqHarm * 20, clamped to 0..1022.
    A range of 96 asks for index 1472 and silently collapses at the clamp, so the
    firmware would have stored and reported a Glide it cannot produce.
    """
    check(MPE_MEMBER_BEND_MAX == 48,
          "R8: the branch contract is a 0..48 semitone member range, header says %r"
          % MPE_MEMBER_BEND_MAX)

    # A. 48 is accepted and renders exactly, at both bend extremes, unclamped.
    t, d = setup(bendRange=48)
    member = 1
    d.midiEventReceived("control_change", member, 101, 0)
    d.midiEventReceived("control_change", member, 100, 0)
    d.midiEventReceived("control_change", member, 6, 48)
    check(d.mpeBendRange[member] == 48,
          "R8-A: 48 semitones must be stored unchanged, got %r" % d.mpeBendRange[member])
    d.midiEventReceived("note_on", member, 60, 100)
    d.midiEventReceived("pitchwheel", member, 1.0)
    v = voice_of(d, member)
    check(near(t.voices[v].mpeFreqOffset, 24.0),
          "R8-A: full positive bend over 48 semitones must be +24.0 freqHarm units, "
          "got %r" % t.voices[v].mpeFreqOffset)
    check(findex(t.voices[v].mpeFreqOffset) <= 1022.0,
          "R8-A: +48 semitones needs exp2_harm index %r, past the 1022 clamp"
          % findex(t.voices[v].mpeFreqOffset))
    d.midiEventReceived("pitchwheel", member, -1.0)
    check(near(t.voices[v].mpeFreqOffset, -24.0),
          "R8-A: full negative bend over 48 semitones must be -24.0 freqHarm units, "
          "got %r" % t.voices[v].mpeFreqOffset)
    check(findex(t.voices[v].mpeFreqOffset) >= 0.0,
          "R8-A: -48 semitones needs exp2_harm index %r, past the 0 clamp"
          % findex(t.voices[v].mpeFreqOffset))

    # B. 96 is clamped to 48. Nothing anywhere may still claim 96, and the audible
    #    result must be the honest +/-24.0 rather than a saturated near miss.
    t2, d2 = setup(bendRange=48)
    d2.midiEventReceived("control_change", member, 101, 0)
    d2.midiEventReceived("control_change", member, 100, 0)
    d2.midiEventReceived("control_change", member, 6, 96)
    check(d2.mpeBendRange[member] == MPE_MEMBER_BEND_MAX,
          "R8-B: a member RPN 0 of 96 must clamp to %d, got %r"
          % (MPE_MEMBER_BEND_MAX, d2.mpeBendRange[member]))
    d2.midiEventReceived("note_on", member, 60, 100)
    d2.midiEventReceived("pitchwheel", member, 1.0)
    v2 = voice_of(d2, member)
    check(near(t2.voices[v2].mpeFreqOffset, 24.0),
          "R8-B: after the clamp a full bend must still be +24.0, got %r"
          % t2.voices[v2].mpeFreqOffset)
    check(not near(t2.voices[v2].mpeFreqOffset, 48.0),
          "R8-B: the firmware still asked for 48.0 freqHarm units, which the "
          "exp2_harm index would silently saturate")
    for bend in (-1.0, -0.5, 0.0, 0.5, 1.0):
        d2.midiEventReceived("pitchwheel", member, bend)
        index = findex(t2.voices[v2].mpeFreqOffset)
        check(0.0 <= index <= 1022.0,
              "R8-B: bend %r reached exp2_harm index %r, outside the usable range"
              % (bend, index))

    # C. one above the limit clamps too - the boundary, not just the spec maximum
    t3, d3 = setup(bendRange=48)
    d3.midiEventReceived("control_change", member, 101, 0)
    d3.midiEventReceived("control_change", member, 100, 0)
    d3.midiEventReceived("control_change", member, 6, 49)
    check(d3.mpeBendRange[member] == 48,
          "R8-C: 49 semitones must clamp to 48, got %r" % d3.mpeBendRange[member])

    # D. a value below the limit is untouched - the clamp must not become a floor
    t4, d4 = setup(bendRange=48)
    d4.midiEventReceived("control_change", member, 101, 0)
    d4.midiEventReceived("control_change", member, 100, 0)
    d4.midiEventReceived("control_change", member, 6, 12)
    check(d4.mpeBendRange[member] == 12,
          "R8-D: 12 semitones must be kept, got %r" % d4.mpeBendRange[member])
    d4.midiEventReceived("note_on", member, 60, 100)
    d4.midiEventReceived("pitchwheel", member, 1.0)
    v4 = voice_of(d4, member)
    check(near(t4.voices[v4].mpeFreqOffset, 6.0),
          "R8-D: full bend over 12 semitones must be +6.0 freqHarm units, got %r"
          % t4.voices[v4].mpeFreqOffset)
    d4.midiEventReceived("pitchwheel", member, -1.0)
    check(near(t4.voices[v4].mpeFreqOffset, -6.0),
          "R8-D: full negative bend over 12 semitones must be -6.0 freqHarm units, "
          "got %r" % t4.voices[v4].mpeFreqOffset)

    # E. the manager keeps the documented limitation: full 0..96 bookkeeping, and it
    #    still may not reach a member range.
    t5, d5 = setup(bendRange=48)
    manager = 0
    d5.midiEventReceived("control_change", member, 101, 0)
    d5.midiEventReceived("control_change", member, 100, 0)
    d5.midiEventReceived("control_change", member, 6, 36)
    d5.midiEventReceived("control_change", manager, 101, 0)
    d5.midiEventReceived("control_change", manager, 100, 0)
    d5.midiEventReceived("control_change", manager, 6, 96)
    check(d5.mpeBendRange[manager] == 96,
          "R8-E: the manager range is bookkeeping only and keeps the 0..96 spec "
          "range, got %r" % d5.mpeBendRange[manager])
    check(d5.mpeBendRange[member] == 36,
          "R8-E: the manager RPN 0 changed a member range, got %r"
          % d5.mpeBendRange[member])
    d5.midiEventReceived("note_on", member, 60, 100)
    d5.midiEventReceived("pitchwheel", member, 1.0)
    v5 = voice_of(d5, member)
    check(near(t5.voices[v5].mpeFreqOffset, 18.0),
          "R8-E: the member voice did not use its own 36 semitone range, got %r"
          % t5.voices[v5].mpeFreqOffset)

    # F. an MPE configuration message still restores manager 2 and members 48
    t6 = Timbre(6)
    d6 = Decoder([t6], mpeInst=1, master=0, members=15, bendRange=12)
    d6.midiEventReceived("control_change", 0, 101, 0)
    d6.midiEventReceived("control_change", 0, 100, 6)
    d6.midiEventReceived("control_change", 0, 6, 5)
    check(d6.mpeBendRange[0] == 2,
          "R8-F: the configuration message did not restore the 2 semitone manager "
          "range, got %r" % d6.mpeBendRange[0])
    check(all(d6.mpeBendRange[c] == 48 for c in range(1, 6)),
          "R8-F: the configuration message did not restore the 48 semitone member "
          "range")
    d6.midiEventReceived("note_on", 1, 60, 100)
    d6.midiEventReceived("pitchwheel", 1, 1.0)
    v6 = voice_of(d6, 1)
    check(near(t6.voices[v6].mpeFreqOffset, 24.0)
          and findex(t6.voices[v6].mpeFreqOffset) <= 1022.0,
          "R8-F: the restored 48 semitone default does not render inside exp2_harm")

    # G. a hand edited preenfm2.txt cannot seed an unrenderable member range.
    #    fillMidiConfig() writes midiConfigValue without a bounds check.
    t7 = Timbre(6)
    d7 = Decoder([t7], mpeInst=1, master=0, members=15, bendRange=96)
    d7.mpeSyncZoneConfig()
    check(all(d7.mpeBendRange[c] == MPE_MEMBER_BEND_MAX for c in range(1, 16)),
          "R8-G: an out of range mpebend setting seeded a member range of %r"
          % d7.mpeBendRange[1])
    d7.midiEventReceived("note_on", 1, 60, 100)
    d7.midiEventReceived("pitchwheel", 1, 1.0)
    v7 = voice_of(d7, 1)
    check(findex(t7.voices[v7].mpeFreqOffset) <= 1022.0,
          "R8-G: the seeded range reached exp2_harm index %r"
          % findex(t7.voices[v7].mpeFreqOffset))

    # H. ordinary, non MPE pitch bend must not be touched by any of this. It goes to
    #    MATRIX_SOURCE_PITCHBEND, whose range is the preset matrix multiplier.
    t8 = Timbre(4)
    d8 = Decoder([t8], mpeInst=0, timbreChannel=[1, 2, 3, 4])
    d8.midiEventReceived("note_on", 0, 60, 100)
    d8.midiEventReceived("pitchwheel", 0, 1.0)
    check(near(t8.pitchBend, 1.0),
          "R8-H: ordinary pitch bend no longer reaches MATRIX_SOURCE_PITCHBEND "
          "unchanged, got %r" % t8.pitchBend)
    d8.midiEventReceived("pitchwheel", 0, -1.0)
    check(near(t8.pitchBend, -1.0),
          "R8-H: ordinary pitch bend was clamped, got %r" % t8.pitchBend)
    check(all(near(v.mpeFreqOffset, 0.0) for v in t8.voices),
          "R8-H: ordinary pitch bend leaked into the per voice MPE bend offset")


def main():
    test_no_new_matrix_source()
    test_polyat_contract_intact()
    test_mpe_pieces_present()
    test_review_findings_r1_to_r4()
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
            ("N boundary channels and values", scenario_N),
            ("R1 manager channel targets the MPE timbre", scenario_R1),
            ("R2 RPN cannot reach arp / nrpn / patch", scenario_R2),
            ("R3 unseen member value keeps the baseline", scenario_R3),
            ("R4 every reset path clears MPE state", scenario_R4),
            ("R5 manager and member bend ranges are distinct", scenario_R5),
            ("R6 RPN only inside the configured zone", scenario_R6),
            ("R7 member count change resynchronises the zone", scenario_R7),
            ("R8 member bend range stays renderable", scenario_R8)):
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
