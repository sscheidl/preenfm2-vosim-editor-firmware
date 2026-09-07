# MPE — implementation report

PreenFM2 firmware 3.00 alpha, branch `feature/full-mpe`.

**ARCHITECTURE GATE: PASS** — a coherent zone/timbre mapping is derivable from the
existing PreenFM2 routing; §3 gives the derivation. Implementation followed.

**An independent review then raised four correctness findings, R1–R4. All four are
CONFIRMED and fixed; §19 records them and the second-order defect R3 exposed.** Claims
in §3, §9 and §10 that the fixes invalidated have been corrected in place.

The PolyAT architecture is preserved unchanged: `MATRIX_SOURCE_AFTERTOUCH` remains the
one pressure dimension. **No matrix source was added.** No preset format change.

---

## 1. Verified base

| item | value |
|---|---|
| base branch | `feature/true-poly-aftertouch` |
| base SHA | `f503e7fd34c1df887e421f782520275a7b807632` |
| expected SHA | `f503e7fd34c1df887e421f782520275a7b807632` — **matches** |
| working tree at start | clean, local == remote |
| new branch | `feature/full-mpe`, created from exactly that SHA |

`feature/true-poly-aftertouch` was not modified.

## 2. Audit of the MPE-related code that already existed

Searched for `MPE`, `MPESLIDE`, `CC74`, pitch bend, `voiceNumber`, channel-to-voice
mapping, MIDI routing, RPN/NRPN, omni, global, current instrument.

| existing item | verdict |
|---|---|
| `MATRIX_SOURCE_MPESLIDE` (`Common.h:484`), broadcast at `MidiDecoder.cpp:667`, shown as `"CC74"` | **reusable**, kept as the Slide source. Its name promises MPE but it was only ever a timbre-wide CC74 broadcast — misleading, now actually per-voice under MPE while the broadcast stays for normal MIDI |
| `CC_MPE_SLIDE_CC74` = 74 (`MidiDecoder.h:96`) | **reusable**, unchanged |
| `MATRIX_SOURCE_PITCHBEND` broadcast (`MidiDecoder.cpp:304`) | **incomplete for MPE**, see §8 |
| `Timbre::voiceNumber[]` + `-1` for unallocated (`Synth::rebuidVoiceTimbre`, `Synth.cpp:652`) | **reusable**, it is the timbre-local voice list MPE needs |
| `Timbre::setMatrixSource()` | broadcast only, and does **not** guard `-1`; not reused for MPE |
| **channel-to-voice mapping** | **did not exist**. Added |
| **RPN (CC 100/101)** | **does not exist anywhere**. NRPN (CC 98/99) is handled, RPN is not. See §6 |
| omni / global / current instrument routing | **reusable**, MPE inserts before it |

There was no partial MPE implementation to complete — only two well-chosen names.

## 3. Architecture: zones and the four timbres

The constraint that decides this: `MidiDecoder::midiEventReceived()` builds the list of
addressed timbres from the channel *before* the message switch. That is the single
natural insertion point, and it makes the following coherent without inventing anything:

**One zone, lower-zone shaped, owned by one timbre.**

    channel            role
    ---------------------------------------------------------------
    master             ordinary PreenFM2 routing  -> zone-wide
    master+1 .. +N     MPE member channels        -> per voice
    everything else    ordinary PreenFM2 routing  -> other timbres

- **Member channels** are intercepted at the top of `midiEventReceived()` and returned
  from. They never reach the per-timbre channels, the global channel, the current
  instrument channel or omni. A timbre configured on a channel inside the zone is
  therefore shadowed while MPE is on — that is what MPE requires, and it is stated
  rather than silently mixed.
- **The manager channel addresses the MPE timbre explicitly**, and then runs the
  ordinary message switch. That keeps every zone-wide meaning — channel pressure
  broadcasts to the timbre and sets the baseline, pitch bend and CC74 broadcast, CC64 is
  the sustain pedal, notes are ordinary notes — while guaranteeing the target.

  > **Corrected.** This report previously said the manager channel was "deliberately not
  > special-cased" and that ordinary routing "*is* the zone-wide behaviour". That was
  > only true when the MPE timbre happened to be the one configured on the manager's midi
  > channel. Review finding **R1** (§19) showed it reaching the wrong timbre otherwise.
  > The ordinary channel match is now skipped for the manager channel.
- **Other timbres** keep working normally on channels outside the zone.
- **Polyphony** is the MPE timbre's own voice count, so up to 14.

### Upper Zone — not supported

Only the ascending shape is implemented. An Upper Zone has its master at channel 16 with
members counting *downwards*; the master channel is configurable here, but the members
are always above it. Set the controller to a Lower Zone. Documented, not half-built.

## 4. Configuration

Four settings appended at the end of the midi configuration enum:

| setting | file key | values | default |
|---|---|---|---|
| `MPE inst:` | `mpeinst` | Off / 1 / 2 / 3 / 4 | **Off** |
| `MPE master:` | `mpemaster` | 1..16 | 1 |
| `MPE members:` | `mpemembers` | count above the master, clamped 1..15 | 15 |
| `MPE bend st:` | `mpebend` | 0..48 semitones | 48 |

Appending is safe because `preenfm2.txt` is **keyed by name**, not by index:
`ConfigurationFile::saveConfig()` writes `nameInFile=value` and `fillMidiConfig()`
compares `midiConfig[k].nameInFile` (`ConfigurationFile.cpp:76`, `:148`). An older
configuration file simply does not carry these four keys and they keep their defaults.
The menu page length is `MIDICONFIG_SIZE + 1` (`Menu.cpp:436`), so the entries appear
automatically.

**MPE is off by default. Nothing changes for anyone who does not switch it on.**

## 5. Member channel → voice identity

`Timbre::mpeVoiceOfChannel_[16]` — the voice each member channel owns, `-1` for none.
Note *number* is never used to address expression: two member channels routinely hold
the same note at the same time.

| event | handling |
|---|---|
| member note on | `mpeNoteOn()` → ordinary `preenNoteOn(note, velocity, **false**)`, then record the voice |
| member note off | `mpeNoteOff()` → release *the voice this channel owns*, never a note-number search |
| sustain | held voice keeps sounding; the channel releases ownership immediately |
| voice stealing | the allocator is untouched; the robbed channel's entry is dropped |
| voice reuse | same |
| repeated note on one channel | the channel's previous voice is released first |
| all notes off / all sound off | `Synth::allNoteOff/allSoundOff` → `mpeForgetAllChannels()` |
| preset / parameter load | `Timbre::afterNewParamsLoad()` → `mpeForgetAllChannels()` |
| voice redistribution | `Timbre::setVoiceNumber()` → `mpeForgetAllChannels()` |

**`reuseSameNote=false` is the load-bearing detail.** `preenNoteOn()` gives an already
sounding identical note priority 1 and reuses that voice. Under MPE that would hand
member channel 3 the voice of member channel 2 and steal its note. `preenNoteOn()` now
takes a `reuseSameNote` flag (default `true`, so ordinary MIDI is untouched) and MPE
passes `false`. Every other allocation rule, including stealing, is unchanged.

**Ownership is dropped in one place, not scattered.** `preenNoteOnUpdateMatrix()` is
already the universal note-on hook that restores the pressure baseline on a recycled
voice. It now also clears that voice's member bend and any member channel still claiming
it. Because *every* allocation route passes through it, a voice can never carry an
association or a bend from its previous owner — including when a non-MPE note takes it.

`preenNoteOn()` now returns the voice it used, or `-1`; it returned `void` before.
`lastPlayedVoiceNum` was not usable for this: it is not updated when no voice is
available, so it would report a stale voice.

Out-of-range access: `channel > 15` is rejected, `voiceNumber[k] < 0` is skipped, and
`mpeVoiceOf()` drops an association whose voice has stopped.

## 6. RPN / MPE Configuration Message — minimal handling (was: not implemented)

**Result of the audit: RPN is not parsed anywhere in this firmware.** `decodeNrpn()`
handles CC 98/99 (NRPN); CC 100/101 (RPN) appear nowhere in `src/`. The MPE
Configuration Message is RPN 6, and pitch bend sensitivity is RPN 0, so neither can be
received today.

Review finding **R2** (§19) showed that this was not merely a missing feature but a
hazard: **CC 100 and CC 101 are `CC_ARP_CLOCK` and `CC_ARP_DIRECTION`** in this
firmware, and **CC 6 / CC 38 are the NRPN data entry bytes**. A standard RPN sequence
would have edited the arpeggiator and fed the NRPN state machine.

A minimal RPN state machine now runs **only while MPE is on**:

| RPN | on the manager channel | on any other channel |
|---|---|---|
| 0 — pitch bend sensitivity | sets `MPE bend st` (clamped 0..48) | consumed, ignored |
| 6 — MPE Configuration Message | sets `MPE members` (clamped 1..15); `n == 0` consumed but **not** honoured, it would silently undo the menu setting | consumed, ignored |
| anything else | consumed, ignored | consumed, ignored |

`CC 101`/`CC 100` are always consumed while MPE is on. `CC 6`/`CC 38` are consumed
**only while an RPN is actually selected** on that channel; RPN Null (127/127) is the
initial and reset state, so ordinary data entry keeps reaching the NRPN path and the
editor remote protocol is untouched — `decodeNrpn()` and every editor constant are byte
identical to the reviewed PolyAT branch.

With MPE off nothing is consumed and CC 100/101/6/38 keep their existing meanings.

The zone itself is still configured on the PreenFM2, in the menu; the controller must be
set to match. A full RPN parser
would be a separate change with its own risk — the CC path is shared with the parameter
CC map — and is not smuggled into this branch.

## 7. Press — member channel pressure

Status `0xDn` on a member channel → `MATRIX_SOURCE_AFTERTOUCH` on **that channel's voice
only**. It does **not** touch `lastChannelAfterTouch_`: that stays the timbre-wide
baseline, exactly as on the reviewed PolyAT branch.

Master channel pressure keeps the reviewed behaviour: broadcast to every voice of the
timbre **and** set the baseline (`setMatrixChannelAfterTouch`).

Ordinary channel pressure and ordinary polyphonic key pressure are untouched.

## 8. Glide — per-voice pitch bend

Audited first, and this is where the interesting constraint is.

Pitch bend today is `MATRIX_SOURCE_PITCHBEND` (source ∈ [−1, +1)) × the preset's matrix
row multiplier → `ALL_OSC_FREQ_HARM` → `findex = 512 + dest*20` into `exp2_harm[]`
(`Voice.cpp:329`). That table has 1024 entries at 0.1 semitone per step, index 512 = 0,
so **one destination unit is two semitones**.

The matrix row multiplier is capped at ±10 (`SynthState.cpp:518`), giving **at most
±20 semitones from one row** — while an MPE controller sends **±48 by default**. Making
the existing source merely voice-local would therefore produce a silently wrong bend
range at default ROLI settings, and getting to ±48 would need a multiplier of 24, which
the parameter cannot hold.

**Resolution:** member pitch bend does not go through the matrix at all. `Voice` gets a
small `mpeFreqOffset` (in the same `ALL_OSC_FREQ_HARM` unit) that is added to the
existing term. That means:

- **no new matrix source, no source id change, no preset change** (§13 of the assignment
  is satisfied literally);
- the full ±48 semitone range works at the ROLI default;
- ordinary pitch bend is completely untouched — same source, same broadcast, same range.

`mpeSetPitchBend()` computes `bend × range × 0.5`, e.g. full bend at 48 semitones →
24.0 units → `findex` 992, inside the table.

**Bounds fix.** `exp2_harm[index]` was **never bounded** — `findex` came straight from
the destination with no clamp, a latent out-of-bounds read reachable today by stacking
matrix rows onto `ALL_OSC_FREQ_HARM`. Since this change adds a second contributor to
that expression, the index is now clamped to the table's usable 0..1022 range. This is
scoped to the expression this feature feeds, not general cleanup.

Master channel pitch bend keeps the ordinary broadcast behaviour.

## 9. Slide — CC74

Member channel CC74 → `MATRIX_SOURCE_MPESLIDE` on that channel's voice only. The
existing source is reused exactly as its name always intended; **no second slide source
was created**. Manager channel CC74 keeps the ordinary timbre-wide broadcast, now
through `Timbre::setMatrixSlide()`, which also stores it as `lastSlide_`.

`preenNoteOnUpdateMatrix()` restores `lastSlide_` on every new note, exactly as it
restores the pressure baseline. Without that a recycled voice kept the per-voice slide
of whoever used it before — a defect that the unconditional zero-write fixed by **R3**
had been masking (§19). For ordinary MIDI the restore is idempotent: the CC74 broadcast
had already written the same value to every voice, including the idle ones.

**Every other control change is ignored on a member channel, deliberately.** The
ordinary `controlChange()` path maps many CC numbers to synth parameters (mix, pan, IM,
filter, envelope…). Letting a member channel through would let a controller edit the
patch while playing. Only note on/off, channel pressure, pitch bend and CC74 are acted
on. Sustain (CC64) is a master-channel message in MPE and works there.

## 10. Per-member-channel expression state

`MidiDecoder` keeps `mpePressure[16]`, `mpeSlide[16]`, `mpeBend[16]`.

Plus `mpePressureSeen[16]`, `mpeSlideSeen[16]` and the per-channel RPN selection state.

**The exact rule:**

1. A member pressure / bend / CC74 message updates that channel's remembered value,
   **marks it seen**, and writes it to the channel's voice if it owns one.
2. On member **note on**, after the voice is allocated (which restores the timbre
   pressure and slide baselines and clears the bend), a remembered value is applied
   **only if that channel has actually sent one**. This is what makes expression sent
   *just before* the note on — normal MPE controller behaviour — belong to that note,
   without an unsent value pretending to be an explicit 0.
3. On member **note off** (including note on with velocity 0) the channel's values, seen
   flags and RPN selection are cleared.

**Bend deliberately has no seen flag.** An unseen bend is `0.0f`, which is the centre,
which is exactly what `preenNoteOnUpdateMatrix()` has already left on the voice — so the
write is a no-op and the state would be dead weight.

> **Corrected.** Rule 2 previously applied all three values unconditionally. Review
> finding **R3** (§19) showed that an unseen `0` then wiped out the manager pressure or
> slide baseline the voice had just been given.

Rule 3 is a deliberate choice over "carry the controller's current state across notes":
the leak-free behaviour is the one that can be reasoned about, and a controller that
wants the next note pressed re-sends the value between note off and note on anyway.

## 11. Normal MIDI regression contract

With `MPE inst = Off` (the default) `getMpeTimbre()` returns −1, the interception is
skipped, and `midiEventReceived()` is byte-for-byte the reviewed PolyAT path. Note on,
note off, channel pressure, polyphonic key pressure, pitch bend, CC74, channel routing,
global / current instrument / omni, the editor protocol and presets are all unchanged.

With MPE **on**, everything outside the zone still behaves normally; only the member
channels are diverted.

The one behaviour change for ordinary MIDI is the `exp2_harm` clamp of §8, which can
only alter output where the old code was reading outside the table.

## 12. Preset compatibility

No preset migration. `SourceEnum`, `MATRIX_SOURCE_MAX`, the two `#ifdef CVIN` display
tables, `struct MatrixRowParams` and every source id are unchanged — asserted
mechanically by `test/host/mpe_state_test.py`. An MPE patch is an ordinary patch that
routes `AftT`, `CC74` and `PitB`.

`preenfm2.txt` gains four named keys; older files load unchanged (§4).

## 13. Files changed

| file | change |
|---|---|
| `src/synth/Timbre.h` | `mpeVoiceOfChannel_[16]`, 5 methods, `preenNoteOn` returns `int` and takes `reuseSameNote` |
| `src/synth/Timbre.cpp` | MPE methods, allocator switch, ownership/bend clear in `preenNoteOnUpdateMatrix`, cleanup in `afterNewParamsLoad` and `setVoiceNumber` |
| `src/synth/Voice.h` | `mpeFreqOffset` + setter |
| `src/synth/Voice.cpp` | offset applied to the frequency, `exp2_harm` index bounded, init |
| `src/synth/Synth.cpp` | drop associations in `allNoteOff` / `allSoundOff` (both) |
| `src/midi/MidiDecoder.h` | 5 methods, per-member-channel expression state |
| `src/midi/MidiDecoder.cpp` | member channel interception, `mpeEventReceived()`, zone helpers |
| `src/hardware/Menu.h` / `.cpp` | four configuration entries + two name tables |
| `src/synth/SynthState.cpp` | four defaults |

Correctness pass (§19), on top of the above:

| file | change |
|---|---|
| `src/midi/MidiDecoder.h` | `isMpeManagerChannel`, `mpeConsumeRpn`, seen flags, RPN state, configuration-change detection, the two listener hooks |
| `src/midi/MidiDecoder.cpp` | manager routing, RPN state machine, seen gating, reset hooks on CC 120/123/127 |
| `src/synth/Timbre.h` / `.cpp` | `lastSlide_` + `setMatrixSlide()` and its restore |
| `src/synth/Voice.h` | `afterNewParamsLoad()` clears `mpeFreqOffset` |

Firmware total **+400 / −16** against the PolyAT base. Not touched: `Common.h`,
`Presets.cpp`, `Matrix.*`, `filesystem/**`, `utils/**`, the editor protocol, the
Makefile, the linker scripts.

## 14. Commits

| # | SHA | subject |
|---|---|---|
| 1 | `0e67aa6` | MPE part 1: member channel voice identity and per voice expression |
| 2 | `a822781` | MPE part 2: zone configuration and MIDI routing |
| 3 | `115d0f2` | MPE host tests, test tool and this report |
| 4 | `c2ffc08` | Review R1 and R2: manager channel routing and RPN containment |
| 5 | `ee5bb1e` | Review R3 and R4: member expression validity and lifecycle resets |
| 6 | branch HEAD | R1–R4 regression tests and report update |

Commits 1–3 were reviewed and are **not** rewritten.

Commit 1 compiles and changes no behaviour (nothing calls the new code yet); commit 2
activates it. A finer split was considered and rejected as artificial: routing without
identity does not compile, and identity with routing but without the three expression
dimensions is not a state anyone would want to bisect to.

The reviewed PolyAT history was not rewritten.

## 15. Host tests

`test/host/mpe_state_test.py` — **250 checks in 18 scenarios, all pass.**

It **does not execute firmware code.** It is (1) structural assertions read from the
real sources and (2) a **simulation** — a transcription of the C++ control flow. It
cannot catch a compiler, timing, interrupt-ordering or hardware problem.

| gate | expectation | result |
|---|---|---|
| A | two member channels, independent pressure, baseline untouched | pass |
| B | independent bend; full bend at 48 st = 24.0 units; other voices unbent | pass |
| C | independent CC74 | pass |
| D | pressure + bend + CC74 on two channels, **same note number**, no cross-talk | pass |
| E | member channel reuse leaks no pressure, bend or slide | pass |
| F | stealing: no shared ownership, stolen voice carries nothing over | pass |
| G | sustain holds the voice, the channel releases ownership | pass |
| H | repeated note on one channel does not leave the previous note hanging | pass |
| I | master channel is not a member; its pressure broadcasts and sets the baseline | pass |
| J | MPE off: all 16 messages reach the ordinary routing, no MPE state touched | pass |
| K | ordinary PolyAT regression | pass |
| L | member channel never reaches a second timbre | pass |
| M | all-notes-off and parameter load clear associations, baseline and bends | pass |
| N | zone boundaries for three configurations; 0/127 and both bend extremes stay in range and inside the bounded `exp2_harm` index | pass |
| **R1** | MPE inst 2, manager ch1, ordinary timbre 1 also on ch1: manager pressure, CC74 and CC64 reach the MPE timbre and **not** timbre 1, also with a global channel set | pass |
| **R2** | RPN 6 sets the member count and RPN 0 the bend range; no arp CC, no NRPN data entry, no ordinary message switch is reached; after RPN Null CC 6/38 go back to the NRPN path; an upper-zone MCM on ch16 is consumed without effect; with MPE off CC 100/101/6/38 keep their ordinary meanings | pass |
| **R3** | manager pressure 70 and slide 90, member note on with nothing sent: the voice keeps 70 and 90; an explicit member 0 then overrides both | pass |
| **R4** | CC 123 / CC 120 / CC 127 / parameter load / MPE off-and-on / moving the zone all leave no stale pressure, slide, bend, seen flag or ownership; `mpeFreqOffset` is 0 after a parameter load | pass |

Assertions were verified **non-vacuous** by reintroducing each defect and watching the
matching check fail — seven in total, none committed:

| defect reintroduced | assertion that fired |
|---|---|
| `reuseSameNote=true` in `mpeNoteOn` | mpeNoteOn reuses a same-note voice |
| bend clear removed from `preenNoteOnUpdateMatrix` | a recycled voice keeps the previous member channel bend |
| manager routing reverted | R1: the manager channel does not address the MPE timbre explicitly |
| RPN consumption disabled | R2: RPN is not consumed in the routing / must be consumed before the member branch |
| `mpePressureSeen` gate removed | R3: an unseen member pressure is still applied on note on |
| `mpeFreqOffset` clear removed from `Voice::afterNewParamsLoad` | R4: a preset load would leave a member bend on the voice |
| slide restore removed from `preenNoteOnUpdateMatrix` | a recycled voice keeps the previous member channel slide |

`polyat_state_test.py` (79 checks) and `protocol_sim_test.py` (115 checks) both still
pass unchanged.

`tools/mpe_test.py --self-test` passes: status bytes, 14-bit pitch bend including both
extremes and clamping, channel and value ranges, master-vs-member channel use, no
destructive message, no hanging note, and that scenario E really does play one note
number on two member channels.

## 16. Build results

**Same substitute toolchain and shims as the reviewed PolyAT cloud build: GCC 13.2, not
the documented GCC 4.7.4. These binaries are NOT release-valid and must not be flashed.**
Shims (command line only, no source/Makefile/linker-script change): `-std=gnu++98`,
`-include sys/types.h`, a linker-script copy with `SIZEOF(.jcr)` → `0`, `LFLAGS`
`-gc-sections` → `-g`. Clean (`rm -rf build/*`) before every target.

Figures below are the **corrected** branch HEAD, after R1–R4.

| target | result | `.bin` | `.text` | `.data` | `.bss` | warnings |
|---|---|---|---|---|---|---|
| `pfm` | **PASS** | 360 384 | 305 496 | 54 888 | 101 356 | 102 |
| `pfmo` | **PASS** | 360 384 | 305 496 | 54 888 | 101 356 | 102 |
| `pfmcv` | **PASS** | 363 216 | 308 312 | 54 904 | 101 692 | 103 |
| `pfmcvo` | **PASS** | 363 216 | 308 312 | 54 904 | 101 692 | 103 |

`pfm`/`pfmo` byte-identical (md5 `b1a70f31…`), `pfmcv`/`pfmcvo` byte-identical
(md5 `ee53ac3b…`).

Against the pre-fix MPE HEAD `115d0f2` (359 104 / 361 936): **+1 280 bytes** in every
variant, `.bss` **+72**, CCMRAM **+16**, `.data` unchanged. Those 88 bytes account for
themselves exactly: `mpePressureSeen[16]` + `mpeSlideSeen[16]` = 32, `mpeRpnMsb/Lsb[16]`
= 32, `mpeLastTimbre` + `mpeLastManager` = 8 (all `.bss`), and `Timbre::lastSlide_` × 4
timbres = 16 (CCMRAM).

### Delta against the PolyAT base

| target | `.bin` | `.text` | `.data` | `.bss` |
|---|---|---|---|---|
| `pfm` / `pfmo` | +4 032 | +3 944 | +88 | +408 |
| `pfmcv` / `pfmcvo` | +3 832 | +3 744 | +88 | +408 |

Section detail (`pfm`), and the 320 bytes account for themselves exactly:

| section | PolyAT | MPE HEAD | corrected | what the correction added |
|---|---|---|---|---|
| `.data` | `0x7478` | `0x74d0` | `0x74d0` | — |
| `.bss` | `0x105b8` | `0x10678` | `0x106c0` | seen flags 32 + RPN state 32 + config detection 8 |
| `.ccm` | `0x6198` | `0x6198` | `0x6198` | — |
| `.ccmnoload` (CCMRAM) | `0x849c` | `0x851c` | `0x852c` | `Timbre::lastSlide_` × 4 = 16 |

CCMRAM total 59 076 / 65 536 under this toolchain, **+144** against the PolyAT base. The
historical GCC 4.7.4 headroom was 6 620 bytes; +144 is 2.2 % of it. **Re-measure with
GCC 4.7.4 before any release.**

### Warnings delta: none

Counts identical per target (102/102/103/103) and the warning-kind histograms are
byte-identical to the PolyAT build **and** to the pre-fix MPE build for all four.
Exactly one warning is *located* in a file this feature touches —
`SynthState.cpp:1571 control reaches end of non-void function` — and it is
**pre-existing**: on the PolyAT branch it is the same warning in the same function at
line 1566, moved only because four default assignments were added above it.

## 17. Known limitations

1. **Upper Zone not supported** (§3). Lower-zone shape only, master configurable.
2. **RPN handling is minimal** (§6). RPN 0 and RPN 6 are honoured on the manager
   channel; every other RPN is consumed and ignored so it cannot reach the patch. The
   zone itself (which timbre, which manager channel) is still configured in the menu.
3. **Unison + MPE is not a supported combination.** In unison `preenNoteOn()` starts the
   whole stack but reports one voice, so only that voice would follow member expression.
   Use the MPE timbre in ordinary polyphonic mode.
4. **Arpeggiator + MPE is not meaningful** — the arpeggiator transposes notes
   (`Timbre.cpp:5330`) and the member channel association is made at note on.
5. **Lift / note-off velocity is not implemented.** Audited as §1 of the assignment
   asked: `Voice::noteOff()` takes no velocity and the six envelopes have no release
   velocity input, so there is no existing parameter for it to reach. Adding one would
   mean new envelope state and a new matrix source. Not added to claim spec coverage.
6. **Member polyphony is the MPE timbre's voice count**, up to 14; a controller sending
   15 member channels will steal voices.
7. **A timbre configured inside the zone is shadowed** while MPE is on (§3) — intended,
   but it will look like a dead timbre if a user forgets.
8. **Builds are not release-valid** (§16) and **no hardware validation was performed**.

## 18. Hardware test plan — LUMI and Seaboard RISE

Prerequisite for both: `tools/midi_monitor.py --port <controller> --summary`, then press
and slide a key. Confirm you see `polytouch`/`aftertouch` per member channel, `pitchwheel`
per member channel and `control_change 74`, and note which channels are in use. Set the
PreenFM2 `MPE master:` and `MPE bend st:` to match the controller; set `MPE inst:` to the
timbre you will play. A patch routing `AftT`, `CC74` and — for reference — `PitB` is
needed to hear anything.

**ROLI LUMI — quick smoke test**

1. MPE off: play normally, confirm nothing changed (this is the regression gate).
2. MPE on: single note, press → only that note responds.
3. Two notes: press one, the other must not move.
4. Slide one note (CC74) → only that note.
5. Bend one note → only that note, and by the configured number of semitones.
6. Release everything → no stuck note.

**ROLI Seaboard RISE — full validation**

7. Strike: velocity differences per note.
8. Press: three-note chord, each note pressed independently, held for several seconds.
9. Glide: slide one note a full octave while the others stay put; verify the interval
   matches `MPE bend st`, then repeat with the setting deliberately mismatched to
   confirm it is the setting that governs.
10. Slide (CC74): independent per note.
11. All three at once on two notes moving in opposite directions.
12. **Same note number on two fingers** — the case a note-number implementation gets
    wrong. Both must sound and respond independently.
13. **Voice stealing**: play more notes than the timbre has voices; no stuck note, and a
    stolen voice must not carry the previous note's pressure or bend.
14. Sustain pedal (master channel) with notes released under the pedal, then new notes.
15. Master channel pressure while per-note pressure is active → all voices jump to it.
16. Fast repeated notes on one finger.
17. Preset change while playing → no stuck note, no leftover bend.
18. `MPE inst: Off` mid-session → controller falls back to ordinary MIDI cleanly.
19. Another timbre on a channel **outside** the zone → unaffected. Then move it
    **inside** the zone → confirm it is shadowed, as documented.
20. Zipper/click check on `AftT → Mix*` driven by member pressure, compared with channel
    aftertouch. Smoothing remains out of scope.

Record results the same way as `docs/POLYAT_HARDWARE_TEST_CHECKLIST.md`, and rebuild
with GCC 4.7.4 per that document's build handoff before flashing anything.

## 19. Independent review findings R1–R4

All four verified against the real source before any change. **All four CONFIRMED.**

### R1 — manager channel could target the wrong timbre — **CONFIRMED + FIXED**

`midiEventReceived()` intercepted member channels only; the manager channel fell into
the ordinary channel match. With `MPE inst 2`, manager channel 1 and timbre 1 configured
on channel 1, manager pressure, CC74 and CC64 reached **timbre 1** while the member
channels drove timbre 2. A global or current-instrument channel equal to the manager made
it worse. This report's own §3 claim was wrong and is corrected there.

Fixed by addressing the configured MPE timbre explicitly and skipping the ordinary match
for that channel. The message still runs the unchanged switch, so every zone-wide meaning
survives. With `MPE inst = Off` the original path is taken unchanged.

### R2 — RPN could edit the patch — **CONFIRMED + FIXED, worse than reported**

The review expected a fall-through hazard. The source shows two, not one:

- **CC 100 = `CC_ARP_CLOCK`, CC 101 = `CC_ARP_DIRECTION`** — an RPN *selection* edits the
  arpeggiator;
- **CC 6 and CC 38 are the NRPN data entry bytes** (`controlChange()`, `case 6:` /
  `case 38:`) — the *data entry* of an RPN sequence also lands in `currentNrpn[]` and can
  complete an unrelated NRPN.

An MPE Configuration Message would therefore have changed arp clock, arp direction and
NRPN state. Fixed by the minimal RPN state machine of §6, which consumes those bytes
before anything else sees them, only while MPE is on, and only lets the manager channel
change a setting. RPN Null keeps ordinary data entry working, so the editor protocol is
untouched — verified byte-identical.

### R3 — unseen member value overwrote the baseline — **CONFIRMED + FIXED**

`mpePressure[]`/`mpeSlide[]` started at 0 and were reapplied unconditionally after a
member note on, so "nothing sent yet" was indistinguishable from "explicitly 0". With a
manager pressure of 70, `preenNoteOnUpdateMatrix()` put 70 on the voice and the next line
put it back to 0.

One correction to the finding's wording: the member write never touched
`lastChannelAfterTouch_` itself — `mpeSetMatrixSource()` only writes the voice. It
overwrote the value on the voice that the baseline had just been restored to. Same
audible bug, different mechanism.

Fixed with `mpePressureSeen[]` / `mpeSlideSeen[]`. Bend deliberately got no flag (§10).

**Second-order defect this exposed.** With the unconditional zero-write gone, a recycled
voice kept the per-voice **slide** of whoever used it before: `MATRIX_SOURCE_MPESLIDE`
had no baseline restore at all, unlike the pressure. `Timbre::lastSlide_` and
`setMatrixSlide()` now mirror the pressure baseline exactly, and
`preenNoteOnUpdateMatrix()` restores it. This was pre-existing for voices recycled by a
*different* channel or by a non-MPE note; R3 merely stopped masking it.

### R4 — reset paths cleared ownership but not decoder or voice state — **CONFIRMED + FIXED**

Timbre ownership was dropped in six places; the decoder state only in the constructor and
on member note off. And **`Voice::afterNewParamsLoad()` did not clear `mpeFreqOffset`** —
it resets the matrix sources, and the bend deliberately lives outside the matrix. The host
simulation had assumed it was cleared; **the simulation was wrong, not the requirement**,
and the real code was fixed rather than the test.

Fixed at the smallest hooks rather than scattered: `Voice::afterNewParamsLoad()`,
`Timbre::afterNewParamsLoad()` (also `lastSlide_`), MidiDecoder's own
`afterNewParamsLoad()` / `afterNewComboLoad()` listener hooks, CC 123 / CC 120 / CC 127
when they address the MPE timbre, and a configuration-change check inside
`getMpeTimbre()` that covers switching MPE off and on again and moving the zone without
needing a hook in the menu code.

**One deliberate non-change:** a *releasing* voice keeps its bend after an all-notes-off.
Zeroing it would make the release tail jump in pitch. The invariant that matters — the
next note on cannot inherit it — is guaranteed by `preenNoteOnUpdateMatrix()`, and is
tested. The `mpeFreqOffset == 0` assertion is therefore scoped to the parameter-load case,
where the voices really are re-initialised.

---

**MPE CORRECTNESS GATE: PASS FOR INDEPENDENT RE-REVIEW**

R1–R4 all CONFIRMED and fixed, plus the slide-baseline defect R3 exposed. Limitations
unchanged and stated plainly: Upper Zone is not implemented (an upper-zone MCM is
consumed without effect), RPN handling is deliberately minimal (§6), Lift is not
implemented (with the code reason), unison and arpeggiator remain unsupported
combinations, the four builds used a **substitute toolchain and are not release-valid**,
and **no hardware validation was performed**. Everything verifiable in the cloud — all
four targets building, zero new warnings, an exactly accounted memory delta, the
structural constraints (no new matrix source, no preset change, PolyAT contract and
editor protocol intact) and all eighteen simulated state gates — was verified.
