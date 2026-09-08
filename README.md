# PreenFM2 3.00 alpha firmware — VOSIM + editor protocol + PolyAT/MPE development

> ## ⚠ Experimental developer firmware — hardware validation of the new expression work is still pending
>
> This repository contains the PreenFM2 3.00-alpha VOSIM firmware plus the editor remote-store protocol and the current development work for true MIDI Polyphonic Key Pressure and Lower-Zone MPE.
>
> The existing 3.00-alpha editor-protocol build has already run successfully on a physical PreenFM2. The newer PolyAT/MPE branch has passed source review, host-side simulations and comparative cloud compile/link checks, but **has not yet been built with the historical release toolchain or flashed to hardware**.
>
> Do not flash the GCC-13 cloud binaries. The next release gate is a clean local build with `gcc-arm-none-eabi 4.7-2014q2` / GCC 4.7.4, followed by manual hardware validation.

<p align="center">
  <img src="docs/PreenFM2_2026.png" alt="PreenFM2 test hardware running the 3.00 alpha firmware" width="900" />
</p>

---

## Current development status — 2026-09-08

Current development branch:

```text
feature/full-mpe
```

Last source-reviewed firmware checkpoint before this documentation-only cleanup:

```text
4655c10efcf4eb3343984941403511ae7bbe6b6b
```

Current state:

- **3.00 Alpha / VOSIM / editor remote protocol:** retained.
- **True MIDI Polyphonic Key Pressure:** implemented and independently source-reviewed. Existing `AftT` matrix routes become note-local when MIDI Polyphonic Key Pressure is received; ordinary Channel Pressure keeps the timbre-wide behaviour.
- **Lower-Zone MPE:** implemented for the intended ROLI workflow:
  - Strike → Note-On velocity
  - Press → per-voice `AftT`
  - Glide → per-voice pitch bend
  - Slide → per-voice CC74 / `MPESLIDE`
- **Member pitch-bend range:** supported up to **±48 semitones**. Larger member RPN-0 values are clamped because the current `exp2_harm` frequency representation cannot reproduce ±96 without saturation.
- **Host verification:** `mpe_state_test.py` 325 checks, `polyat_state_test.py` 79 checks, `protocol_sim_test.py` 115 checks; these are simulations / structural tests, not firmware unit tests and not hardware tests.
- **Comparative cloud builds:** `pfm`, `pfmo`, `pfmcv`, `pfmcvo` compile/link with the GCC-13.2 substitute environment and no new warnings. These binaries are **not release-valid and must not be flashed**.

Known MPE limitations are documented rather than hidden: Lower Zone only, RPN 6 with `n=0` is consumed but does not deactivate MPE, manager-channel pitch-bend sensitivity is recorded but not applied as a semitone range, no Lift/release-velocity expression, and MPE with Unison or the arpeggiator is not supported.

Detailed reports:

- [`docs/POLYAT_IMPLEMENTATION_REPORT.md`](docs/POLYAT_IMPLEMENTATION_REPORT.md)
- [`docs/POLYAT_HARDWARE_TEST_CHECKLIST.md`](docs/POLYAT_HARDWARE_TEST_CHECKLIST.md)
- [`docs/MPE_IMPLEMENTATION_REPORT.md`](docs/MPE_IMPLEMENTATION_REPORT.md)

Hardware-test tools:

- `tools/polyat_test.py` — deterministic MIDI Polyphonic Key Pressure / Channel Pressure sender
- `tools/midi_monitor.py` — MIDI input monitor to verify what the controller actually transmits
- `tools/mpe_test.py` — deterministic Lower-Zone MPE sender for Press / Glide / Slide / sustain / voice-allocation scenarios

### Next gate

The next step is deliberately **not more feature work**:

1. check out the final `feature/full-mpe` source locally;
2. build cleanly with the historical `gcc-arm-none-eabi 4.7-2014q2` / GCC 4.7.4 toolchain;
3. build all four targets separately (`pfm`, `pfmo`, `pfmcv`, `pfmcvo`) with a clean build directory between targets;
4. record the exact output filename and SHA-256 of every resulting binary;
5. identify the correct hardware image for the physical PreenFM2;
6. flash manually — never automatically;
7. validate ordinary MIDI and Channel Aftertouch first, then PolyAT, then Lower-Zone MPE with LUMI / Seaboard RISE.

See [`docs/POLYAT_HARDWARE_TEST_CHECKLIST.md`](docs/POLYAT_HARDWARE_TEST_CHECKLIST.md) and §18 of [`docs/MPE_IMPLEMENTATION_REPORT.md`](docs/MPE_IMPLEMENTATION_REPORT.md).

---

## What this repository is

This repository started as a focused extension of the PreenFM2 VOSIM firmware: a MIDI protocol that lets PreenFM+ store the current edit buffer, query the current bank/preset position and detect firmware capability support. That 3.00-alpha base is still preserved.

The current development branch additionally carries the PolyAT and Lower-Zone MPE work described above. It remains a standalone firmware repository; it is not a GitHub fork or a submodule of the editor project. Original history, GPL headers and author attribution are retained.

> [!IMPORTANT]
> **Experimental AI-assisted development.** This tAUREON project explores the practical benefits and limits of AI-assisted work on audio and synthesizer software. Development has been human-directed with OpenAI Codex / ChatGPT and Anthropic Claude Code used in separate implementation and review roles. Treat the current expression branch as developer firmware until the historical-toolchain build and physical-hardware gates have passed.

| | |
|---|---|
| origin of the code | [`pvig/preenfm2`](https://github.com/pvig/preenfm2) |
| VOSIM base branch | `vosim` |
| VOSIM base commit | `6ed604a43636c00bfbac9613c8f5a79a7582dfa7` |
| editor-protocol baseline branch | `feature/editor-remote-store` |
| reviewed PolyAT checkpoint | `feature/true-poly-aftertouch` @ `f503e7fd34c1df887e421f782520275a7b807632` |
| current development branch | `feature/full-mpe` |
| firmware version family | `3.00 alpha` (pre-release; original base was `2.21b`) |

Remotes are set up as:

```text
upstream -> https://github.com/pvig/preenfm2.git
origin   -> https://github.com/sscheidl/preenfm2-vosim-editor-firmware.git
```

### Why the VOSIM branch

`pvig/preenfm2` `vosim` carries VOSIM algorithms 29–32 and the additional LFO shapes 6–8. They are part of the base and are preserved by the 3.00-alpha work. The reference material for the historical VOSIM build is under [`release/fw_2.21b_lfo_vosim/`](release/fw_2.21b_lfo_vosim/README.md).

---

## Editor remote protocol

The editor extension uses NRPN page 4, which older firmware silently ignores:

| NRPN page / LSB | command |
|---|---|
| 4 / 0 | capability query |
| 4 / 1 | current-position query |
| 4 / 2 | store current edit buffer to a bank/preset target |

Responses use page 4, LSB 64 and above, with explicit status codes. The protocol specification and the already prepared 3.00-alpha editor-protocol build are in [`release/editor-protocol-3.00alpha/`](release/editor-protocol-3.00alpha/).

[PreenFM+ 4.0.4](https://github.com/sscheidl/preenfm2-Editor) implements capability detection, position query, guarded Store and status reporting. On the device, `Receives:` must include NRPN for those requests.

Existing patch format and ordinary program/bank loading remain compatible with the underlying 2.21/VOSIM format.

---

## Building

The historical toolchain is [arm-gcc 4.7-2014q2](https://launchpad.net/gcc-arm-embedded/+milestone/4.7-2014-q2-update). It is not bundled in this repository.

`GCC_PATH` in the Makefile still points to the original author's environment, so normally place the historical toolchain on `PATH` and override the tools on the command line:

```bash
export PATH="/path/to/gcc-arm-none-eabi-4_7-2014q2/bin:$PATH"

MK='make CC=arm-none-eabi-c++ AS=arm-none-eabi-as NM=arm-none-eabi-nm \
        READELF=arm-none-eabi-readelf CP=arm-none-eabi-objcopy'

$MK clean && $MK pfm
$MK clean && $MK pfmo
$MK clean && $MK pfmcv
$MK clean && $MK pfmcvo
```

For the current hardware-validation build, also clear stale build outputs between targets and record the filename from `.binfirmware`; do not assume that the last `.bin` in `build/` belongs to the target just built.

Useful host checks:

```bash
python test/host/protocol_sim_test.py
python test/host/polyat_state_test.py
python test/host/mpe_state_test.py
python tools/polyat_test.py --self-test
python tools/mpe_test.py --self-test
```

These checks complement — and never replace — the historical-toolchain build and physical-hardware test.

---

## Authors and license

The PreenFM2 firmware is the work of **Xavier Hosxe**. The VOSIM branch used as the base is maintained by **pvig**. The tAUREON 3.00-alpha editor protocol, PolyAT and MPE development in this repository were produced through human-directed development with AI assistance.

The boot screen for the 3.00-alpha line credits `By Hosxe & tAUREON`.

GPL, as upstream. Original source-file copyright and GPL headers remain intact; the USB manufacturer string continues to identify the device as Xavier Hosxe's design.

---

## Upstream README summary

The official PreenFM2 firmware repository is [`Ixox/preenfm2`](https://github.com/Ixox/preenfm2). The upstream project uses ARM GCC 4.7 and provides the original hardware, firmware and flashing documentation. For first-time flashing and bootloader details, follow the upstream PreenFM2 instructions rather than treating this development README as a replacement for them.
