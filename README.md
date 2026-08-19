# preenfm2 firmware, VOSIM base, with editor remote protocol

> ## ⚠ Experimental pre-release. Not hardware tested. Do not flash yet.
>
> The firmware in `release/editor-protocol-2.22p1/` compiles and links cleanly with the
> prescribed toolchain, and its new data path has been reviewed statically and checked by
> simulation. **It has never run on a PreenFM2.** No successful hardware test is claimed.
>
> Do not flash it until the repository owner explicitly releases it. When that happens,
> back up the usb key first: the new store command writes immediately, by design, with no
> confirmation step.

---

## What this repository is

A standalone repository for one piece of work: adding a MIDI protocol that lets a PreenFM
editor **store** the current edit buffer into a bank slot, ask the firmware **where** it
currently is, and ask **whether** the protocol is available at all.

It is not a GitHub fork and not a submodule of any editor project. It carries the full
original commit history, and all GPL and copyright headers are preserved unchanged.

| | |
|---|---|
| origin of the code | [`pvig/preenfm2`](https://github.com/pvig/preenfm2) |
| branch | `vosim` |
| base commit | `6ed604a43636c00bfbac9613c8f5a79a7582dfa7` |
| working branch | `feature/editor-remote-store` |
| firmware version | `2.22p1` (pre-release; the base was `2.21b`) |

Remotes are set up as:

```
upstream -> https://github.com/pvig/preenfm2.git          (fetch only)
origin   -> https://github.com/sscheidl/preenfm2-vosim-editor-firmware.git
```

The `vosim` branch here is the untouched base, kept for a like for like diff. All work is
on `feature/editor-remote-store`.

### Why the VOSIM branch

`pvig/preenfm2` `vosim` carries the VOSIM algorithms 29-32 and the additional LFO shapes
6-8. Those had to be preserved, so this work is based on that branch rather than on
`master`, `dispatcher`, or the official Ixox repository. No changes from other development
branches were merged in.

---

## What was added

Three requests on NRPN page 4, an address page every earlier firmware silently ignores:

| NRPN page / LSB | command |
|---|---|
| 4 / 0 | capability query — protocol version and capability bits |
| 4 / 1 | position query — bank type, bank, preset, valid flag |
| 4 / 2 | store — `target = (bank << 7) \| preset`, both zero based |

Responses come back on page 4, LSB 64 and above, with explicit status codes for success,
bank absent or not writable, invalid slot, ambiguous midi channel, storage error, and
protocol error.

Full byte level specification: [`release/editor-protocol-2.22p1/EDITOR_PROTOCOL.md`](release/editor-protocol-2.22p1/EDITOR_PROTOCOL.md).

Loading over CC 0, CC 32 and program change is unchanged, as is the full parameter dump
over NRPN 127/127. Synthesis, the VOSIM algorithms, the LFO shapes and the patch storage
format are untouched — patches stay compatible with 2.21 in both directions.

### Editor requirements

An editor needs a build that speaks this protocol; no released PreenFM+ / PreenFM2 editor
does yet. On the device, `Receives:` must be set to `NRPN` or `CC & NRPN`, otherwise the
requests are ignored and nothing is answered.

An editor should detect support by sending the capability query and waiting for an answer.
It must not infer support from the firmware version string.

---

## Where the results are

`release/editor-protocol-2.22p1/`

| file | content |
|---|---|
| `p2_2.22p1.bin` | regular firmware |
| `p2_2.22p1o.bin` | overclock firmware (byte identical, see the build report) |
| `p2_2.22p1.elf`, `p2_2.22p1o.elf` | with debug symbols |
| `p2_2.22p1_symbol.txt`, `p2_2.22p1o_symbol.txt` | symbol maps |
| `SHA256SUMS` | checksums |
| `editor-protocol-2.22p1.patch` | full git diff against the base commit |
| `EDITOR_PROTOCOL.md` | protocol specification |
| `BUILD_REPORT.md` | toolchain, commands, sizes, warnings, what could not be tested |
| `FIRMWARE_SAFETY_REVIEW.md` | independent review of the store data path |
| `CHANGELOG_EDITOR_PRERELEASE.md` | change log |

---

## Building

Needs [arm-gcc 4.7-2014q2](https://launchpad.net/gcc-arm-embedded/+milestone/4.7-2014-q2-update),
the version the project has always used. The toolchain is **not** part of this repository.

`GCC_PATH` in the `Makefile` still points at the original author's home directory. Either
edit it locally, or override the tool variables on the command line with the toolchain on
`PATH`, which is what the release build did:

```bash
export PATH="/path/to/gcc-arm-none-eabi-4_7-2014q2/bin:$PATH"

MK='make CC=arm-none-eabi-c++ AS=arm-none-eabi-as NM=arm-none-eabi-nm \
        READELF=arm-none-eabi-readelf CP=arm-none-eabi-objcopy'

$MK clean && $MK pfm
$MK clean && $MK pfmo
```

Always run a full `clean` between targets, as the project warns.

Protocol check, a simulation of the decode path, not a hardware test:

```bash
python test/host/protocol_sim_test.py
```

---

## License

GPL, as the upstream project. Every source file keeps its original license header and its
original author attribution. See the headers in `src/` — most carry
`Copyright 2013 Xavier Hosxe` and the GNU General Public License, version 3 or later.

---

# Upstream README

The text below is the README of the base repository, kept for reference.

---

# preenfm2 firmware

Ixox/preenfm2 is the official repository of the preenfm2 firmware.

You can find here the compiled firmware, its code source and some hardware files for the PCB, MCU board and cases.

To flash the preenfm2 for the first time, [follow these instructions](https://github.com/Ixox/preenfm2/tree/master/flash)


## Compiling the firmware

To compile the firmware, you'll need [arm-gcc version 4.7](https://launchpad.net/gcc-arm-embedded/+milestone/4.7-2014-q2-update)

Add the bin directory to your PATH, and run **'make'**, you'll get the list of the available targets.

```bash
$ make
You must chose a target 
Don't forget to clean between different build targets
   clean : clean build directory
   pfm : build pfm2 firmware
   pfmcv : build pfm2 firmware for Eurorack 
   installdfu : flash last compiled firmware through DFU
   zip : create zip with all inside
```
Since some refactoring, the bootloader does not compile anymore. But it's available in its binary format.

Then put your preenfm2 in [bootloader mode](http://ixox.fr/preenfm2/manual/upgrade-firmware/). Look at DFU part 4.

To flash the firmware on the preenfm2 using the DFU protocol :

```bash
make installdfu
```

Once it's done, unplug the power cable and plug it back.

## New Filters in 2.11

Many effects have been added in the firmware 2.11. They were coded by [Toltekradiation](http://ixox.fr/forum/index.php?topic=69544.0).

His github repo is [here](https://github.com/pvig/preenfm2). You'll find there some description of the different effects.
