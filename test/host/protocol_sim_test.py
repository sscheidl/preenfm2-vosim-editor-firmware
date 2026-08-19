#!/usr/bin/env python3
"""
Editor remote protocol - static / simulated check.

This is NOT a firmware unit test and it does NOT run any firmware code. There is no
host C++ toolchain in this project, and building one would mean shipping a second
build system next to the real firmware Makefile.

What it does instead:

  1. it reads the protocol constants out of the real src/midi/MidiDecoder.h, so the
     documentation and this simulation can never drift away from the firmware;
  2. it checks the chosen NRPN page against every address range decodeNrpn() already
     uses, i.e. it proves the collision freedom claim mechanically;
  3. it replays NRPN byte sequences through a transcription of the decode path
     (controlChange -> decodeNrpn -> editorCommandReceived -> editorStore) and checks
     the resulting status codes and side effects for the cases listed in the
     assignment.

Point 3 is a simulation of the C++ control flow, so it can only catch protocol and
control flow mistakes, never a compiler or hardware level problem. Every real
hardware test is still open.

Run:  python test/host/protocol_sim_test.py
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
HEADER = os.path.join(REPO, "src", "midi", "MidiDecoder.h")
DECODER = os.path.join(REPO, "src", "midi", "MidiDecoder.cpp")

failures = []
checks = [0]


def check(condition, message):
    checks[0] += 1
    if not condition:
        failures.append(message)


# ---------------------------------------------------------------------------
# 1. read the constants out of the real header
# ---------------------------------------------------------------------------

def read_constants():
    src = open(HEADER, encoding="utf-8", errors="replace").read()
    consts = {}

    for name, value in re.findall(r"#define\s+(EDITOR_[A-Z0-9_]+)\s+(0x[0-9a-fA-F]+|\d+)", src):
        consts[name] = int(value, 0)

    for body in re.findall(r"enum\s+EditorProtocol\w*\s*\{(.*?)\}", src, re.S):
        nxt = 0
        for line in body.split(","):
            line = line.split("//")[0].strip()
            if not line:
                continue
            m = re.match(r"(EDITOR_[A-Z0-9_]+)\s*(?:=\s*(\d+))?$", line)
            if not m:
                continue
            nxt = int(m.group(2)) if m.group(2) else nxt
            consts[m.group(1)] = nxt
            nxt += 1

    return consts


C = read_constants()

REQUIRED = [
    "EDITOR_NRPN_PAGE", "EDITOR_PROTOCOL_VERSION",
    "EDITOR_CAPABILITY_STORE", "EDITOR_CAPABILITY_POSITION_QUERY",
    "EDITOR_BANKTYPE_PREENFM_PATCH",
    "EDITOR_REQ_CAPABILITY", "EDITOR_REQ_POSITION", "EDITOR_REQ_STORE",
    "EDITOR_RSP_PROTOCOL_VERSION", "EDITOR_RSP_CAPABILITIES",
    "EDITOR_RSP_POSITION_BANKTYPE", "EDITOR_RSP_POSITION_BANK",
    "EDITOR_RSP_POSITION_PRESET", "EDITOR_RSP_POSITION_VALID",
    "EDITOR_RSP_STORE_STATUS", "EDITOR_RSP_STORE_TARGET",
    "EDITOR_STATUS_OK", "EDITOR_STATUS_BANK_NOT_FOUND",
    "EDITOR_STATUS_INVALID_TARGET", "EDITOR_STATUS_AMBIGUOUS_CHANNEL",
    "EDITOR_STATUS_STORAGE_ERROR", "EDITOR_STATUS_PROTOCOL_ERROR",
]
for name in REQUIRED:
    check(name in C, "constant %s missing from MidiDecoder.h" % name)
if failures:
    print("\n".join(failures))
    sys.exit(1)

PAGE = C["EDITOR_NRPN_PAGE"]
REQUESTS = {C["EDITOR_REQ_CAPABILITY"], C["EDITOR_REQ_POSITION"], C["EDITOR_REQ_STORE"]}
RESPONSES = {v for k, v in C.items() if k.startswith("EDITOR_RSP_")}

# banks present on the usb key, 128 presets each
NUMBEROFPREENFMBANKS = 64


# ---------------------------------------------------------------------------
# 2. collision freedom of the chosen page, against the ranges decodeNrpn() uses
# ---------------------------------------------------------------------------

def test_page_is_free():
    # decodeNrpn(): paramMSB < 2 -> synth parameters and preset name letters
    check(PAGE >= 2, "page %d overlaps the synth parameter pages 0 and 1" % PAGE)
    # decodeNrpn(): paramMSB < 4 -> step sequencer 1 and 2
    check(PAGE >= 4, "page %d overlaps the step sequencer pages 2 and 3" % PAGE)
    # decodeNrpn(): paramMSB == 127 && paramLSB == 127 -> full dump
    check(PAGE != 127, "page %d collides with the full dump command 127/127" % PAGE)

    # the editor side (Plugin/Source/PreenNrpn.h) uses parameter numbers up to the
    # second step sequencer and one ui internal number, none of them on this page
    editor_highest_parameter = 399      # PREENFM2_NRPN_STEPSEQ2_STEP16
    editor_ui_internal = 2044           # PREENFM_NRPN_PFMTYPE, not a firmware address
    page_first = PAGE * 128
    page_last = page_first + 127
    check(page_first > editor_highest_parameter,
          "page %d overlaps editor parameter numbers up to %d" % (PAGE, editor_highest_parameter))
    check(not (page_first <= editor_ui_internal <= page_last),
          "page %d overlaps the editor internal number %d" % (PAGE, editor_ui_internal))


def test_requests_and_responses_are_disjoint():
    check(not (REQUESTS & RESPONSES), "request and response LSBs overlap")
    check(min(RESPONSES) >= 64, "responses must live at LSB 64 and above")
    check(max(REQUESTS) < 64, "requests must live below LSB 64")


def test_source_really_dispatches_the_page():
    src = open(DECODER, encoding="utf-8", errors="replace").read()
    check("paramMSB == EDITOR_NRPN_PAGE" in src,
          "decodeNrpn() does not dispatch EDITOR_NRPN_PAGE")
    # the write must be reported only after savePreenFMPatch returned
    store = src[src.index("void MidiDecoder::editorStore"):]
    store = store[:store.index("\nvoid MidiDecoder::")]
    save_at = store.index("savePreenFMPatch")
    ok_at = store.index("EDITOR_STATUS_OK")
    check(save_at < ok_at, "EDITOR_STATUS_OK is sent before savePreenFMPatch() is called")
    check("loadPreenFMPatch" not in store, "editorStore() reads the target before writing it")


# ---------------------------------------------------------------------------
# 3. simulation of the decode path
# ---------------------------------------------------------------------------

FILE_OK, FILE_READ_ONLY, FILE_EMPTY = 0, 1, 2
COMMAND_SUCCESS, COMMAND_FAILED = 0, 1


class Bank(object):
    def __init__(self, file_type=FILE_OK):
        self.fileType = file_type
        self.presets = {}


class Firmware(object):
    """Transcription of the C++ control flow, not a re-implementation of the synth."""

    def __init__(self, banks=None, storage_ok=True, receives_nrpn=True):
        self.banks = banks if banks is not None else [Bank() for _ in range(3)]
        self.storage_ok = storage_ok
        self.receives = 0x02 if receives_nrpn else 0x01
        # per timbre nrpn state, mirrors struct Nrpn currentNrpn[NUMBER_OF_TIMBRES]
        self.nrpn = [{"paramMSB": 0, "paramLSB": 0, "valueMSB": 0, "valueLSB": 0}
                     for _ in range(4)]
        self.editorValueMsbSeen = [False] * 4
        self.currentEventTimbreCount = 0
        self.editorCommandDoneThisEvent = False
        # fullState
        self.preenFMBankNumber = 0
        self.preenFMPresetNumber = 0
        self.preenFMBank = None
        # timbre edit buffers
        self.params = ["timbre%d-buffer" % t for t in range(4)]
        self.sent = []
        self.full_dumps = []
        self.param_writes = []

    # -- storage ----------------------------------------------------------
    def get_file(self, index):
        if index < 0 or index >= len(self.banks):
            return Bank(FILE_EMPTY)      # PreenFMFileType::errorFile
        return self.banks[index]

    def save_preenfm_patch(self, bank, preset, params):
        if not self.storage_ok:
            return COMMAND_FAILED
        bank.presets[preset] = params
        return COMMAND_SUCCESS

    # -- midi -------------------------------------------------------------
    def send_response(self, timbre, lsb, value):
        self.sent.append((lsb, value))

    def midi_event(self, timbres, ccs):
        """One midi event dispatched to the given timbres, as midiEventReceived() does."""
        if not timbres:
            return
        self.currentEventTimbreCount = len(timbres)
        self.editorCommandDoneThisEvent = False
        for cc, value in ccs:
            for t in timbres:
                self.control_change(t, cc, value)

    def control_change(self, timbre, cc, value):
        if not (self.receives & 0x02):
            return
        n = self.nrpn[timbre]
        ready = False
        if cc == 99:
            n["paramMSB"] = value
            self.editorValueMsbSeen[timbre] = False
        elif cc == 98:
            n["paramLSB"] = value
        elif cc == 6:
            n["valueMSB"] = value
            self.editorValueMsbSeen[timbre] = True
        elif cc == 38:
            n["valueLSB"] = value
            ready = True
        if ready:
            self.decode_nrpn(timbre)

    def decode_nrpn(self, timbre):
        n = self.nrpn[timbre]
        if n["paramMSB"] < 2:
            self.param_writes.append((timbre, (n["paramMSB"] << 7) + n["paramLSB"]))
        elif n["paramMSB"] < 4:
            self.param_writes.append((timbre, "stepseq"))
        elif n["paramMSB"] == 127 and n["paramLSB"] == 127:
            self.full_dumps.append(timbre)
        elif n["paramMSB"] == PAGE:
            self.editor_command_received(timbre)

    def editor_command_received(self, timbre):
        if self.editorCommandDoneThisEvent:
            return
        self.editorCommandDoneThisEvent = True
        n = self.nrpn[timbre]
        value = (n["valueMSB"] << 7) + n["valueLSB"]
        lsb = n["paramLSB"]

        if lsb == C["EDITOR_REQ_CAPABILITY"]:
            self.send_response(timbre, C["EDITOR_RSP_PROTOCOL_VERSION"], C["EDITOR_PROTOCOL_VERSION"])
            self.send_response(timbre, C["EDITOR_RSP_CAPABILITIES"],
                               C["EDITOR_CAPABILITY_STORE"] | C["EDITOR_CAPABILITY_POSITION_QUERY"])
        elif lsb == C["EDITOR_REQ_POSITION"]:
            self.editor_send_position(timbre)
        elif lsb == C["EDITOR_REQ_STORE"]:
            if self.currentEventTimbreCount != 1:
                self.send_response(timbre, C["EDITOR_RSP_STORE_TARGET"], value)
                self.send_response(timbre, C["EDITOR_RSP_STORE_STATUS"], C["EDITOR_STATUS_AMBIGUOUS_CHANNEL"])
                return
            if not self.editorValueMsbSeen[timbre]:
                self.send_response(timbre, C["EDITOR_RSP_STORE_STATUS"], C["EDITOR_STATUS_PROTOCOL_ERROR"])
                return
            self.editor_store(timbre, value)
        else:
            if lsb < C["EDITOR_RSP_PROTOCOL_VERSION"]:
                self.send_response(timbre, C["EDITOR_RSP_STORE_STATUS"], C["EDITOR_STATUS_PROTOCOL_ERROR"])

    def editor_send_position(self, timbre):
        bank = self.preenFMBank
        valid = bank is not None and bank.fileType != FILE_EMPTY
        self.send_response(timbre, C["EDITOR_RSP_POSITION_BANKTYPE"], C["EDITOR_BANKTYPE_PREENFM_PATCH"])
        self.send_response(timbre, C["EDITOR_RSP_POSITION_BANK"], self.preenFMBankNumber)
        self.send_response(timbre, C["EDITOR_RSP_POSITION_PRESET"], self.preenFMPresetNumber)
        self.send_response(timbre, C["EDITOR_RSP_POSITION_VALID"], 1 if valid else 0)

    def editor_store(self, timbre, target):
        target_bank = target >> 7
        target_preset = target & 0x7f

        if target_bank >= NUMBEROFPREENFMBANKS or target_preset >= 128:
            self.send_response(timbre, C["EDITOR_RSP_STORE_TARGET"], target)
            self.send_response(timbre, C["EDITOR_RSP_STORE_STATUS"], C["EDITOR_STATUS_INVALID_TARGET"])
            return

        bank = self.get_file(target_bank)
        if bank is None or bank.fileType != FILE_OK:
            self.send_response(timbre, C["EDITOR_RSP_STORE_TARGET"], target)
            self.send_response(timbre, C["EDITOR_RSP_STORE_STATUS"], C["EDITOR_STATUS_BANK_NOT_FOUND"])
            return

        result = self.save_preenfm_patch(bank, target_preset, self.params[timbre])
        if result != COMMAND_SUCCESS:
            self.send_response(timbre, C["EDITOR_RSP_STORE_TARGET"], target)
            self.send_response(timbre, C["EDITOR_RSP_STORE_STATUS"], C["EDITOR_STATUS_STORAGE_ERROR"])
            return

        self.preenFMBankNumber = target_bank
        self.preenFMPresetNumber = target_preset
        self.preenFMBank = bank
        self.send_response(timbre, C["EDITOR_RSP_STORE_TARGET"], target)
        self.send_response(timbre, C["EDITOR_RSP_STORE_STATUS"], C["EDITOR_STATUS_OK"])

    # -- program change ---------------------------------------------------
    def program_change(self, timbres, cc0, cc32, program):
        if cc0 != 0:
            return                                  # combo / dx7, not handled here
        for t in timbres:
            bank = self.get_file(cc32)
            if bank.fileType != FILE_EMPTY:
                self.preenFMBankNumber = cc32
                self.preenFMPresetNumber = program
                self.preenFMBank = bank


def nrpn_sequence(lsb, value):
    return [(99, PAGE), (98, lsb), (6, (value >> 7) & 0x7f), (38, value & 0x7f)]


def status_of(fw):
    for lsb, value in fw.sent:
        if lsb == C["EDITOR_RSP_STORE_STATUS"]:
            return value
    return None


def responses(fw, lsb):
    return [v for l, v in fw.sent if l == lsb]


# --- the assignment test cases ---------------------------------------------

def test_01_capability_query():
    fw = Firmware()
    fw.midi_event([0], nrpn_sequence(C["EDITOR_REQ_CAPABILITY"], 0))
    check(responses(fw, C["EDITOR_RSP_PROTOCOL_VERSION"]) == [C["EDITOR_PROTOCOL_VERSION"]],
          "capability query did not report the protocol version")
    caps = responses(fw, C["EDITOR_RSP_CAPABILITIES"])
    check(len(caps) == 1 and caps[0] & C["EDITOR_CAPABILITY_STORE"],
          "capability query did not report store support")
    check(caps[0] & C["EDITOR_CAPABILITY_POSITION_QUERY"],
          "capability query did not report position query support")


def test_02_position_after_boot():
    fw = Firmware()
    fw.midi_event([0], nrpn_sequence(C["EDITOR_REQ_POSITION"], 0))
    check(responses(fw, C["EDITOR_RSP_POSITION_VALID"]) == [0],
          "position must be reported as unknown right after boot")
    check(responses(fw, C["EDITOR_RSP_POSITION_BANKTYPE"]) == [C["EDITOR_BANKTYPE_PREENFM_PATCH"]],
          "bank type must be the regular preenfm patch bank")


def test_03_position_after_program_change():
    fw = Firmware()
    fw.program_change([0], cc0=0, cc32=2, program=17)
    fw.midi_event([0], nrpn_sequence(C["EDITOR_REQ_POSITION"], 0))
    check(responses(fw, C["EDITOR_RSP_POSITION_BANK"]) == [2], "bank not remembered after program change")
    check(responses(fw, C["EDITOR_RSP_POSITION_PRESET"]) == [17], "preset not remembered after program change")
    check(responses(fw, C["EDITOR_RSP_POSITION_VALID"]) == [1], "position must be valid after program change")


def test_04_store_bank0_preset0():
    fw = Firmware()
    fw.midi_event([0], nrpn_sequence(C["EDITOR_REQ_STORE"], (0 << 7) | 0))
    check(status_of(fw) == C["EDITOR_STATUS_OK"], "store to bank 0 preset 0 failed")
    check(fw.banks[0].presets.get(0) == "timbre0-buffer", "wrong buffer written")
    check(responses(fw, C["EDITOR_RSP_STORE_TARGET"]) == [0], "target not echoed")
    check((fw.preenFMBankNumber, fw.preenFMPresetNumber) == (0, 0), "position not updated after store")


def test_05_store_bank127_preset127():
    fw = Firmware()
    fw.midi_event([0], nrpn_sequence(C["EDITOR_REQ_STORE"], (127 << 7) | 127))
    # only 64 banks can ever exist, so this target is out of range by construction
    check(status_of(fw) == C["EDITOR_STATUS_INVALID_TARGET"],
          "bank 127 must be rejected as an invalid target, got %s" % status_of(fw))
    check(all(not b.presets for b in fw.banks), "an invalid target still wrote something")


def test_05b_store_highest_existing_slot():
    banks = [Bank() for _ in range(NUMBEROFPREENFMBANKS)]
    fw = Firmware(banks=banks)
    fw.midi_event([0], nrpn_sequence(C["EDITOR_REQ_STORE"], ((NUMBEROFPREENFMBANKS - 1) << 7) | 127))
    check(status_of(fw) == C["EDITOR_STATUS_OK"], "highest existing slot could not be written")
    check(banks[NUMBEROFPREENFMBANKS - 1].presets.get(127) == "timbre0-buffer",
          "highest existing slot holds the wrong data")


def test_06_bank_not_present():
    fw = Firmware(banks=[Bank(), Bank()])
    fw.midi_event([0], nrpn_sequence(C["EDITOR_REQ_STORE"], (9 << 7) | 3))
    check(status_of(fw) == C["EDITOR_STATUS_BANK_NOT_FOUND"], "absent bank was not rejected")
    check(all(not b.presets for b in fw.banks), "absent bank target wrote into another bank")


def test_06b_read_only_bank():
    fw = Firmware(banks=[Bank(), Bank(FILE_READ_ONLY)])
    fw.midi_event([0], nrpn_sequence(C["EDITOR_REQ_STORE"], (1 << 7) | 5))
    check(status_of(fw) == C["EDITOR_STATUS_BANK_NOT_FOUND"], "read only bank was not rejected")
    check(not fw.banks[1].presets, "read only bank was written")


def test_07_ambiguous_channel():
    for timbres in ([0, 1, 2, 3], [0, 1]):
        fw = Firmware()
        fw.midi_event(timbres, nrpn_sequence(C["EDITOR_REQ_STORE"], (0 << 7) | 4))
        check(status_of(fw) == C["EDITOR_STATUS_AMBIGUOUS_CHANNEL"],
              "store on %d timbres was not rejected" % len(timbres))
        check(not fw.banks[0].presets, "ambiguous store still wrote a patch")
        check(len(responses(fw, C["EDITOR_RSP_STORE_STATUS"])) == 1,
              "ambiguous store answered more than once")


def test_08_incomplete_sequence():
    # 99 / 98 / 38 without the value msb
    fw = Firmware()
    fw.midi_event([0], [(99, PAGE), (98, C["EDITOR_REQ_STORE"]), (38, 4)])
    check(status_of(fw) == C["EDITOR_STATUS_PROTOCOL_ERROR"],
          "an incomplete nrpn sequence was accepted")
    check(not fw.banks[0].presets, "an incomplete sequence wrote a patch")

    # a stale value msb from an earlier nrpn must not leak into the target
    fw = Firmware()
    fw.midi_event([0], nrpn_sequence(C["EDITOR_REQ_CAPABILITY"], (3 << 7) | 0))
    fw.sent = []
    fw.midi_event([0], [(99, PAGE), (98, C["EDITOR_REQ_STORE"]), (38, 4)])
    check(status_of(fw) == C["EDITOR_STATUS_PROTOCOL_ERROR"],
          "a stale value msb was used as a store target")
    check(all(not b.presets for b in fw.banks), "a stale value msb wrote a patch")

    # 99 / 98 only, nothing may happen at all
    fw = Firmware()
    fw.midi_event([0], [(99, PAGE), (98, C["EDITOR_REQ_STORE"])])
    check(fw.sent == [], "a truncated sequence produced a response")


def test_09_repeated_identical_store():
    fw = Firmware()
    target = (1 << 7) | 9
    for _ in range(3):
        fw.sent = []
        fw.midi_event([0], nrpn_sequence(C["EDITOR_REQ_STORE"], target))
        check(status_of(fw) == C["EDITOR_STATUS_OK"], "a repeated store failed")
        check(len(responses(fw, C["EDITOR_RSP_STORE_STATUS"])) == 1,
              "a repeated store answered more than once")
    check(fw.banks[1].presets == {9: "timbre0-buffer"}, "a repeated store touched other slots")


def test_10_other_timbres_untouched():
    fw = Firmware()
    fw.midi_event([2], nrpn_sequence(C["EDITOR_REQ_STORE"], (0 << 7) | 6))
    check(status_of(fw) == C["EDITOR_STATUS_OK"], "store on timbre 2 failed")
    check(fw.banks[0].presets == {6: "timbre2-buffer"},
          "the wrong timbre buffer was written: %r" % fw.banks[0].presets)


def test_11_full_dump_still_works():
    fw = Firmware()
    fw.midi_event([0], [(99, 127), (98, 127), (6, 0), (38, 0)])
    check(fw.full_dumps == [0], "the full dump on 127/127 regressed")


def test_12_parameter_nrpns_unchanged():
    fw = Firmware()
    # a regular parameter nrpn, page 0
    fw.midi_event([0], [(99, 0), (98, 44), (6, 0), (38, 12)])
    check(fw.param_writes == [(0, 44)], "a regular parameter nrpn regressed")
    check(fw.sent == [], "a regular parameter nrpn produced an editor response")
    # a preset name letter, page 1
    fw = Firmware()
    fw.midi_event([0], [(99, 1), (98, 100), (6, 0), (38, 65)])
    check(fw.param_writes == [(0, 228)], "a preset name letter nrpn regressed")
    # a step sequencer step, page 2
    fw = Firmware()
    fw.midi_event([0], [(99, 2), (98, 3), (6, 0), (38, 7)])
    check(fw.param_writes == [(0, "stepseq")], "a step sequencer nrpn regressed")
    check(fw.sent == [], "a step sequencer nrpn produced an editor response")


def test_13_storage_error_is_reported():
    fw = Firmware(storage_ok=False)
    fw.midi_event([0], nrpn_sequence(C["EDITOR_REQ_STORE"], (0 << 7) | 1))
    check(status_of(fw) == C["EDITOR_STATUS_STORAGE_ERROR"], "a write failure was reported as success")
    check((fw.preenFMBankNumber, fw.preenFMPresetNumber, fw.preenFMBank) == (0, 0, None),
          "a failed write still moved the remembered position")


def test_14_responses_do_not_loop():
    fw = Firmware()
    # feed every response back into the input, as midi thru would
    for lsb in sorted(RESPONSES):
        fw.midi_event([0], nrpn_sequence(lsb, 1234))
    check(fw.sent == [], "a looped back response produced another response")
    check(all(not b.presets for b in fw.banks), "a looped back response wrote a patch")


def test_15_unknown_command_is_reported():
    fw = Firmware()
    unknown = max(REQUESTS) + 1
    check(unknown < 64, "test needs an unused lsb below the response range")
    fw.midi_event([0], nrpn_sequence(unknown, 0))
    check(status_of(fw) == C["EDITOR_STATUS_PROTOCOL_ERROR"], "an unknown command was ignored")


def test_16_one_command_per_midi_event():
    # even when four timbres are addressed, a query answers exactly once
    fw = Firmware()
    fw.midi_event([0, 1, 2, 3], nrpn_sequence(C["EDITOR_REQ_CAPABILITY"], 0))
    check(len(responses(fw, C["EDITOR_RSP_PROTOCOL_VERSION"])) == 1,
          "a query on the global channel answered once per timbre")


def test_17_target_encoding_round_trip():
    for bank in range(128):
        for preset in range(128):
            target = (bank << 7) | preset
            check_ok = (target >> 7) == bank and (target & 0x7f) == preset
            if not check_ok:
                check(False, "target encoding broken for %d/%d" % (bank, preset))
                return
            msb, lsb = (target >> 7) & 0x7f, target & 0x7f
            if ((msb << 7) + lsb) != target:
                check(False, "nrpn value split broken for %d/%d" % (bank, preset))
                return
    check(True, "")


def test_18_nrpn_receive_disabled():
    fw = Firmware(receives_nrpn=False)
    fw.midi_event([0], nrpn_sequence(C["EDITOR_REQ_STORE"], 0))
    check(fw.sent == [], "the protocol answered while nrpn receive is disabled")
    check(not fw.banks[0].presets, "a patch was written while nrpn receive is disabled")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print("editor protocol simulation: %d checks in %d cases" % (checks[0], len(tests)))
    print("NRPN page %d, requests %s, responses %s"
          % (PAGE, sorted(REQUESTS), sorted(RESPONSES)))
    if failures:
        print("\nFAILED:")
        for f in failures:
            print("  - " + f)
        return 1
    print("all checks passed (simulated, no firmware code executed, no hardware test)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
