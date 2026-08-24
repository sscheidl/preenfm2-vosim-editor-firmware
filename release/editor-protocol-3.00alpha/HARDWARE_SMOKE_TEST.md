# First hardware smoke test, 3.00 alpha

> **Status update, 2026-08-24:** the regular build has now been installed on a
> physical PreenFM2. The initial smoke test was positive, and subsequent use
> with PreenFM+ has remained stable without observed firmware anomalies. This
> procedure remains useful for every new device, USB key or rebuilt binary.

This procedure was written before the first hardware run and is retained as the
safe validation sequence for a new device or rebuilt binary. It keeps the
destructive step last, after the non-destructive checks have behaved.

**The store command writes immediately. There is no confirmation step, by design.**

---

## 0. Before you start

| | |
|---|---|
| binary | `p2_3.00alpha.bin` |
| SHA-256 | `da9944f01a6f87390f33b5b58000698ba7cb4c80a1c687f2b3ec9f022b65e82e` |
| do **not** flash | `5fe73ed8…f0dc` and `7d77d70d…33df`, both carry a store defect |

Verify before flashing:

```bash
sha256sum -c SHA256SUMS
```

Preparation:

1. **Copy the whole usb key to your computer.** Not just the banks — the entire `pfm2`
   directory. This is the only real safety net in the whole procedure.
2. Create a throwaway bank on the key, e.g. `ZTEST.bnk`, and note its **index**. The index
   is the position in the sorted bank list, zero based, not the file name.
3. Note the current firmware version so you can go back: the previous release binary and a
   working 2.21 build should both be on hand.
4. Disconnect anything that sends MIDI on its own — sequencers, controllers with
   auto-send, DAW tracks with active output. For this test the editor or a MIDI monitor
   should be the only thing talking to the device.
5. On the device: `Receives:` = `NRPN` or `CC & NRPN`. Without it nothing answers.
   `USB midi:` = `In/Out` if you want the responses over USB.

Flash the same way as any other PreenFM2 firmware, via DFU. Nothing about the bootloader
changed.

---

## 1. Boot screen

Expected, on a 20 column display:

```
preenfm2 v3.00 alpha
  By Hosxe & tAUREON
```

Check the version line is not truncated and the author line shows both names. The menu
version item `V:` should read `3.00 alpha`.

If the display looks wrong here, stop and reflash the previous firmware. Everything below
assumes the boot screen renders.

---

## 2. Nothing new happens on its own

Before sending anything, watch the MIDI output for a minute while playing a few notes and
turning encoders. The new protocol must be completely silent until asked.

Then load a patch the old way and confirm nothing extra appears:

```
B0 00 00      CC 0  = 0     regular patch bank
B0 20 02      CC 32 = 2     bank 2
C0 11         program change 17
```

The patch should load, and **no** NRPN dump should follow. If a dump appears, that is a
regression, stop.

---

## 3. Capability query, read only

```
send      B0 63 04   B0 62 00   B0 06 00   B0 26 00
expect    B0 63 04   B0 62 40   B0 06 00   B0 26 01     protocol version 1
          B0 63 04   B0 62 41   B0 06 00   B0 26 03     capabilities: store + position
```

No answer means either `Receives:` is not set to NRPN, or the firmware is not the one you
think it is. Nothing is written either way.

---

## 4. Position query, read only

Right after boot, before loading anything:

```
send      B0 63 04   B0 62 01   B0 06 00   B0 26 00
expect    bank type 0, and POSITION_VALID = 0
```

Then repeat step 2's program change and query again. Now expect bank 2, preset 17,
`POSITION_VALID = 1`. This confirms the position tracking fix without writing anything.

---

## 5. Full dump still works

```
send      B0 63 7F   B0 62 7F   B0 06 00   B0 26 00
```

A complete parameter dump should follow, as in 2.21. Compare a few values against the
device display.

---

## 6. Error paths, still non-destructive

Run these **before** the first real store. Every one of them must answer an error and write
nothing. Afterwards, check on the device that no bank changed.

| test | send | expect |
|---|---|---|
| bank that does not exist | store target `(60 << 7) \| 0` | status **1** |
| bank index out of range | store target `(100 << 7) \| 0` | status **2** |
| ambiguous channel | the same store on the **global** channel | status **3** |
| incomplete sequence | `B0 63 04`, `B0 62 02`, then `B0 26 05` with no CC 6 | status **5** |
| repeat of a consumed command | a valid store, then a lone `B0 26 0A` | status **5** |
| increment | a valid store, then `B0 60 00` (CC 96) | status **5** |

The last three are the ones that used to write a second slot. They are the reason this
build exists. Check the throwaway bank afterwards: only the slot you addressed in the
"valid store" should have changed.

---

## 7. First real store, throwaway bank only

Only now, and only into the bank you created in step 0.

1. Edit a patch so it is obviously different — change the name and one loud parameter.
2. Send a store to `target = (throwaway_bank_index << 7) | 0`.
3. Expect `STORE_TARGET` echoing your target, then `STORE_STATUS = 0`.
4. **Power cycle the device.** This is the point: it proves the write reached the key, not
   just RAM.
5. Load the slot back with CC 0 / CC 32 / program change and compare the name and the
   parameter you changed.
6. Query the position and confirm it reports the slot you just wrote.

If the status came back 0 but the reload shows the old patch, the file system layer
reported success for a write that did not land. Stop and report it — that is the one
failure mode the firmware cannot detect on its own.

---

## 8. Store while notes are sounding

Hold a chord and send a store. Expect an audible gap: the write blocks the audio loop.
This is the same behaviour the menu save has always had. What matters is that the device
does not hang or reset, and that the write still succeeds.

Incoming MIDI during the write goes into a 200 byte buffer and can overflow, so do not
send a dense MIDI stream at the same time as a store.

---

## 9. Only after all of the above

- store to a second slot in the throwaway bank and reload both;
- store from a timbre other than 1, on that timbre's own channel, and confirm the other
  three timbres are untouched;
- confirm the VOSIM algorithms 29-32 and LFO shapes 6-8 still sound as they did on the
  previous firmware.

Real banks stay out of it until every step above has passed twice.

---

## What to record

For each step: what you sent, what came back, and whether the device state matched. The
error paths in step 6 and the power cycle in step 7 are the two that actually matter — the
rest is confirmation that nothing regressed.
