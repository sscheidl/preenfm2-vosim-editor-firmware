# Changelog, editor protocol pre-release

## 3.00 alpha — 2026-08-19 — experimental pre-release, not hardware tested

Base: `pvig/preenfm2` branch `vosim`, commit `6ed604a43636c00bfbac9613c8f5a79a7582dfa7`
(firmware `2.21b`). Work commit `f7cdfcf444f3442be3e0a823ef25d66b316f1dbf`.

### Added

- **Editor remote protocol, version 1, on NRPN page 4.** Three requests, eight responses.
  See `EDITOR_PROTOCOL.md` for the byte level specification.
  - `CAPABILITY_QUERY` — reports protocol version and capability bits, so an editor can
    tell a firmware with the protocol apart from one without it.
  - `POSITION_QUERY` — reports bank type, zero based bank, zero based preset, and whether
    the position is known.
  - `STORE` — writes the current edit buffer of the addressed timbre into
    `target = (bank << 7) | preset`. No arm/commit step, no confirmation dialog, no button
    press: a complete, valid command writes immediately.
- **Status responses for every request**, with distinct codes for success, bank absent or
  not writable, invalid slot, ambiguous midi channel, storage error, and protocol error.
  Success is only reported after the write actually completed.
- `test/host/protocol_sim_test.py` — a simulation of the decode path that reads its
  constants out of `src/midi/MidiDecoder.h` and the bank count out of
  `src/filesystem/PreenFMFileType.h`, so the model cannot drift from the firmware. It also
  asserts against the source text of `controlChange()`, so removing the value invalidation
  from CC 96, CC 97 or the dispatch path fails the test instead of silently passing.
  28 cases, 115 checks. Every new assertion was checked by negative control. Not a hardware
  test.

### Fixed during development, before any hardware test

Two defects of the same class were found and closed in the store guard. Both allowed a
write to a preset slot that no received byte had addressed, and both answered
`EDITOR_STATUS_OK`. Details and the review failure behind them: `FIRMWARE_SAFETY_REVIEW.md`
section 4 and section 12.

- After a completed page 4 command, the value stayed valid, so a **lone CC 38** re-entered
  the store handler and wrote a neighbouring slot. The value is now consumed after every
  page 4 dispatch, on every addressed timbre.
- **CC 96 and CC 97**, the NRPN increment and decrement, set the dispatch flag without
  carrying a data entry byte, deriving the preset number from an unrelated earlier NRPN.
  They now invalidate the value and can never reach the store.

Two binaries produced during development carry these defects and must not be flashed:
`5fe73ed8…f0dc` and `7d77d70d…33df`.

### Changed

- `PatchBank::savePreenFMPatch()` returns `int` instead of `void`, so a failed write can be
  detected. It reports the file system result of both of its writes, the patch and the zero
  padding. Its two existing callers ignore the value and behave exactly as before.
- `SynthState::loadPreenFMPatchFromMidi()` now also sets `fullState.preenFMBank` when a
  regular patch bank is loaded. It previously updated only `preenFMBankNumber` and
  `preenFMPresetNumber`, which left the remembered position incomplete after a program
  change. **This is a bug fix, independent of the new protocol.**
- Version `2.21b` -> `3.00 alpha`. The boot screen shows `preenfm2 v3.00 alpha`, exactly
  the 20 columns the display has, and the menu version item shows `3.00 alpha`.
- `Makefile` now separates the display string from the file name component, because the
  display string contains a space and the same value ends up in build paths:
  `PFM2_VERSION_NUMBER=3.00alpha` for file names, `PFM2_VERSION_DISPLAY=3.00 alpha` for the
  display, and `PFM2_VERSION` single quoted so the compiler receives one argument.
- Boot author line `"     By Xavier Hosxe"` -> `"  By Hosxe & tAUREON"`. Xavier Hosxe keeps
  his attribution as the original author, here and in every GPL header, in the USB
  manufacturer string and in the README; this build's author is credited alongside him. The
  line stays exactly 20 characters because `MCP4922_screenBoot()` prints it column by column
  without a terminator check.

### Unchanged, verified

- Loading over CC 0, CC 32 and program change: byte for byte the old path.
- The full parameter dump over NRPN 127/127.
- Every existing parameter NRPN, preset name NRPN and step sequencer NRPN.
- All CC handling.
- The patch storage format. Patches written by this firmware stay readable by 2.21 and the
  other way round.
- Synthesis, the VOSIM algorithms 29-32, the LFO shapes 6-8, filters and the audio engine.
  No synthesis source file is in the diff.
- No automatic dump after a program change, and no new message after a load.

### Known limits

- Only regular preenfm patch banks (bank type 0) can be written. Combo and DX7 banks are
  refused and are not reachable through the protocol.
- At most 64 bank files are indexed, so a store to bank 64..127 returns status 2 rather
  than writing. The highest reachable slot is bank 63, preset 127.
- The protocol needs `Receives: NRPN` or `Receives: CC & NRPN`. With NRPN receive off,
  requests are ignored and nothing is answered.
- Responses ignore the `Send:` setting on purpose, so a query is always answered. They only
  use NRPN page 4, which nothing else uses.
- Responses reach USB only when `USB midi:` is `In/Out`. DIN midi out always carries them.
- A store performs a blocking usb write from the main loop, so an audible gap is possible
  while notes are sounding. This is the same behaviour the existing menu save has.
- `pfmo` is byte identical to `pfm`. `-DOVERCLOCK` is passed by the `Makefile` but is not
  referenced anywhere in the vosim sources. This is true of the unmodified base as well.

### Not done

- No editor change. `Preen FM VST3 Editor` was read as a reference only and was not
  modified.
- No hardware flashing, no bootloader change, no CV build.
- No public release, no change of repository visibility.

### Open, hardware only

Writing to a real usb key and reading the slot back, audio behaviour during a store, usb
key failure modes, the boot and menu rendering of the new version string, and confirming
the VOSIM algorithms and LFO shapes still sound correct.

**Do not flash until explicitly released.** Back up the usb key first, and aim the first
store at a throwaway bank.
