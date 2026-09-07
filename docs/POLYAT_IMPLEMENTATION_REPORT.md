# True Polyphonic Aftertouch — implementation report

PreenFM2 firmware 3.00 alpha, implementation task 1.

Architecture: **option B, shared `AftT` source**. No new matrix source, no new preset
source id, no preset format change.

---

## 1. Verified base

| item | value |
|---|---|
| repository | `sscheidl/preenfm2-vosim-editor-firmware` |
| base branch | `feature/editor-remote-store` |
| base SHA | `ed8a866559e7207f7ed6383caaea645c16c7ef4e` |
| expected SHA | `ed8a866559e7207f7ed6383caaea645c16c7ef4e` — **matches** |
| working tree at start | clean |
| last commit touching `src/` | `f7cdfcf444f3442be3e0a823ef25d66b316f1dbf` |

`git diff --stat f7cdfcf..ed8a866` touches only `README.md`, `docs/PreenFM2_2026.png`,
`release/**` (build report, changelog, protocol doc, safety review, smoke test,
SHA256SUMS, patch, `.bin`, `.elf`, symbol maps) and `release/fw_2.21b_lfo_vosim/`.
**No firmware source changed since the known baseline**, so implementation proceeded as
instructed.

No existing 3.00 alpha or editor-protocol work was discarded or overwritten.

## 2. Feature branch

    feature/true-poly-aftertouch    created from origin/feature/editor-remote-store

## 3. Commits

| # | SHA | subject |
|---|---|---|
| 1 | `6fdc28c` | Add true polyphonic key pressure on the shared AftT source |
| 2 | branch HEAD | Add PolyAT implementation report and host state test |

## 4. Files changed

Commit 1 (firmware, +64 / −3):

| file | change |
|---|---|
| `src/synth/Timbre.h` | +2 method declarations, +1 member `lastChannelAfterTouch_` |
| `src/synth/Timbre.cpp` | constructor init, 2 new methods, note-on restore, reset on new params load |
| `src/midi/MidiDecoder.cpp` | poly pressure dispatch, channel pressure routed through the new setter |

Commit 2 (no firmware code):

| file | change |
|---|---|
| `docs/POLYAT_IMPLEMENTATION_REPORT.md` | this report |
| `test/host/polyat_state_test.py` | host state/structure test |

Nothing else is touched. In particular `src/synth/Common.h`, `src/synth/SynthState.cpp`,
`src/filesystem/**` and `src/utils/**` are byte-identical to the base.

## 5. Implementation summary

`MIDI_POLY_AFTER_TOUCH` (status `0xAn`) was already parsed as a two-data-byte message
(`MidiDecoder.cpp:171`) but the dispatch was a no-op. It now writes the **existing**
`MATRIX_SOURCE_AFTERTOUCH` source.

`AftT` is treated as the musical *pressure* dimension. The MIDI transport decides only
the scope of the write:

    Channel Pressure  (0xDn)  -> broadcast, every voice of the addressed timbre
    Poly Key Pressure (0xAn)  -> selective, the voice(s) playing that note

`Timbre` remembers the last channel pressure it received and hands it back to any voice
that starts a note, which is what keeps a recycled voice from inheriting the previous
note's polyphonic pressure.

## 6. Exact state machine

State, one per timbre (`Timbre.h:180`):

    float lastChannelAfterTouch_;        // 0.0f .. 1.0f, initialised to 0.0f

Initialised in `Timbre::Timbre()` (`Timbre.cpp:534`).

**Channel pressure** — `Timbre::setMatrixChannelAfterTouch()` (`Timbre.cpp:5600`):

    lastChannelAfterTouch_ = newValue;
    setMatrixSource(MATRIX_SOURCE_AFTERTOUCH, newValue);   // all voices of the timbre

Called from `MidiDecoder.cpp:286` with `INV127 * midiEvent.value[0]`. This replaces the
previous direct `setMatrixSource(MATRIX_SOURCE_AFTERTOUCH, ...)` call, so the invariant
lives in `Timbre` and is not duplicated in the decoder.

**Poly key pressure** — `Timbre::setMatrixPolyAfterTouch()` (`Timbre.cpp:5608`):

    for k in 0 .. numberOfVoice-1:
        n = voiceNumber[k]
        if n < 0:            continue        // slot not allocated to this timbre
        if !voices[n]->isPlaying(): continue
        if voices[n]->getNote() == note:
            voices[n]->matrix.setSource(MATRIX_SOURCE_AFTERTOUCH, newValue)
            if !isUnison: return

Called from `MidiDecoder.cpp:281` with note `value[0]` and `INV127 * value[1]`, i.e.
normalised exactly like the channel pressure. `lastChannelAfterTouch_` is deliberately
**not** modified — it stays the timbre-wide baseline.

**Note on / voice reuse** — `Timbre::preenNoteOnUpdateMatrix()` (`Timbre.cpp:764`):

    voices[voiceToUse]->matrix.setSource(MATRIX_SOURCE_AFTERTOUCH, lastChannelAfterTouch_);

**New parameter load** — `Timbre::afterNewParamsLoad()` (`Timbre.cpp:4917`):

    lastChannelAfterTouch_ = 0.0f;

Net rule: **last MIDI event wins**, with channel pressure as broadcast and poly pressure
as selective override, and every new note starting from the broadcast baseline.

## 7. Note-on path audit

`preenNoteOnUpdateMatrix()` is the single restore point. Every allocation route in
`Timbre::preenNoteOn()` passes through it, verified by reading all call sites:

| call site | case covered |
|---|---|
| `Timbre.cpp:662` | same note retriggered, voice reused (priority 1) |
| `Timbre.cpp:670` | same note retriggered, unison, every voice of the stack |
| `Timbre.cpp:711` | fresh allocation (`NEW_NOTE_FREE`), released reuse (`NEW_NOTE_RELEASE`), stolen voice (`NEW_NOTE_OLD`) |
| `Timbre.cpp:732` | unison allocation / retrigger, every voice of the stack |

In all four the restore runs **before** `Voice::noteOn()` / `noteOnWithoutPop()`, so the
value is in place before the new note sounds.

Mono is `numberOfVoice == 1` and uses the same `preenNoteOn()` path, so it is covered by
the rows above. The arpeggiator reaches the same function through
`Timbre::SendNote()` → `preenNoteOn()` (`Timbre.cpp:5213`) and
`SendScheduledNotes()` → `preenNoteOn()` (`Timbre.cpp:5242`).

The report from the architecture review is confirmed in the current source:
`Voice::noteOn()` (`Voice.cpp:157`) and `Voice::noteOnWithoutPop()` (`Voice.cpp:87`) do
not touch `matrix.sources[]`. `Matrix::resetSources()` is reached only from
`Voice::setCurrentTimbre()` (`Voice.cpp:3696`, voice redistribution) and
`Voice::afterNewParamsLoad()` (`Voice.h:320`, new parameter load). Nothing else clears a
matrix source, so the explicit restore is required.

## 8. Voice matching policy

- only voices of the addressed timbre (`voiceNumber[]`), never the global voice array;
- `voiceNumber[k] == -1` is skipped — `Synth::rebuidVoiceTimbre()` (`Synth.cpp:646`)
  fills unallocated slots with `-1`, and the pre-existing `Timbre::setMatrixSource()`
  does *not* guard this. The new code does. This is the lesson from the preenfm3 MPE fix
  `f0b7dc5` (`voiceToUse = channel - 1` → `voiceNumber_[channel - 1]` plus a `-1` guard).
- the voice must be `isPlaying()`;
- the match is `getNote() == note`, i.e. PFM2's own note identity. **No preenfm3
  `shiftNote` transform was added** — the PFM2 note on/off path uses `midiEvent.value[0]`
  directly (`MidiDecoder.cpp:254`–`271`), so poly pressure uses the same identity.

**Normal polyphony: first match, then return.** Source inspection supports this:
`preenNoteOn()` gives an already sounding identical note priority 1 and reuses that very
voice (`Timbre.cpp:650`–`677`), so a note number normally maps to at most one voice per
timbre. One transient exception exists and is documented in §17.

**Unison: all matching voices, no `break`.** `isUnison` is
`numberOfVoice > 1 && playMode == 2.0f` (matching `Timbre.cpp:624`), and in unison every
voice of the timbre plays the same note (`Timbre.cpp:666`–`674`, `730`–`743`). The
preenfm3 implementation (`Ixox/preenfm3` @ `65cb963`, `Timbre.cpp:3095`) breaks after the first match;
copying that would modulate one voice of the stack and leave the rest behind. The
existing PFM2 unison-aware patterns `preenNoteOff()` (`Timbre.cpp:796`) and
`propagateCvFreq()` (`Timbre.cpp:779`) were used as the reference.

## 9. Preset / reset behaviour

`Timbre::afterNewParamsLoad()` resets the baseline in the same place where
`Voice::afterNewParamsLoad()` → `Matrix::resetSources()` clears the per-voice sources
(`Timbre.cpp:4914`–`4917`). That covers every route with new-parameter-load semantics:

| route | reaches the reset via |
|---|---|
| normal preset load | `SynthState::propagateAfterNewParamsLoad()` → `Synth::afterNewParamsLoad()` (`Synth.cpp:392`) → `Timbre::afterNewParamsLoad()` |
| DX7 import | `SynthState::loadDx7Patch()` (`SynthState.cpp:1469`) → same |
| sysex patch | `SynthState::analyseSysexBuffer()` (`SynthState.cpp:2331`) → same |
| combo load | `Synth::afterNewComboLoad()` (`Synth.cpp:408`) → `Timbre::afterNewParamsLoad()` for all 4 timbres |

No preset data structure, field, size or source id was changed.

## 10. Glide result — **not extended, limitation documented**

Audited and deliberately left alone.

During and after a glide `Voice::getNote()` exposes the *previous* note while the note
actually sounding is `nextGlidingNote` (`Voice.cpp:69`–`72`, `235`–`239`). Poly pressure
therefore addresses the pre-glide note on a gliding voice.

Extending the match with `getNextGlidingNote()` was evaluated and rejected as **not
obviously safe**: `nextGlidingNote` is cleared only in `glideFirstNoteOff()`
(`Voice.cpp:238`) and `killNow()` (`Voice.cpp:277`). `Voice::noteOff()` — reached from
the hold-pedal release in `Timbre::setHoldPedal()` and from `Synth::allNoteOff()` — does
not clear it, and the reuse branch of `noteOnWithoutPop()` (`Voice.cpp:97`–`99`) sets
`note` without clearing it either. A voice could therefore carry a stale
`nextGlidingNote` from an earlier glide while playing an unrelated note, and poly
pressure for that stale note would then hit the wrong voice. A second corner is that
`nextGlidingNote == 0` doubles as the "not gliding" marker, so MIDI note 0 needs its own
guard — `preenNoteOff()` has exactly the same limitation.

Glide is only active when `numberOfVoice == 1` or in unison (`Voice.cpp:90`–`91`), i.e.
where poly pressure is degenerate anyway. Glide was **not redesigned** in this task.

## 11. Arpeggiator result — **not changed, limitation documented**

Audited. No clean existing mapping exists to reuse.

The arpeggiator transposes the sounding note by `note += 12 * current_octave_`
(`Timbre.cpp:5313`) before it reaches `preenNoteOn()` through `SendNote()`
(`Timbre.cpp:5213`). The voice therefore plays a different note number from the
key that is physically held, and no reverse mapping from an arpeggiated note back to the
originating key exists in the arpeggiator state.

> Poly pressure addresses physical MIDI note identity and may not follow
> octave-transposed internally generated arpeggiator notes.

With `arp octave == 1` there is no transposition and poly pressure works normally. No new
arpeggiator note tracking was added.

## 12. Editor impact — none

`sscheidl/preenfm2-Editor` was **not modified and not inspected**; no compatibility
problem was found that would require it.

Verified on the firmware side that nothing the editor consumes changed:

| editor touch point | state |
|---|---|
| matrix source list / `Srce` value range | unchanged (`SourceEnum` and `MATRIX_SOURCE_MAX` untouched) |
| `matrixSourceNames` / `Order` / `Position`, both `#ifdef CVIN` variants | unchanged, still 21 / 25 entries |
| `EDITOR_PROTOCOL_VERSION`, `EDITOR_CAPABILITIES` | unchanged (`1`, `3`) |
| NRPN address space, wire format, store command | unchanged |
| patch structure / full dump content | unchanged |
| `test/host/protocol_sim_test.py` | still passes (re-run, see §15) |

`AftT` keeps source id 10. Host MIDI `0xAn` is expected to pass through transparently.

## 13. Build results

### Toolchain limitation — read this before using the binaries

The documented 3.00 alpha toolchain (**gcc-arm-none-eabi 4.7-2014q2, GCC 4.7.4**) is
**not available** in this environment and could not be installed. The only ARM toolchain
obtainable was **GCC 13.2.1** (Ubuntu `gcc-arm-none-eabi` 15:13.2.rel1-2).

**The binaries produced here are NOT release-valid and must not be flashed or
published.** They exist only to obtain a compile/link result and a warning comparison.

GCC 13.2 cannot build the unmodified 3.00 alpha tree either. Three **pre-existing**
incompatibilities had to be shimmed, all **on the command line only — no source file, no
Makefile and no linker script in the repository was modified**:

| shim | reason (all pre-existing, unrelated to this feature) |
|---|---|
| `-std=gnu++98` | approximate GCC 4.7's default language mode |
| `-include sys/types.h` | GCC 13's newlib no longer pulls it in transitively, so `uint` in `LfoStepSeq.cpp:150` is undeclared |
| linker script copy with `SIZEOF(.jcr)` → `0` | GCC 13 emits no `.jcr` section; `linker/stm32f4xx.ld:143` references it |
| `LFLAGS` `-gc-sections` → `-g` | GCC 13 rejects the legacy spelling as a debug level |

Because both sides of the comparison were built with the *same* substitute toolchain and
the *same* shims, the **delta** below is meaningful. The **absolute** figures are not
comparable with the historical 3.00 alpha build (415 120 byte `.bin`, `.text` 360 352,
`.data` 54 768, `.bss` 100 216).

Every target was built with a full clean (`rm -rf build/*`) before it, from an
independent make invocation.

### Baseline (base SHA `ed8a866`, substitute toolchain)

| target | result | `.bin` | `.text` | `.data` | `.bss` | warnings |
|---|---|---|---|---|---|---|
| `pfm` | PASS | 355 648 | 300 848 | 54 800 | 100 932 | 102 |
| `pfmo` | PASS | 355 648 | 300 848 | 54 800 | 100 932 | 102 |
| `pfmcv` | PASS | 358 616 | 303 800 | 54 816 | 101 268 | 103 |
| `pfmcvo` | PASS | 358 616 | 303 800 | 54 816 | 101 268 | 103 |

### With polyphonic aftertouch (commit `6fdc28c`)

| target | result | `.bin` | `.text` | `.data` | `.bss` | warnings |
|---|---|---|---|---|---|---|
| `pfm` | **PASS** | 356 416 | 301 616 | 54 800 | 100 948 | 102 |
| `pfmo` | **PASS** | 356 416 | 301 616 | 54 800 | 100 948 | 102 |
| `pfmcv` | **PASS** | 359 448 | 304 632 | 54 816 | 101 284 | 103 |
| `pfmcvo` | **PASS** | 359 448 | 304 632 | 54 816 | 101 284 | 103 |

`pfm` and `pfmo` are byte-identical to each other (md5 `a4eb9627…`), as are `pfmcv` and
`pfmcvo` (md5 `c30176c2…`). All four were verified to be genuinely distinct builds: the
CVIN variants carry the `CV1` source name and are 2 968 bytes larger.

## 14. Binary / memory deltas

| target | `.bin` | `.text` | `.data` | `.bss` |
|---|---|---|---|---|
| `pfm` | **+768** | +768 | 0 | **+16** |
| `pfmo` | **+768** | +768 | 0 | **+16** |
| `pfmcv` | **+832** | +832 | 0 | **+16** |
| `pfmcvo` | **+832** | +832 | 0 | **+16** |

The 16 bytes are `lastChannelAfterTouch_` × 4 timbres and land in CCMRAM, as designed —
there is **no per-voice cost**, `Matrix::sources[]` is unchanged:

| section | baseline | feature |
|---|---|---|
| `.ccm` (`pfm`) | `0x6198` | `0x6198` — unchanged |
| `.ccmnoload` (`pfm`) | `0x848c` | `0x849c` — **+16** |
| `.ccmnoload` (`pfmcv`) | `0x8594` | `0x85a4` — **+16** |
| symbol `synth` | `0x7c44` | `0x7c54` — **+16** |
| `.bss` proper | unchanged | unchanged |

CCMRAM totals under this toolchain: `pfm` 58 932 / 65 536, `pfmcv` 59 196 / 65 536.
The historical 3.00 alpha CCMRAM headroom was 6 620 bytes; +16 bytes consumes 0.24 % of
it. **This must be re-measured with GCC 4.7.4 before any release.**

### Warnings delta

**Zero new warnings.** Warning counts are identical per target (102 / 102 / 103 / 103),
the sorted warning-kind histograms are byte-identical between baseline and feature for
all four targets, and **no warning at all is emitted for `Timbre.cpp`, `Timbre.h` or
`MidiDecoder.cpp`**. All pre-existing warnings are toolchain-era artefacts
(`-Wwrite-strings`, `-Wregister`, `-Wpointer-arith`, `__packed` redefinition).

## 15. Tests and reasoning performed

**Hardware was not available. No hardware validation is claimed.**

`test/host/polyat_state_test.py` — 69 checks, all pass. It does two things:

1. **Structural assertions against the real sources**: no
   `MATRIX_SOURCE_POLYPHONIC_AFTERTOUCH` anywhere, `SourceEnum` still ends
   `MPESLIDE, RANDOM, MAX`, `AFTERTOUCH` still id 10, both `#ifdef CVIN` display tables
   still 21 / 25 entries, no `PolA` name, `MatrixRowParams` unchanged, no MPE symbol, the
   editor protocol constants unchanged — plus the presence of the baseline member, the
   broadcast/selective split, the note-on restore, the reset, and the `n < 0` guard.
2. **State-model simulation** of the assignment's event sequences, through a
   transcription of the C++ control flow.

| gate | sequence | expectation | result |
|---|---|---|---|
| A | NoteOn C4, ChannelAT 80 | all voices → 80/127 | pass |
| B | C4+E4+G4, PolyAT C4 127, PolyAT E4 32 | 127, 32, baseline | pass |
| C | PolyAT C4 127, ChannelAT 50 | all voices → 50/127 | pass |
| D | ChannelAT 70, NoteOn D4 | D4 starts 70/127 | pass |
| E | ChannelAT 40, PolyAT C4 127, C4 voice reused for D4 | D4 → 40/127, not 127 | pass |
| F | ChannelAT 70, new params load | baseline → 0, new note → 0; then ChannelAT 70 → 70/127 | pass |
| G | unison, PolyAT on the note | every voice of the stack follows | pass |
| H | hold pedal, note released | held voice still addressable; channel broadcasts over it | pass |
| I | two timbres, PolyAT on one | the other timbre unchanged | pass |
| J | PolyAT 0, 127, rapid changes | value always within 0.0 … 1.0 | pass |
| K | repeated same note | restarts from the baseline | pass |

`test/host/protocol_sim_test.py` was re-run against the modified tree: **all checks pass**,
no editor-protocol regression.

The simulation reproduces control flow only. It cannot catch a compiler, timing,
interrupt-ordering or hardware level problem.

### Boundary-value reasoning (gate J)

`INV127 * value` with `value` 0…127 yields exactly 0.0 … 1.0. Poly and channel pressure
use the identical expression, so a note can never carry a pressure outside the range a
channel pressure could already produce. `MATRIX_SOURCE_AFTERTOUCH` is written through
`Matrix::setSource()` exactly as before; no clamping behaviour changed.

## 16. Manual hardware test plan (later, local phase)

Prerequisite: a controller that sends real MIDI poly key pressure. Record for each item
whether it was performed, on which build, and the result.

**Regression — must be unchanged from 3.00 alpha (run these first, without poly pressure)**

1. `AftT → Mix1`, channel aftertouch sweep: identical to 3.00 alpha.
2. `AftT → o*Fq`, channel aftertouch sweep: identical.
3. New note while channel aftertouch is held: the note starts at the current pressure.
4. Load a preset, browse all 12 matrix rows: no display anomaly, no reset.
5. Save and reload a preset: matrix rows unchanged.
6. Editor remote protocol: capability query, position query, store — all still work.
7. VOSIM algorithms 29–32 and the added LFO shapes: unchanged.

**Polyphonic aftertouch**

8. Three-note chord, independent pressure per note (`AftT → o*Fq`).
9. Pressure 0 and pressure 127 on a single note.
10. Fast continuous pressure movement on one note of a held chord.
11. Channel aftertouch **after** poly aftertouch: all voices jump to the channel value.
12. New note after channel aftertouch: starts at the channel value.
13. Repeated same note: restarts at the channel value until fresh poly pressure arrives.
14. **Voice stealing**: full poly pressure on C4, then play enough notes to steal that
    voice — the new note must start at the channel baseline, not at C4's pressure.
15. Sustain pedal: held note still responds to its poly pressure; channel aftertouch
    broadcasts over it; a later reuse of that voice restores the baseline.
16. Unison (`playMode = unison`, `numberOfVoice > 1`): the whole stack follows together,
    no drift between stack members.
17. Mono (`numberOfVoice = 1`).
18. Glide: confirm the documented limitation of §10, do not treat it as a defect.
19. Arpeggiator with `octave > 1`: confirm the documented limitation of §11.
    Also check `octave == 1`, where poly pressure must work.
20. All four timbres on four channels: poly pressure affects only the addressed timbre.
21. Global MIDI channel: poly pressure reaches all four timbres.
22. Current-instrument channel and omni.
23. High polyphony with a rapid poly pressure stream from several notes at once: no
    stuck note, no audible dropout, no CPU overload.
24. **Zipper/click comparison**: `AftT → Mix*` driven by channel aftertouch versus the
    same routing driven by poly aftertouch. Both are 7-bit into an unsmoothed
    destination; the point is to establish whether poly pressure is *worse* than the
    pre-existing channel behaviour, not to fix it here.

Smoothing is explicitly **not** addressed in this task.

## 17. Known limitations

1. **Glide** — poly pressure addresses the pre-glide note on a gliding voice. Not
   extended; reasoning in §10.
2. **Arpeggiator with octave > 1** — poly pressure does not follow transposed notes.
   Reasoning in §11.
3. **Duplicate note, transient** — `preenNoteOn()` skips a voice in
   `isNewNotePending()` state (`Timbre.cpp:646`) and may allocate a second voice for a
   note number that an outgoing voice still reports. During that window (one audio
   block, ~0.7 ms at 46.9 kHz / `BLOCK_SIZE 32`) two voices can match, and first-match
   wins picks the lower `voiceNumber[]` slot. Not audible in practice; noted for
   completeness rather than as a defect.
4. **MIDI note 0** — not a limitation of this implementation, but note that
   `nextGlidingNote == 0` doubles as the "not gliding" marker in the existing code
   (see §10).
5. **Behaviour extension, by design** — an existing preset routing `AftT` responds
   note-locally as soon as poly pressure arrives, with no way to switch that off. Without
   poly pressure messages the observable aftertouch behaviour is functionally equivalent
   to 3.00 alpha. This is intentional under option B and belongs in the changelog.
6. **Builds are not release-valid** — substitute toolchain, see §13.
7. **No hardware validation** — §16 is entirely open.

## 18. MPE

**MPE NOT IMPLEMENTED.**

No MPE code, symbol or matrix source was added; verified by grep for `noteOnMPE`,
`noteOffMPE`, `setMatrixSourceMPE`, `AFTERTOUCH_MPE`, `PITCHBEND_MPE` and
`POLYPHONIC_AFTERTOUCH` — zero hits in `src/`.

The next major firmware goal after stable polyphonic aftertouch is **full MPE support**,
on its own branch. Planned pressure rule, consistent with what this task builds:

    MPE Member Channel Pressure -> same MATRIX_SOURCE_AFTERTOUCH, selective per-voice write

Dimensions to review separately in that task:

| MPE dimension | intended mapping |
|---|---|
| Strike | Velocity |
| Press | `AftT` |
| Glide | per-voice pitch bend |
| Slide | per-voice CC74 |
| Lift | investigate note-off velocity |

Planned hardware for that stage: ROLI LUMI for a quick MPE test, ROLI Seaboard RISE as
the full expressive reference.

The per-voice selective write and the `voiceNumber[k] < 0` guard introduced here are the
same mechanisms MPE will need, and the preenfm3 history (`3a2e17a` introduced
`voiceToUse = channel - 1`; `f0b7dc5` fixed it to `voiceNumber_[channel - 1]` with a
`-1` guard) is the concrete trap to avoid when the member-channel mapping is added.

## 19. Feature branch HEAD

Branch: `feature/true-poly-aftertouch`, based on `ed8a866`.

    commit 1   6fdc28c   firmware change (the only commit touching src/)
    commit 2   HEAD      this report + test/host/polyat_state_test.py

Commit 2 is the branch HEAD. Its SHA is deliberately not written into this file: the
file is part of that commit, so quoting the SHA here could only ever be stale. Read it
with `git rev-parse feature/true-poly-aftertouch`, or from the push output.

---

**IMPLEMENTATION GATE: PASS FOR INDEPENDENT REVIEW**

with the two limitations stated plainly: the four builds were produced with a
**substitute toolchain (GCC 13.2, not the documented GCC 4.7.4)** and are **not
release-valid**, and **no hardware validation was performed**. Everything that could be
verified in the cloud — all four targets building, zero new warnings, the memory delta,
the structural constraints of option B, and all eleven state-model gates — was verified.
