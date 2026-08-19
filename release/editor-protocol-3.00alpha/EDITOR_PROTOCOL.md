# PreenFM2 editor remote protocol, version 1

Protocol version 1, introduced in firmware `3.00 alpha`.
Base `pvig/preenfm2` branch `vosim`, commit `6ed604a43636c00bfbac9613c8f5a79a7582dfa7`.

The protocol version is independent of the firmware version and stays at 1 until the wire
format changes.

This protocol lets an editor ask the firmware what it supports, ask where it currently is
inside the patch banks, and write the current edit buffer straight into a bank slot. It is
purely additive: no existing parameter, command or address changes meaning.

---

## 1. Address space and why it is free

Every command lives on a single NRPN page, selected by the NRPN parameter MSB:

```
EDITOR_NRPN_PAGE = 4          NRPN parameter numbers 512 .. 639
```

`MidiDecoder::decodeNrpn()` has always dispatched on the parameter MSB only:

| parameter MSB | meaning in every existing firmware |
|---|---|
| 0, 1 | synth parameters, plus the preset name letters at parameter 228..239 |
| 2, 3 | step sequencer 1 and 2 steps |
| 127 with LSB 127 | request a full parameter dump |
| **everything else** | **falls through, no `else` branch, no side effect** |

Page 4 therefore collides with nothing:

- not with any synth parameter (pages 0 and 1),
- not with the preset name NRPNs (page 1, parameter 228..239),
- not with the step sequencer addresses (pages 2 and 3),
- not with the full dump command 16383 (page 127, LSB 127),
- not with the editor side numbering in `Plugin/Source/PreenNrpn.h`, whose highest firmware
  facing parameter is 399 and whose UI internal number 2044 is never sent to the firmware.

`test/host/protocol_sim_test.py` re-checks these ranges mechanically against the constants
in `src/midi/MidiDecoder.h`.

---

## 2. Wire format

Every request and every response is one standard 4 message NRPN sequence. With `c` as the
zero based midi channel:

```
Bc 63 04        CC 99  = 4          NRPN parameter MSB, the editor page
Bc 62 ll        CC 98  = ll         NRPN parameter LSB, the command or response id
Bc 06 mm        CC  6  = mm         value MSB, bits 13..7
Bc 26 vv        CC 38  = vv         value LSB, bits 6..0
```

The 14 bit value is `value = (mm << 7) | vv`, range 0..16383. The firmware acts on CC 38,
which is what completes the sequence, exactly as it does for ordinary parameter NRPNs.
Running status is accepted, the byte level decoder is unchanged.

### Completeness rule

A store only executes on a **CC 38 that followed a CC 6**. Specifically:

- CC 99 starts a new NRPN and invalidates the previous value;
- CC 6 makes the value msb fresh;
- CC 38 completes the sequence and dispatches;
- once a page 4 command has been dispatched, its value is consumed. A second CC 38 alone
  will **not** repeat it, it answers status 5;
- the NRPN increment and decrement messages **CC 96 and CC 97 never store**. They carry no
  data entry byte and derive the value from whatever was there before, so on page 4 they
  always answer status 5. Use them for ordinary parameters only.

Resending only a fresh `CC 6` + `CC 38` pair, keeping the previously selected page and
command, is a valid way to issue another store. Resending all four messages is also valid
and is what an editor should normally do.

Queries are unaffected by this rule: their value is ignored.

---

## 3. Requests, editor to firmware

| LSB | command | value |
|---:|---|---|
| 0 | `CAPABILITY_QUERY` | ignored, send 0 |
| 1 | `POSITION_QUERY` | ignored, send 0 |
| 2 | `STORE` | `target = (bank << 7) \| preset` |

`bank` and `preset` are both **zero based**, 0..127 each.

Requests must be sent on a midi channel that maps to exactly one timbre, see section 7.

---

## 4. Responses, firmware to editor

Responses use LSB 64 and above. That range is disjoint from the request range, so a
response fed back into the input by midi thru or by an editor echo is never decoded as a
request and can never start a midi loop.

| LSB | response | value |
|---:|---|---|
| 64 | `PROTOCOL_VERSION` | `1` |
| 65 | `CAPABILITIES` | bit 0 store supported, bit 1 position query supported. Currently `3` |
| 66 | `POSITION_BANKTYPE` | `0` = regular preenfm patch bank |
| 67 | `POSITION_BANK` | zero based bank number |
| 68 | `POSITION_PRESET` | zero based preset number |
| 69 | `POSITION_VALID` | `1` when a patch bank is actually selected, else `0` |
| 70 | `STORE_STATUS` | status code, see section 5 |
| 71 | `STORE_TARGET` | the 14 bit target the firmware acted on |

Response channel: the midi channel configured for the addressed timbre
(`Midi ch. 1..4` in the menu). When that setting is `All`, the response goes out on
channel 1. This matches what the existing full dump already does.

---

## 5. Status codes, response LSB 70

| code | meaning |
|---:|---|
| 0 | success, the patch is written |
| 1 | target bank absent or not writable (`FILE_EMPTY` or `FILE_READ_ONLY`) |
| 2 | invalid target slot (bank index outside the 64 addressable banks) |
| 3 | the midi channel does not map to exactly one timbre |
| 4 | storage or file system error, the write failed |
| 5 | protocol or command error (unknown command, incomplete NRPN sequence) |

On every non zero status **nothing is written**, and the remembered bank and preset
position is left untouched. No error path ever falls back to bank 0, preset 0 or to the
current position.

`STORE_TARGET` (LSB 71) is echoed before `STORE_STATUS` in every case except the
incomplete sequence error, where no meaningful target exists.

---

## 6. The store command in detail

```
target = (bank << 7) | preset          bank 0..127, preset 0..127, both zero based
```

Order of validation, all before any write:

1. `bank >= 64` or `preset >= 128` -> status 2. A preenfm patch bank file holds exactly
   128 presets, and at most 64 bank files are indexed, so bank 64..127 can never exist.
2. the midi event must address exactly one timbre -> otherwise status 3.
3. the NRPN sequence must have carried a fresh CC 6 -> otherwise status 5.
4. `PatchBank::getFile(bank)` must report `FILE_OK` -> otherwise status 1. Out of range
   indexes return an internal error entry with `FILE_EMPTY`, so an absent bank can never be
   silently redirected to a different one.
5. `PatchBank::savePreenFMPatch()` must return `COMMAND_SUCCESS` -> otherwise status 4.

What the store does:

- writes the live edit buffer of the addressed timbre, preset name and all patch
  parameters included, exactly as the menu save does,
- **does not read the target slot first**,
- **does not touch any other timbre**,
- updates the remembered bank and preset position **only after** the write succeeded,
- sends `STORE_STATUS` **only after** `savePreenFMPatch()` has returned.

There is no arm/commit step, no confirmation dialog and no extra button press. A complete,
valid store command writes immediately.

Combo banks and DX7 banks are not reachable through this command at all. Bank type 0 is
the only type the protocol accepts, and the store path only ever calls
`Storage::getPatchBank()`.

---

## 7. Midi channel and timbre

`MidiDecoder::midiEventReceived()` builds the list of timbres an incoming event applies to:

- the global channel maps to **all four** timbres,
- the "current instrument" channel maps to the currently selected timbre,
- otherwise every timbre whose channel matches, whose channel is `All`, or which has omni
  on.

A store is refused with status 3 unless that list holds exactly one timbre. This is not an
extra confirmation, it is what makes the command deterministic: on the global channel the
same slot would otherwise be written four times, once per timbre, with four different edit
buffers, and the last write would win.

Queries are answered once per midi event as well, never once per addressed timbre.

To store reliably, give each timbre its own dedicated midi channel and send the store on
that channel.

---

## 8. Example sequences

Channel 1 is used below, so the status byte is `B0`.

### Capability query

```
editor    B0 63 04   B0 62 00   B0 06 00   B0 26 00
firmware  B0 63 04   B0 62 40   B0 06 00   B0 26 01     protocol version = 1
          B0 63 04   B0 62 41   B0 06 00   B0 26 03     capabilities = store + position
```

No answer at all means: old firmware, or NRPN receive is off. See section 10.

### Position query

```
editor    B0 63 04   B0 62 01   B0 06 00   B0 26 00
firmware  B0 63 04   B0 62 42   B0 06 00   B0 26 00     bank type 0
          B0 63 04   B0 62 43   B0 06 00   B0 26 02     bank 2
          B0 63 04   B0 62 44   B0 06 00   B0 26 11     preset 17
          B0 63 04   B0 62 45   B0 06 00   B0 26 01     valid
```

### Load and preview, unchanged from earlier firmwares

```
editor    B0 00 00       CC 0  = 0    regular preenfm patch bank
          B0 20 02       CC 32 = 2    bank 2, zero based
          C0 11          program change 17, zero based -> preset 17
```

This still requires `Program change: Yes` in the menu. No dump is sent automatically.

### Pull the loaded patch, unchanged from earlier firmwares

```
editor    B0 63 7F   B0 62 7F   B0 06 00   B0 26 00     NRPN 127/127, parameter 16383
firmware  ... full parameter dump as NRPNs, name letters first ...
```

### Store the edit buffer into bank 3, preset 12

```
target = (3 << 7) | 12 = 396 = 0x18C  ->  value MSB 3, value LSB 12

editor    B0 63 04   B0 62 02   B0 06 03   B0 26 0C
firmware  B0 63 04   B0 62 47   B0 06 03   B0 26 0C     target echoed, 396
          B0 63 04   B0 62 46   B0 06 00   B0 26 00     status 0, success
```

### Store into a bank that does not exist

```
editor    B0 63 04   B0 62 02   B0 06 09   B0 26 03     bank 9, preset 3
firmware  B0 63 04   B0 62 47   B0 06 09   B0 26 03     target echoed
          B0 63 04   B0 62 46   B0 06 00   B0 26 01     status 1, bank not found
```

### Store on an ambiguous channel

```
editor    (on the global channel)
          B0 63 04   B0 62 02   B0 06 00   B0 26 00
firmware  B0 63 04   B0 62 47   B0 06 00   B0 26 00     target echoed
          B0 63 04   B0 62 46   B0 06 00   B0 26 03     status 3, nothing written
```

---

## 9. USB and DIN behaviour

Responses are emitted by `MidiDecoder::sendMidiCCOut()`, the same function the existing
full dump uses:

- **DIN midi out**: always. Every response is written to the USART output buffer.
- **USB midi**: only when `USB midi:` is set to `In/Out`. With `Off` or `In`, responses
  reach the DIN output only.

Requests are accepted from whichever input the firmware is reading, DIN or USB, without
distinction.

---

## 10. Dependency on the midi configuration

| menu setting | effect on this protocol |
|---|---|
| `Receives: None` or `CC` | **requests are ignored**, no answer at all |
| `Receives: NRPN` or `CC & NRPN` | requests are decoded |
| `Send:` (`None` / `CC` / `NRPN`) | **no effect**, responses are always sent |
| `USB midi: In/Out` | responses also go out over USB |
| `Program change: No` | only affects loading, not this protocol |

The receive dependency is pre-existing behaviour: the whole NRPN branch of
`MidiDecoder::controlChange()` sits behind the NRPN receive bit, and changing that would
alter how existing setups handle every other NRPN. It was deliberately left alone.

The send side is different. Responses deliberately bypass the `Send:` setting, because a
query that stays unanswered when `Send: None` is configured would be useless, and an editor
could not tell that case apart from an old firmware. This only ever emits on NRPN page 4,
which no earlier firmware or editor uses, so no existing setup can be disturbed by it.

**Recommended editor precondition:** `Receives: NRPN` or `CC & NRPN`. If a capability query
goes unanswered, the editor should report "new protocol not available" rather than assume
a firmware version.

---

## 11. Behaviour of older firmware

An older firmware, official 2.21 or the unmodified vosim build, receives the page 4 NRPN,
runs through `decodeNrpn()`, matches none of the branches, and returns. Nothing is written,
nothing is answered, no state changes.

Detection therefore is:

1. send the capability query,
2. wait for `PROTOCOL_VERSION` on page 4 / LSB 64 for a short timeout,
3. no answer means no new protocol. Fall back to load only operation.

The editor must not infer support from the firmware version string, since a user can run
any build.

---

## 12. What this protocol deliberately does not do

- no combo and no DX7 storing,
- no bank creation, renaming or reorganisation,
- no automatic full dump after a program change,
- no position message after a load. The load path is byte for byte the old one, so no
  existing setup sees new traffic. An editor that wants the position after loading should
  send a position query.
