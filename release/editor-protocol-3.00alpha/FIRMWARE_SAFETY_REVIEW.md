# Firmware safety review, editor remote store, 3.00 alpha

Independent second pass over the new store data path, done after the implementation, on
the working commit `f7cdfcf444f3442be3e0a823ef25d66b316f1dbf`.

**The review below was originally static or simulated; no hardware test had been
performed when it was written.** Post-review status update, 2026-08-24: the
regular binary has since passed its initial physical-hardware smoke test and
has remained stable during continued use with PreenFM+, with no observed
firmware anomalies. This does not turn the static review into exhaustive
hardware coverage.

---

## 0. The data path under review

```
midi bytes
  -> MidiDecoder::newByte()            byte level, unchanged
  -> MidiDecoder::midiEventReceived()  builds timbres[], records the count      [NEW: 2 lines]
  -> MidiDecoder::controlChange()      nrpn state machine, gated by receives&2  [NEW: 2 lines]
  -> MidiDecoder::decodeNrpn()         dispatch on parameter MSB                [NEW: 1 branch]
  -> MidiDecoder::editorCommandReceived()                                       [NEW]
  -> MidiDecoder::editorStore()                                                 [NEW]
  -> PatchBank::savePreenFMPatch()     now returns the file system result       [CHANGED]
  -> MidiDecoder::editorSendResponse() status, after the write returned         [NEW]
```

---

## 1. Wrong bank

**Risk:** a store lands in a bank the editor did not name.

The bank index comes only from `target >> 7`, of the value of the very NRPN that carried
the store command. Nothing else feeds it. There is no default, no "current bank" fallback
and no clamping. The three ways a wrong bank could be reached are each closed:

- *index out of range.* `target >> 7` yields 0..127, but at most `NUMBEROFPREENFMBANKS`
  = 64 bank files are indexed. An explicit check rejects `>= 64` with status 2 before any
  lookup. Without it, `PatchBank::getFile()` would still return its internal `errorFile`
  with `FILE_EMPTY`, so the second gate would also have caught it — the two are redundant
  on purpose.
- *bank absent.* `getFile()` returns `errorFile` (`fileType == FILE_EMPTY`) and the store
  aborts with status 1. `errorFile.fileType = FILE_EMPTY` is set in
  `PreenFMFileType.cpp:24`, verified in source, not assumed.
- *bank not writable.* `FileType` is `{FILE_OK = 0, FILE_READ_ONLY, FILE_EMPTY}`. The check
  is `!= FILE_OK`, so a read only bank is refused as well. A check against `FILE_EMPTY`
  alone would have let a read only bank through — this was deliberately made stricter.

**Clamping check:** no `min`, `max`, `%` or `&` narrows the bank index anywhere on the
path. Confirmed by reading `editorStore()` end to end.

**Verdict: closed.** Simulated in cases 05, 05b, 06, 06b.

---

## 2. Wrong preset slot

**Risk:** a store lands in a slot the editor did not name.

`target & 0x7f` yields 0..127 by construction. Every preenfm patch bank file is 131072
bytes at `ALIGNED_PATCH_SIZE` = 1024, i.e. exactly 128 slots, so every value in that range
is a real slot. `PatchBank::isCorrectFile()` rejects any `.bnk` whose size is not 131072,
so a short file can never be indexed as a bank in the first place.

The seek is `patchNumber * ALIGNED_PATCH_SIZE`, the same expression the menu save has
always used. No new arithmetic was introduced.

The explicit `targetPreset >= 128` check is unreachable given the 7 bit mask. It is kept as
a guard so a future change to the target encoding cannot silently start writing past the
end of a bank file.

**Verdict: closed.** Simulated in cases 04, 05b, 09.

---

## 3. Wrong timbre

**Risk:** the wrong edit buffer is written, or several timbres are written at once.

This was the sharpest risk in the whole change. `midiEventReceived()` builds a list of
timbres, and then calls `controlChange()` **once per timbre in a loop**. Left alone, a
store arriving on the global channel would have run four times, each writing a different
edit buffer into the same slot, last one winning, silently.

Two independent mechanisms close this:

- `currentEventTimbreCount` records the size of that list before dispatch. `editorStore()`
  is only reached when it is exactly 1. Global channel (4 timbres), omni on several
  timbres, and several timbres sharing one channel or set to `All` are all refused with
  status 3.
- `editorCommandDoneThisEvent` is cleared once per midi event and set on the first
  handling, so even the refusal is emitted once, not four times. Without it, an ambiguous
  store would have answered four identical error statuses.

The buffer actually written is `synth->getTimbre(timbre)->getParamRaw()` for that one
timbre. No loop, no array write, no other timbre referenced anywhere in `editorStore()`.

The "current instrument" channel maps to exactly one timbre and is therefore accepted. That
is deterministic at the moment of the command, though it depends on the instrument selected
on the device. Documented in `EDITOR_PROTOCOL.md` section 7; the recommendation is a
dedicated channel per timbre.

**Verdict: closed.** Simulated in cases 07, 10, 16.

---

## 4. Incomplete NRPN sequence

**Risk:** a partial sequence writes something, or writes to a stale target.

The existing state machine acts on CC 38 and keeps `paramMSB`, `paramLSB`, `valueMSB` and
`valueLSB` as sticky per timbre state. Three separate holes had to be closed here, and two
of them were **missed by the first version of this review**. See section 12 for what went
wrong.

`editorValueMsbSeen[timbre]` now means: *the value msb is fresh and has not been consumed.*
It is

- **set** by CC 6,
- **cleared** by CC 99, which starts a new nrpn,
- **cleared** by CC 96 and CC 97,
- **cleared** after every page 4 dispatch, in `controlChange()`.

A store only executes when the flag is true, so it can only ever fire on a **CC 38 that
followed a CC 6**.

### 4.1 Missing value msb

`CC99=4, CC98=2, CC38=x` with no CC 6 would have used whatever `valueMSB` an unrelated
earlier nrpn left behind, addressing an arbitrary bank. Refused with status 5.

Naively zeroing `valueMSB` on CC 99 instead would have been worse: a missing CC 6 would
then have silently resolved to bank 0.

### 4.2 Sticky state after a completed command

After a store, `paramMSB` and `paramLSB` still hold page 4 / LSB 2. Clearing the flag only
on CC 99 left it **true** after the command, so a later lone CC 38 re-entered the store
handler with a valid looking flag and wrote a neighbouring slot, reporting success:

```
store bank 1 preset 9      -> written, status OK
then a bare CC38=10        -> ALSO written, status OK      (before the fix)
```

The flag is now cleared after every page 4 dispatch, for **every** addressed timbre. That
placement matters: it is outside `decodeNrpn()` and outside the `editorCommandDoneThisEvent`
guard, so a command rejected on an ambiguous channel still consumes the value on all four
timbres, not only on the one that emitted the error.

### 4.3 Increment and decrement

CC 96 and CC 97 set `readyToSend` without carrying a data entry byte at all. They derive the
value lsb from whatever was there before, so they could store a preset number that no
received byte ever specified:

```
CC99=0, CC98=44, CC6=0, CC38=42    an ordinary parameter, leaves valueLSB = 42
CC99=4, CC98=2,  CC6=1, CC96       a store command that never sends CC38
-> wrote bank 1 preset 43, status OK                        (before the fix)
```

Both now clear the flag, so neither can reach `editorStore()`.

### 4.4 Not over-strict

The regular nrpn idiom of keeping the selected parameter and resending only a fresh
CC 6 / CC 38 pair still stores normally. There is a dedicated test for that, so a later
tightening cannot quietly break it.

A sequence truncated before CC 38 never reaches `decodeNrpn()` at all, so nothing happens
and nothing is answered.

**Verdict: closed.** Simulated in cases 08, 09b, 09c, 09d, 09e, and asserted against the
source text so removing the invalidation from either CC 96 or CC 97 fails the test.

## 5. Collision with existing parameters

**Risk:** the new addresses reinterpret something that already has a meaning.

`decodeNrpn()` dispatches solely on `paramMSB`, with branches for `< 2`, `< 4`,
`== 127 && paramLSB == 127`, and no `else`. Page 4 was unreachable dead space in every
firmware to date. The new branch is appended after the full dump branch, so all three
pre-existing branches are matched first and are textually unchanged.

Cross-checked against the editor side, `Plugin/Source/PreenNrpn.h` (read only, never
modified): the highest firmware facing parameter is 399, `PREENFM2_NRPN_STEPSEQ2_STEP16`,
well below page 4's range of 512..639. `PREENFM_NRPN_PFMTYPE = 2044` is marked "Part of the
UI" and is not a firmware address; the protocol has no dependency on 2044..2047, as
required.

Requests occupy LSB 0..2, responses LSB 64..71. The two ranges are disjoint, so a response
looped back by midi thru or by an editor echo hits the `default` branch and is dropped
without an answer. **No midi loop is possible.** An unknown LSB below 64 is answered with
status 5; an unknown LSB at or above 64 is treated as a stray response and ignored on
purpose.

`test/host/protocol_sim_test.py` re-derives all of this from the constants in
`MidiDecoder.h` rather than from a copy, so the two cannot drift.

**Verdict: closed.** Simulated in cases 12, 14, 15, plus the page range checks.

---

## 6. Unnoticed file system error

**Risk:** a write fails and the editor is told it succeeded.

`PatchBank::savePreenFMPatch()` returned `void`, so this was structurally impossible to
detect before. It now returns the file system result. It performs two writes, the patch
itself and the zero padding to `ALIGNED_PATCH_SIZE`, and **both** must succeed — a slot
whose padding failed is not a complete patch.

`save()` returns `commandParams.commandResult`, and `COMMAND_SUCCESS = 0` is defined in
`src/usb/usbKey_usr.h:62`. This was read in source, not assumed. `editorStore()` compares
against `COMMAND_SUCCESS`, not against a bare `0`.

The API change is the minimum necessary. Both existing callers, `PatchBank::create()` and
`SynthState.cpp` `MENU_SAVE_ENTER_PRESET_NAME`, ignore the value and are unchanged in
behaviour; C++ allows discarding a return value, so they did not need to be touched, and
they were not.

Not covered: an error the file system layer itself does not report, for instance a usb key
that acknowledges a write it never committed. That is outside what firmware can detect and
is why case 15 of the assignment, reading the patch back and comparing, has to be run on
hardware.

**Verdict: closed for reported errors, open for silent media failure.** Simulated in
case 13.

---

## 7. Status sent before the write

**Risk:** success is reported and the write then fails or never happens.

`editorSendResponse(..., EDITOR_STATUS_OK)` is the last statement of `editorStore()`, after
`savePreenFMPatch()` returned and after its result was checked. Every earlier exit path
returns before reaching it. `savePreenFMPatch()` is synchronous: `save()` calls
`usbProcess()`, which blocks until the usb command completes, so there is no in flight
write when the status is emitted.

Ordering is also asserted mechanically: `test_source_really_dispatches_the_page` parses
`editorStore()` out of the source and fails if `EDITOR_STATUS_OK` appears before the
`savePreenFMPatch` call.

The remembered position `preenFMBankNumber`, `preenFMPresetNumber` and `preenFMBank` is
updated in the same block, after the result check. A failed write therefore leaves the
position exactly as it was.

`STORE_TARGET` is echoed on every outcome, so a status can never be attributed to the wrong
target.

**Verdict: closed.**

---

## 8. Unintended repeated execution

**Risk:** one command writes more than once.

Three paths were examined:

- *per timbre dispatch.* Covered by `editorCommandDoneThisEvent`, see section 3.
- *repeated identical command.* A second store to the same target simply writes the same
  data again. It is idempotent, answers once, and touches no other slot. Simulated in
  case 09.
- *sticky nrpn state.* This is where the first version of this review was **wrong**. It
  claimed the value flag "was cleared" after a store and that a stray CC 38 would therefore
  be refused. No such clearing existed. See sections 4.2, 4.3 and 12.

Two commands sent back to back must both execute: each control change is its own midi
event, so `midiEventReceived()` clears the per event guard for every message. Covered by
case 09f, which was added after the model was found to reset that guard only once per
replayed sequence.

**Verdict: closed after the fix in `f7cdfcf`.**

## 9. Full dump regression

**Risk:** NRPN 127/127 stops working or changes.

The `paramMSB == 127 && paramLSB == 127` branch is textually unchanged and is evaluated
before the new branch. `sendCurrentPatchAsNrpns()` is not modified in any way.

The new `editorSendResponse()` deliberately mirrors that function's channel selection and
its flush and busy-wait pattern, rather than inventing a second output convention.

Pre-existing behaviour worth naming, unchanged by this work: on the global channel the full
dump is also emitted once per timbre. That is upstream behaviour and was intentionally left
alone; only the new commands are made unambiguous.

A store does not trigger a dump, and a program change still does not trigger a dump.

**Verdict: no regression.** Simulated in case 11.

---

## 10. VOSIM and LFO regression

**Risk:** the VOSIM algorithms 29-32 or the LFO shapes 6-8 are affected.

The complete diff `6ed604a…f7cdfcf` touches seven files:

```
Makefile                        version strings only
src/PreenFM_init.cpp            boot screen author line only
src/filesystem/PatchBank.cpp    savePreenFMPatch return value
src/filesystem/PatchBank.h      savePreenFMPatch signature
src/midi/MidiDecoder.cpp        editor protocol
src/midi/MidiDecoder.h          editor protocol
src/synth/SynthState.cpp        3 lines, preenFMBank pointer after program change
```

`src/PreenFM_init.cpp` changes one string constant on the boot screen and nothing else. It
has no reachable path into the synthesis, storage or midi code.

Not touched: `Osc.cpp`, `Osc.h`, `Timbre.cpp`, `Voice.cpp`, `Lfo*.cpp`, `Lfo*.h`,
`Synth.cpp`, `Env.cpp`, `Matrix.cpp`, `Common.h`, or any other synthesis file. No
algorithm table, no shape enum, no oscillator or LFO code is in the diff.

The one `SynthState.cpp` change adds `fullState.preenFMBank = bank;` inside the existing
`bank->fileType != FILE_EMPTY` guard of `loadPreenFMPatchFromMidi()` case 0. It assigns a
pointer already validated on the line above, is confined to the regular patch bank case,
and cannot run for combo or DX7 banks.

The patch storage format is unchanged: `struct FlashSynthParams`,
`convertParamsToMemory()` and `convertMemoryToParams()` are all untouched, so patches
written by this firmware stay readable by 2.21 and vice versa.

**Verdict: no regression by construction.** Sound has to be confirmed on hardware.

---

## 11. Additional findings

### 11.1 The store blocks the audio loop

`savePreenFMPatch()` performs a blocking usb write. `MidiDecoder::newByte()` is called from
`MCP4922_loop()` and `CS4344_loop()` in `src/PreenFM.cpp`, which are main loop functions
called from `main()` — verified, **not** interrupt handlers. During the write,
`fillSoundBuffer()` is not called, so an audible gap is possible if notes are sounding.

This is exactly what the existing menu save already does, from the same context, so the new
command is no worse than the established path. It is not a new class of risk, but case 10
of the assignment cannot be closed without hardware.

### 11.2 The protocol depends on `Receives: NRPN`

The whole NRPN branch of `controlChange()` sits behind the NRPN receive bit. With
`Receives: None` or `Receives: CC`, the new commands are ignored and nothing is answered —
indistinguishable, from the editor's side, from an old firmware. Changing that gate would
have altered how every other NRPN behaves in existing setups, so it was left alone and
documented instead. Simulated in case 18.

### 11.3 Responses ignore the `Send:` setting

This is a deliberate deviation. A query that stays silent under `Send: None` would be
useless. The responses only ever occupy NRPN page 4, which no earlier firmware and no
current editor uses, so no existing setup can receive traffic it did not receive before.
Documented in `EDITOR_PROTOCOL.md` section 10.

### 11.4 Bank 127 can never exist

The assignment lists "store bank 127 / preset 127, if the bank exists" as a test case.
`NUMBEROFPREENFMBANKS` is 64, so banks 64..127 are not addressable at all. The command
returns status 2 rather than writing anywhere. The highest reachable slot is bank 63,
preset 127, covered by simulated case 05b.

### 11.5 Position validity is looser than store validity

`POSITION_VALID` reports 1 for any bank that is not `FILE_EMPTY`, so a read only bank counts
as a valid position. A store to that same bank is refused with status 1. This is
intentional: a read only bank is a legitimate place to *be*, just not a legitimate place to
*write*. An editor must not infer writability from `POSITION_VALID`; it should read the
store status.

---

## 12. Summary

| risk | verdict |
|---|---|
| wrong bank | closed |
| wrong preset slot | closed |
| wrong timbre | closed |
| incomplete NRPN sequence | closed |
| collision with existing parameters | closed |
| unnoticed file system error | closed for reported errors, open for silent media failure |
| status before the write | closed |
| unintended repeated execution | closed |
| full dump regression | no regression |
| VOSIM and LFO regression | no regression by construction, sound unverified |

These items were open when the static review was written: real write and read
back comparison, audio behaviour during a Store, USB-key failure modes, display
of the new version string, and the actual sound of the VOSIM algorithms and LFO
shapes. Subsequent positive hardware use covers normal operation but does not
fully close rare media-failure or exhaustive long-term cases.

**Current recommendation: treat the build as experimental alpha firmware.** The Store writes
immediately and without confirmation, by design, so a first test on any device should target a
throwaway bank on a usb key whose contents are backed up.

---

## 12. What the first version of this review got wrong

This document is a safety review, so its own failures belong in it.

The first version, published with the initial 3.00 alpha artifacts, stated in section 8
that `editorValueMsbSeen` was cleared after a store. **It was not.** The flag was only ever
cleared by CC 99. The consequence was a real defect in the shipped binary
`5fe73ed8b155da8f61eea8e91884ea3cb0e9fab064296b0f292ae144009b7737`: after a valid store, a
single stray CC 38 wrote the edit buffer into a second preset slot and reported success.

The claim was written from intent rather than from the code. Nothing in the test suite
covered the state *after* a completed command, so nothing contradicted it.

A follow up fix cleared the flag after every page 4 dispatch. Reviewing **that** fix
independently surfaced a second hole of the same class: the flag tracked CC 6 only, never
CC 38, so CC 96 and CC 97 could still store a derived preset number. Section 4.3.

Three changes were made so this class of error is harder to repeat:

1. the flag's meaning is now written down at its declaration, and the code matches it;
2. the test suite asserts against the **source text** of `controlChange()`, so removing the
   invalidation from CC 96, CC 97 or the dispatch path fails the test rather than silently
   passing;
3. every new assertion was checked by negative control — the fix was removed again to
   confirm the test actually fails without it.

Two of the three defects in this area were found by review of a previous review, not by the
tests. That is worth remembering when weighing how much the green test output is worth.
