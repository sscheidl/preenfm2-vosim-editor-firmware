# MPE test MIDI files (PreenFM2 3.10 alpha)

Standard MIDI Files that replay the Lower-Zone MPE scenarios **A–J** of
[`tools/mpe_test.py`](../../tools/mpe_test.py) from a DAW (written with Studio One 7 in mind).
They cover cases a real MPE controller cannot be made to play on purpose: the same note on
two channels, voice stealing, master-channel pressure, expression sent before the note on.

The messages are not re-implemented: the generator imports the scenarios from
`tools/mpe_test.py`, so the files contain the same bytes as `--scenario A` … `--scenario J`.
Only the timing is laid out on a bar grid (120 bpm, 4/4, 140 s, one marker per scenario).

| File | Content |
|---|---|
| `PreenFM2_MPE_Test.mid` | One note track carrying channels 1–16, like an MPE recording. Try this first. |
| `PreenFM2_MPE_Test_PerChannel.mid` | Same content, one track per MIDI channel (17 tracks) for hosts that only know one channel per track. |
| `make_mpe_test_midi.py` | Generator. `--check` exits 1 if the files on disk no longer match what `tools/mpe_test.py` generates. |

```text
python test/midi/make_mpe_test_midi.py            # (re)write both files
python test/midi/make_mpe_test_midi.py --check    # verify them, writes nothing
```

Needs `mido` (`pip install mido`). Nothing is sent and no MIDI port is opened.

## What is in the files

Master channel 1, member channels 2–16, member pitch bend range ±48 semitones (the firmware
default and maximum). Only note on/off, channel pressure, pitch bend, CC74 (slide, member
channels) and CC64 (sustain, master channel). **No RPN / MPE Configuration Message**, no
program or bank change, no reset: outside the configured zone RPN (CC100/101) can still reach
the arpeggiator, so the zone is configured in the PreenFM2 menu. **No polyphonic key pressure**
(use `tools/polyat_test.py` for that).

## PreenFM2 setup

- Load `MPE ROLI` (slot 2 of `ExprDemo.bnk`, see the release assets) in the MPE timbre.
- `MPE inst:` = that timbre, `MPE master:` = 1, `MPE members:` = 15, `MPE bend st:` = 48.
- Arpeggiator and unison off. Listen in stereo (slide moves the pan).

## Host setup and the channel check

In Studio One, add the PreenFM2 as an external instrument and enable MPE / all channels if the
device offers it. **Not verified:** how Studio One 7 treats per-event channels when a
multi-channel file is imported and played to an external instrument. Play scenario **B**
(bar 7) first: two notes sound, only one of them jumps up an octave, later only the other one
down a fifth. If both move together or neither does, channels are being lost: use the
per-channel file with each track set to its own channel, or send the scenarios directly:

```text
python tools/mpe_test.py --scenario B --port "<port name>"
```

## Scenarios

| | Bar | Expect |
|---|---|---|
| **A** Press | 2 | C4 (ch 2) gets bright while E4 (ch 3) stays; then E4 slightly bright, C4 back |
| **B** Glide | 7 | C4 +1 octave, E4 down a fifth, each alone; then back |
| **C** Slide CC74 | 13 | only C4 moves in pan/timbre, G4 only slightly |
| **D** Press + Glide + Slide | 18 | two notes sweep in opposite directions, ±1 octave, brighter/darker, pan opposed |
| **E** Same note twice | 28 | C4 on two channels is two voices; only one gets bright; the reused channel then starts clean |
| **F** Voice stealing | 34 | 15 notes on all member channels: no stuck note, a stolen voice inherits no old pressure |
| **G** Sustain | 45 | CC64 on channel 1: voices outlast their note off and end at pedal up |
| **H** Master pressure | 50 | C4 pressed hard, then every voice jumps to pressure 40, then 0 |
| **I** Expression first | 55 | pressure, slide and +5 semitone bend arrive before the note on; it starts already bright, panned and on F4 |
| **J** Four-note chord | 60 | C4 E4 G4 B4 on ch 2–5, each with its own pressure, bend (−3 … +3 semitones) and slide |

Watch D and F for dropouts or stuck notes (CPU / MIDI load). Record results per scenario as in
[`docs/MPE_IMPLEMENTATION_REPORT.md`](../../docs/MPE_IMPLEMENTATION_REPORT.md) §18.

## Controller notes

A ROLI LUMI Keys sends no slide (CC74) and has no sustain input, according to ROLI and
reviews (not verified on a specific unit). Scenarios C, E, G, H and I can therefore only be
covered with these files.

## Status

Checked by an independent parser: raw bytes identical to `tools/mpe_test.py`, only the
message types listed above, CC64 only on the master channel, no note on the master channel,
no hanging note, pedal up at the end, both files equal in content. **Not verified:** playback
in Studio One 7 or on a PreenFM2.
