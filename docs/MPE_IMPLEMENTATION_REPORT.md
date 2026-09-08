# MPE — implementation report

PreenFM2 firmware 3.00 alpha, branch `feature/full-mpe`.

**ARCHITECTURE GATE: PASS** — a coherent zone/timbre mapping is derivable from the
existing PreenFM2 routing; §3 gives the derivation. Implementation followed.

**Two rounds of independent review raised seven correctness findings, R1–R7. All seven
are CONFIRMED and fixed; §19 and §20 record them and the two second-order defects they
exposed.** Claims that the fixes invalidated have been corrected in place.

**This is not "full MPE".** Upper Zone is not implemented, RPN 6 with a member count of
zero is not honoured, and the manager pitch bend range cannot be applied. §17 lists the
standards limitations.

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
| `MPE bend st:` | `mpebend` | 0..96 semitones, the **member** default | 48 |

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

**Only channels of the configured zone** feed the RPN state machine (finding **R6**).
A channel outside the zone is an ordinary PreenFM2 channel and keeps its existing
meaning for CC 100/101/6/38.

| RPN | manager channel | member channel | outside the zone |
|---|---|---|---|
| 0 — pitch bend sensitivity | sets the **manager** range (recorded, see §8) | sets **that member channel's** range, 0..96 semitones | not intercepted |
| 6 — MPE Configuration Message | sets `MPE members` (1..15), resynchronises the zone and restores the MPE 1.1 defaults: manager 2, every member 48 | ignored — only the manager may resize the zone | not intercepted |
| anything else | consumed, ignored | consumed, ignored | not intercepted |

`CC 101`/`CC 100` are consumed on zone channels while MPE is on. `CC 6`/`CC 38` are
consumed **only while an RPN is actually selected** on that channel; RPN Null (127/127)
is the initial state, so ordinary data entry keeps reaching the NRPN path and the editor
remote protocol is untouched — `decodeNrpn()` and every editor constant are byte
identical to the reviewed PolyAT branch.

**RPN selection is sticky**, as MIDI defines it: it is initialised once at construction
and is *not* cleared by a note off or a reset. Clearing it would drop the selection
between the data entry MSB and LSB of a configuration message, and the LSB would fall
through to the NRPN path (§20, R7).

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

**The range is per channel** (`mpeBendRange[16]`), as MPE 1.1 requires: RPN 0 addressed
to the manager channel sets the manager range, RPN 0 addressed to a member channel sets
*that* member channel's range, and an MPE Configuration Message restores manager 2 /
member 48. The menu `MPE bend st` seeds the member ranges until a controller negotiates
its own. Finding **R5** (§20) showed that a single shared value let a manager RPN 0 of 2
semitones become the member Glide range.

**The manager range is recorded but not applied.** Manager pitch bend goes through
`MATRIX_SOURCE_PITCHBEND`, whose range is the preset's matrix row multiplier, so this
firmware has no manager bend-range parameter for it to reach. Stated rather than faked.

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
| 6 | `d2c73fb` | R1–R4 regression tests and report update |
| 7 | `9682f55` | Review R5, R6 and R7: MPE 1.1 bend sensitivity, RPN scope and zone resize |
| 8 | branch HEAD | R5–R7 regression tests and report update |

Commits 1–6 were reviewed and are **not** rewritten.

Commit 1 compiles and changes no behaviour (nothing calls the new code yet); commit 2
activates it. A finer split was considered and rejected as artificial: routing without
identity does not compile, and identity with routing but without the three expression
dimensions is not a state anyone would want to bisect to.

The reviewed PolyAT history was not rewritten.

## 15. Host tests

`test/host/mpe_state_test.py` — **290 checks in 21 scenarios, all pass.**

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
| **R2** | RPN 6 sets the member count and RPN 0 the bend range; no arp CC, no NRPN data entry, no ordinary message switch is reached; after RPN Null CC 6/38 go back to the NRPN path; an upper-zone MCM on ch16 that falls *inside* the configured lower zone is consumed without resizing it (outside the zone is R6's documented limitation);  with MPE off CC 100/101/6/38 keep their ordinary meanings | pass |
| **R3** | manager pressure 70 and slide 90, member note on with nothing sent: the voice keeps 70 and 90; an explicit member 0 then overrides both | pass |
| **R4** | CC 123 / CC 120 / CC 127 / parameter load / MPE off-and-on / moving the zone all leave no stale pressure, slide, bend, seen flag or ownership; `mpeFreqOffset` is 0 after a parameter load | pass |
| **R5** | the exact sequence asked for: manager RPN 0 = 2, then member RPN 0 = 48 → member Glide uses **48** (24.0 freqHarm units), not 2 (which would be 1.0); each member channel keeps its own range; an MCM restores 2 / 48; 96 semitones is accepted | pass |
| **R6** | the exact sequence asked for: lower zone manager 1 with 4 members, ordinary timbre on channel 10, CC 100/101 there → they keep their ordinary meaning, the channel never enters the MPE RPN state, CC 6/38 still reach the NRPN path, and RPN inside the zone is still consumed | pass |
| **R7** | the exact sequence asked for: 15 members, expression on channel 16, shrink to 4, expand to 15, new channel-16 note → no old pressure, slide, bend or ownership returns; the same through RPN 6; a bend-setting change also resynchronises | pass |

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
| the single shared bend setting restored | R5: RPN 0 still writes the single shared bend setting |
| the R6 zone gate removed | R6: RPN is consumed on channels outside the configured zone |
| member count dropped from the change detection | R7: the member count and bend setting are not part of the change detection |
| RPN selection cleared on reset again | R4: a reset clears the RPN selection, which breaks a configuration message mid sequence |

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
| `pfm` | **PASS** | 361 152 | 306 264 | 54 888 | 101 380 | 102 |
| `pfmo` | **PASS** | 361 152 | 306 264 | 54 888 | 101 380 | 102 |
| `pfmcv` | **PASS** | 364 048 | 309 144 | 54 904 | 101 716 | 103 |
| `pfmcvo` | **PASS** | 364 048 | 309 144 | 54 904 | 101 716 | 103 |

`pfm`/`pfmo` byte-identical (md5 `5cb2740a…`), `pfmcv`/`pfmcvo` byte-identical
(md5 `9803025c…`).

Step by step, so each correctness round is attributable:

| revision | `pfm` `.bin` | `.bss` | CCMRAM | what it added |
|---|---|---|---|---|
| `115d0f2` first MPE | 359 104 | `0x10678` | `0x851c` | — |
| `d2c73fb` R1–R4 | 360 384 | `0x106c0` | `0x852c` | +72 `.bss` (seen flags 32, RPN state 32, config detection 8), +16 CCM (`lastSlide_` × 4) |
| branch HEAD R5–R7 | 361 152 | `0x106d8` | `0x852c` | +24 `.bss` (`mpeBendRange[16]` = 16, `mpeLastMembers` + `mpeLastBend` = 8) |

Every byte is accounted for; `.data` and CCMRAM are unchanged by the R5–R7 round.

### Delta against the PolyAT base

| target | `.bin` | `.text` | `.data` | `.bss` |
|---|---|---|---|---|
| `pfm` / `pfmo` | +4 800 | +4 712 | +88 | +432 |
| `pfmcv` / `pfmcvo` | +4 664 | +4 576 | +88 | +432 |

Section detail (`pfm`), and the 320 bytes account for themselves exactly:

| section | PolyAT | branch HEAD | delta |
|---|---|---|---|
| `.data` | `0x7478` | `0x74d0` | +88, the two const name tables |
| `.bss` | `0x105b8` | `0x106d8` | +288, decoder state |
| `.ccm` | `0x6198` | `0x6198` | 0 |
| `.ccmnoload` (CCMRAM) | `0x849c` | `0x852c` | **+144** |

CCMRAM total 59 076 / 65 536 under this toolchain, **+144** against the PolyAT base. The
historical GCC 4.7.4 headroom was 6 620 bytes; +144 is 2.2 % of it. **Re-measure with
GCC 4.7.4 before any release.**

### Warnings delta: none

Counts identical per target (102/102/103/103) and the warning-kind histograms are
byte-identical to the PolyAT build **and** to both earlier MPE builds for all four.
Exactly one warning is *located* in a file this feature touches —
`SynthState.cpp:1571 control reaches end of non-void function` — and it is
**pre-existing**: on the PolyAT branch it is the same warning in the same function at
line 1566, moved only because four default assignments were added above it.

## 17. Known limitations

These are the reasons the header does not call this "full MPE".

**Standards limitations**

1. **Upper Zone is not implemented** (§3). The shape is lower-zone only: manager channel
   plus ascending member channels. An MPE Configuration Message addressed to the Upper
   Zone manager (channel 16) is **not** intercepted unless channel 16 happens to be
   inside the configured Lower Zone — see limitation 4.
2. **RPN 6 with a member count of `n = 0` is consumed but not honoured.** MPE 1.1 defines
   `n = 0` as *deactivate the zone*: the manager channel reverts to an ordinary MIDI
   channel and every member channel is released. This implementation clamps the count to
   `1..15` (`MidiDecoder::mpeConsumeRpn()`), so a controller that switches MPE off by
   sending RPN 6 = 0 leaves the PreenFM2 in MPE mode. **Workaround: `MPE inst: Off` in
   the menu**, which is the deactivation path this firmware actually supports and which
   does perform the full teardown (§R4, §R7).
   Honouring `n = 0` properly is not a one-line clamp change: "MPE off" is a menu-owned
   setting read through `getMpeTimbre()`, so a runtime deactivation would either have to
   write back into `SynthState` parameters from the MIDI thread — a cross-thread write to
   a menu-visible value, with the display and the config file to keep consistent — or
   introduce a second, decoder-local "active" flag that the menu does not know about and
   that nothing in the UI could show. Both were judged larger and riskier than the
   remaining benefit, so the limitation is documented rather than papered over.
3. **The manager pitch bend range is recorded but never applied** (§8). RPN 0 on the
   manager channel stores `mpeBendRange[manager]` and MPE 1.1's default of 2 semitones is
   seeded there, but manager-channel pitch bend still runs the firmware's existing path:
   `MATRIX_SOURCE_PITCHBEND`, whose range is the preset's matrix row multiplier, not a
   semitone setting. There is therefore no manager bend range in this firmware to set.
   Only member-channel bend uses `mpeBendRange[]`. This keeps ordinary pitch bend
   byte-for-byte unchanged, at the cost of the manager range being informational.
4. **RPN outside the configured zone is not swallowed** (finding **R6**, §20). CC
   100/101/6/38 are intercepted **only** on the manager and member channels of the
   configured Lower Zone. Outside it they keep their existing PreenFM2 meanings
   (`CC_ARP_CLOCK`, `CC_ARP_DIRECTION`, NRPN data entry). An earlier draft of this report
   claimed an Upper-Zone MCM was "consumed harmlessly"; that was only true because RPN
   was intercepted on **every** channel, which meant hijacking ordinary channels the user
   had not given to MPE. The narrower rule is the correct trade, and the honest statement
   of it is: **an RPN sent outside the zone can still reach the arpeggiator or the NRPN
   state machine, exactly as it did before this branch.**
5. **Lift / note-off velocity is not implemented.** Audited as §1 of the assignment
   asked: `Voice::noteOff()` takes no velocity and the six envelopes have no release
   velocity input, so there is no existing parameter for it to reach. Adding one would
   mean new envelope state and a new matrix source. Not added to claim spec coverage.

**Implementation limitations**

6. **Unison + MPE is not a supported combination.** In unison `preenNoteOn()` starts the
   whole stack but reports one voice, so only that voice would follow member expression.
   Use the MPE timbre in ordinary polyphonic mode.
7. **Arpeggiator + MPE is not meaningful** — the arpeggiator transposes notes
   (`Timbre.cpp:5330`) and the member channel association is made at note on.
8. **Member polyphony is the MPE timbre's voice count**, up to 14; a controller sending
   15 member channels will steal voices.
9. **A timbre configured inside the zone is shadowed** while MPE is on (§3) — intended,
   but it will look like a dead timbre if a user forgets.
10. **Builds are not release-valid** (§16) and **no hardware validation was performed**.

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
before anything else sees them and only while MPE is on. RPN Null keeps ordinary data
entry working, so the editor protocol is untouched — verified byte-identical.

*(Two later findings narrowed this rule: **R6** restricts the interception to channels of
the configured zone, and **R5** lets a member channel negotiate its own bend range while
only the manager may resize the zone. §6 states the current rule.)*

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

## 20. Independent re-review findings R5–R7

Three residual issues, all verified against the real source and against the MPE 1.1
specification before any change. **All three CONFIRMED.** One commit, `9682f55`, on top
of the reviewed `d2c73fb`; the three reviewed MPE commits were not rewritten.

### R5 — one shared pitch bend sensitivity for manager and members — **CONFIRMED + FIXED**

`mpeConsumeRpn()` wrote RPN 0 into `midiConfigValue[MIDICONFIG_MPE_BEND]`, the single
menu setting, and `mpeEventReceived()` read that same single value as the member Glide
range. So the exact sequence the review named reproduced:

| step | message | old behaviour | MPE 1.1 |
|---|---|---|---|
| 1 | manager ch 1: RPN 0 = 2 | `MPE bend st` := 2 | manager range := 2 |
| 2 | member ch 2: RPN 0 = 48 | `MPE bend st` := 48 | ch 2 range := 48 |
| 3 | member ch 2 bend | 48 st — right by luck | 48 st |
| 3′ | steps 1 and 2 swapped | **2 st** — Glide reduced to a semitone-ish wobble | 48 st |

The old code also clamped RPN 0 to 48, while MPE 1.1 allows 0..96, and it accepted RPN 0
**only on the manager channel** — a member channel negotiating its own range was
consumed and dropped.

Fixed with `uint8_t mpeBendRange[16]`, one entry per channel. RPN 0 writes only the
channel it arrived on, over the full 0..96 range. `mpeEventReceived()` reads
`mpeBendRange[channel]`, so a member note bends by *its own* channel's range. An MPE
Configuration Message restores the MPE 1.1 defaults: manager 2, every member channel 48.
The `MPE bend st` menu setting seeds the member ranges until a controller negotiates.

**A manager RPN 0 can no longer change the member Glide range** — which was the
requirement.

The manager range is now recorded rather than applied, because this firmware has no
manager bend range to apply (§17 limitation 3). Recorded and stated, not faked.

### R6 — RPN was intercepted on every channel — **CONFIRMED + FIXED**

`midiEventReceived()` called `mpeConsumeRpn()` for every control change while MPE was on,
*before* any membership test. CC 100/101/6/38 on a channel that had nothing to do with the
zone were swallowed and lost their existing PreenFM2 meanings — `CC_ARP_CLOCK`,
`CC_ARP_DIRECTION` and NRPN data entry — on a timbre the user never gave to MPE.

Fixed by gating the call: `isMpeManagerChannel(channel) || isMpeMemberChannel(channel)`.

**The honest consequence, as required:** an Upper-Zone MPE Configuration Message on
channel 16 is *no longer* consumed unless channel 16 is inside the configured Lower Zone.
§19's earlier claim that it was "consumed harmlessly" was only true at the price of
hijacking channels outside the zone. That trade is not worth making, so the claim is
withdrawn and replaced by §17 limitation 4, which says plainly that an RPN outside the
zone reaches the same places it reached before this branch.

### R7 — a member-count change was not treated as a zone change — **CONFIRMED + FIXED**

`getMpeTimbre()`'s change detection compared only the timbre and the manager channel.
Shrinking the zone — from the menu, or via RPN 6 — left the dropped channels' decoder
expression, their `mpeVoiceOfChannel_` ownership in the timbre and any negotiated bend
range in place. A later note that landed on a recycled voice could pick them up.

Fixed by making the member count and the bend setting part of the detected configuration
(`mpeLastMembers`, `mpeLastBend`) and by concentrating the teardown in one new function,
`mpeSyncZoneConfig()`: snapshot the configuration, drop decoder expression, reseed the
bend ranges, and clear the member-channel ownership on all four timbres. RPN 6 calls it
directly, so a resize is clean immediately rather than one MIDI event later.

**RPN 6 with `n = 0`, verified against MPE 1.1.** The specification defines it as
*deactivate the zone*. It is **not** honoured here, and implementing it is neither small
nor safe: "MPE on" is a menu-owned `SynthState` setting, so runtime deactivation means
either writing a menu-visible, display-backed, config-file-persisted value from the MIDI
thread, or adding a decoder-local active flag the UI cannot show. Per the instruction, it
is documented as a **remaining standards limitation** (§17 limitation 2) and this report
**does not call the implementation "full MPE"** — the header says so first.

### Second-order defects this round exposed

**1. The zone resync cleared the RPN selection mid-message.** `mpeForgetAllChannelState()`
reset `mpeRpnMsb/Lsb` to Null. Once RPN 6 called `mpeSyncZoneConfig()`, the selection was
cleared *between* the data entry MSB (which carries the member count) and the data entry
LSB of that very configuration message — so the LSB was no longer recognised as RPN data
and **fell through to the NRPN path**, exactly the hazard R2 was fixed to close.

RPN selection is sticky in MIDI: it survives notes and resets and is replaced only by the
next CC 101/100 pair. It is now initialised once, in the constructor, and never cleared by
a note off, a reset or a zone change. Tested as gate R7-c.

**2. `MidiDecoder::synth` was never initialised.** `mpeSyncZoneConfig()` reaches through
it to clear the timbres' ownership, and it can now run before `setSynth()` — the first
`getMpeTimbre()` happens on the first MIDI event, `setSynth()` later in start-up order.
`MidiDecoder` lives in `.bss` so it was zero in practice, but that is an accident of the
link, not a guarantee. Explicitly null-initialised in the constructor, with a
`likely(this->synth != 0)` guard at the call site.

---

**MPE CORRECTNESS GATE: PASS FOR INDEPENDENT RE-REVIEW**

Seven findings over two review rounds — R1–R4 (§19) and R5–R7 (§20) — all CONFIRMED
against the real source and all fixed, plus the three second-order defects they exposed
(the missing slide baseline, the RPN selection cleared mid-message, and the uninitialised
`synth` pointer). Verified in the cloud: 290 checks over 21 scenarios in
`test/host/mpe_state_test.py`, 79 in `polyat_state_test.py` and 115 in
`protocol_sim_test.py`, all four targets (`pfm`, `pfmo`, `pfmcv`, `pfmcvo`) building
clean, **zero new warnings** and an exactly accounted `.bss` delta of +24 bytes, with the
structural constraints holding — no matrix source added, no preset format change, the
PolyAT contract and the editor remote protocol byte-identical.

**This is not "full MPE", and the report does not claim it is.** The standards
limitations are §17, items 1–5: no Upper Zone; RPN 6 `n = 0` consumed but not honoured;
the manager pitch bend range recorded but not applicable; RPN outside the configured zone
deliberately not intercepted; no Lift. Unison and the arpeggiator remain unsupported
combinations. The four builds used a **substitute toolchain (GCC 13.2) and are not
release-valid**, and **no hardware validation was performed** — §18 is the plan for it.
