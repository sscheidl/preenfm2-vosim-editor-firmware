# PolyAT hardware test checklist

PreenFM2 3.00 alpha, branch `feature/true-poly-aftertouch`.
Fill this in on the local Windows machine. Copy the file per test session.

Implementation details: `docs/POLYAT_IMPLEMENTATION_REPORT.md`.
MIDI sender: `tools/polyat_test.py`. Controller inspection: `tools/midi_monitor.py`.

---

## 0. Session record

| field | value |
|---|---|
| date | |
| tester | |
| firmware branch | `feature/true-poly-aftertouch` |
| firmware commit SHA | |
| toolchain | gcc-arm-none-eabi 4.7-2014q2 / GCC 4.7.4 |
| toolchain archive SHA-256 | `a2fe8e910b451375c98d92a4e1952b51c712da3dc546924dbb0acc5af4cd603e` |
| build host / OS | |
| GNU Make version | |
| target flashed | `pfm` / `pfmo` / `pfmcv` / `pfmcvo` |
| binary filename | |
| binary SHA-256 | |
| binary size (bytes) | |
| PreenFM2 unit / serial | |
| MIDI controller | |
| controller pressure mode | poly / channel / both (confirm with `midi_monitor.py`) |
| MIDI interface / connection | DIN / USB, host or device port |
| preset bank used | |

**Before any test**, run `tools/midi_monitor.py --port <controller> --summary`, press a
key and lean on it. If the controller sends only channel pressure, none of the
polyphonic gates below actually test anything — fix the controller configuration first
and record what it turned out to be.

---

## 1. Build handoff: rebuilding with the historical toolchain

The cloud builds attached to the implementation report were produced with **GCC 13.2**,
not the documented compiler. **They are not release-valid and must not be flashed.**
Rebuild locally with GCC 4.7.4 before any hardware test.

### Method

Put the historical toolchain on `PATH` and override the tool variables on the command
line. The `Makefile` contains a hard-coded `GCC_PATH` that is deliberately **not**
edited or committed — the overrides win:

```
make <target> \
    CC=arm-none-eabi-c++ \
    AS=arm-none-eabi-as \
    NM=arm-none-eabi-nm \
    READELF=arm-none-eabi-readelf \
    CP=arm-none-eabi-objcopy
```

### Required sequence — clean between every target

```
make clean && make pfm     CC=... AS=... NM=... READELF=... CP=...
make clean && make pfmo    CC=... AS=... NM=... READELF=... CP=...
make clean && make pfmcv   CC=... AS=... NM=... READELF=... CP=...
make clean && make pfmcvo  CC=... AS=... NM=... READELF=... CP=...
```

### Warnings — these cost a build cycle if ignored

- **`make clean` does not remove every previous output.** It deletes `build/*.o` only.
  `build/*.bin`, `build/*.elf` and the symbol files from a previous target **survive**.
- **The four targets write four different filenames**, so a stale file from an earlier
  target sits right next to the fresh one:

  | target | binary |
  |---|---|
  | `pfm` | `build/p2_3.00alpha.bin` |
  | `pfmo` | `build/p2_3.00alphao.bin` |
  | `pfmcv` | `build/p2_cv_3.00alpha.bin` |
  | `pfmcvo` | `build/p2_cv_3.00alphao.bin` |

  Picking "the .bin in build/" therefore picks the wrong one. This actually happened
  during the cloud build and produced three wrong size figures before it was caught.
- **Do not trust a stale binary.** Either `rm -rf build/*` between targets, or read the
  filename the Makefile itself recorded in `.binfirmware` after each build.
- **Hash every final binary** and write it into §0 above:
  `certutil -hashfile build\p2_3.00alpha.bin SHA256` (Windows) or
  `sha256sum build/p2_3.00alpha.bin`.
- **Never flash the GCC 13 cloud binaries.**
- Do not use `installdfu` or any other flash target from inside a build script.

### Expected shape of the result

Firmware 3.00 alpha built with GCC 4.7.4 was 415 120 bytes before this feature. The
PolyAT change adds roughly 0.7 KB of `.text` and exactly **16 bytes** of CCMRAM
(`lastChannelAfterTouch_` × 4 timbres). Record the real figures; the historical CCMRAM
headroom was 6 620 bytes, so a surprise there is worth investigating before flashing.

---

## 2. Test gates

Mark each **PASS** / **FAIL** / **NOT TESTED**. A gate with no observation written down
counts as NOT TESTED.

Scenario letters in brackets refer to `tools/polyat_test.py --scenario X`.

| # | gate | expected | result | observation / reproduction |
|---|---|---|---|---|
| 1 | boot | firmware boots, version screen correct | | |
| 2 | existing presets load | all banks browse and load, matrix rows display normally | | |
| 3 | **Channel AT regression** | channel aftertouch behaves exactly as 3.00 alpha did | | |
| 4 | `AftT → Mix*` | channel aftertouch sweeps the mix as before | | |
| 5 | `AftT → o*Fq` | channel aftertouch sweeps pitch as before | | |
| 6 | 3-note independent PolyAT [B] | each note responds alone, the other two do not move | | |
| 7 | PolyAT 0 / 127 [A] | full range reached, no wrap, no jump at the ends | | |
| 8 | fast sweep [E] | smooth follow, no stuck value, no hang | | |
| 9 | Channel AT after PolyAT [D] | **all** voices jump to the channel value | | |
| 10 | Note-On after Channel AT [C] | the new note **starts** at the channel value | | |
| 11 | repeated note [F] | a new strike restarts at the channel baseline, not at the old poly value | | |
| 12 | **voice stealing** | press C4 hard, then play enough notes to steal that voice: the new note starts at the baseline, **not** at C4's pressure | | |
| 13 | sustain [G] | held voice still follows its poly pressure; channel pressure broadcasts over it; later reuse restores the baseline | | |
| 14 | **Unison** (`playMode = unison`, `numberOfVoice > 1`) | the whole stack follows together, no drift between stack members | | |
| 15 | Mono (`numberOfVoice = 1`) | poly pressure works on the single voice | | |
| 16 | **Glide** — known limitation | poly pressure addresses the *pre-glide* note. Confirm the documented behaviour, do **not** file it as a defect (report §10) | | |
| 17 | Arpeggiator, `octave = 1` | poly pressure works normally | | |
| 18 | **Arpeggiator, `octave > 1`** — known limitation | poly pressure does **not** follow the transposed notes (report §11). Confirm, do not file as a defect | | |
| 19 | Timbres 1–4 independently | poly pressure on one timbre's channel leaves the other three untouched | | |
| 20 | Global MIDI channel | poly pressure on the global channel reaches all four timbres | | |
| 21 | Current Instrument channel | poly pressure reaches the currently selected timbre only | | |
| 22 | Omni | poly pressure from any channel reaches an omni timbre | | |
| 23 | rapid multi-note stress [H] | no stuck note, no dropout, no audible CPU overload, no hang | | |
| 24 | **zipper / click comparison** | drive `AftT → Mix*` from channel aftertouch, then the same routing from poly aftertouch. Both are 7-bit into an unsmoothed destination. The question is only whether poly is **worse** than the existing channel behaviour — smoothing is explicitly out of scope | | |
| 25 | editor MIDI pass-through (if convenient) | the editor still connects, stores and reads; `0xAn` passes through to the hardware | | |

### Notes on the two "known limitation" gates

Gates 16 and 18 are expected to behave as described. They are in the list so the
behaviour is confirmed and recorded rather than discovered later and mistaken for a
regression. If either behaves **differently from the documented limitation**, that *is*
a finding worth reporting.

### What would be a real failure

- gate 3, 4 or 5 differing from 3.00 alpha — the regression guarantee is broken;
- gate 12 showing the new note inheriting the old note's pressure — the voice-reuse
  restore is not working;
- gate 9 leaving a voice behind on its poly value — the broadcast is not reaching
  every voice;
- gate 14 with the unison stack drifting apart — the "all matching voices" rule is not
  being applied;
- gate 19 showing cross-talk between timbres;
- any stuck note or hang in gate 23.

---

## 3. Result summary

| | |
|---|---|
| gates PASS | |
| gates FAIL | |
| gates NOT TESTED | |
| verdict | |

Free-form notes, recordings, photos:

---

## 4. Out of scope for this session

- **Smoothing.** The PreenFM2 does not filter mix or pan; a 7-bit step is audible at the
  ~1.46 kHz matrix rate. This is pre-existing and already true for channel aftertouch.
  Gate 24 only records whether poly makes it worse. Any fix is a separate branch.
- **MPE.** Not implemented. The next firmware goal, on its own branch, with ROLI LUMI
  for a quick test and a Seaboard RISE as the expressive reference.
- **Glide and arpeggiator redesign.** See gates 16 and 18.
