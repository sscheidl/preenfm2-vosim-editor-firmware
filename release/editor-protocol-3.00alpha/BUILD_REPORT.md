# Build report, editor protocol pre-release 3.00 alpha

Built on 2026-08-19, Windows 11 Pro 10.0.26200, Git Bash.

---

## 1. Repository

| | |
|---|---|
| upstream | `https://github.com/pvig/preenfm2.git` |
| upstream branch | `vosim` |
| origin | `https://github.com/sscheidl/preenfm2-vosim-editor-firmware.git` (private, not a fork) |
| base commit | `6ed604a43636c00bfbac9613c8f5a79a7582dfa7` — "vosim sync, sigma fix" |
| working branch | `feature/editor-remote-store` |
| working commit | `f7cdfcf444f3442be3e0a823ef25d66b316f1dbf` |

The remote `vosim` head was checked before any work started and matched the expected base
commit `6ed604a…` exactly, so no deviation had to be investigated.

`6ed604a` is also the head of [`Ixox/preenfm2` PR #18](https://github.com/Ixox/preenfm2/pull/18),
which publishes the VOSIM work and is still open against `master`. It is therefore the
source of the `p2_2.21b_lfo_vosim.bin` community build, so this firmware is that build plus
the editor protocol. The check is documented in `release/fw_2.21b_lfo_vosim/README.md`. The push URL of
`upstream` was set to `DISABLED_NO_PUSH_TO_UPSTREAM` so nothing can be pushed to
`pvig/preenfm2` by accident.

Full original history and all GPL and copyright headers are preserved. No author
information was changed.

---

## 2. Toolchain

The project prescribes `gcc-arm-none-eabi 4.7-2014q2`. Nothing was installed on the
system: the toolchain and GNU make were unpacked into a directory **outside** the git
repository and are not committed.

```
toolchain/gcc-arm-none-eabi-4_7-2014q2/     from launchpad, unpacked, not installed
toolchain/make/                             portable GNU make, unpacked, not installed
```

Compiler, exact version output:

```
arm-none-eabi-c++.exe (GNU Tools for ARM Embedded Processors) 4.7.4 20140401 (release)
[ARM/embedded-4_7-branch revision 209195]

gcc version 4.7.4 20140401 (release) [ARM/embedded-4_7-branch revision 209195]
(GNU Tools for ARM Embedded Processors)
Target: arm-none-eabi
Thread model: single
```

Source archive: `gcc-arm-none-eabi-4_7-2014q2-20140408-win32.zip`
SHA-256 `a2fe8e910b451375c98d92a4e1952b51c712da3dc546924dbb0acc5af4cd603e`

Build tools:

```
GNU Make 4.4.1, Built for Windows32
git version 2.53.0.windows.1
```

This is the exact compiler the project asks for. No newer toolchain was substituted.

---

## 3. Build commands

The `Makefile` hard codes `GCC_PATH` to the original author's home directory, and the
local toolchain path contains spaces, which a make variable cannot carry safely. Rather
than edit the `Makefile` and commit a machine specific path, the five tool variables the
`Makefile` actually uses were overridden on the command line, with the toolchain on `PATH`:

```bash
export PATH="<project>/toolchain/gcc-arm-none-eabi-4_7-2014q2/bin:<project>/toolchain/make/bin:$PATH"

MK='make CC=arm-none-eabi-c++ AS=arm-none-eabi-as NM=arm-none-eabi-nm \
        READELF=arm-none-eabi-readelf CP=arm-none-eabi-objcopy'

$MK clean && $MK pfm        # regular build
$MK clean && $MK pfmo       # overclock build
```

`$(C)` and `$(LD)` are declared in the `Makefile` but never used by any rule, so they need
no override. A full `clean` was run between every target, as the project warns.

No CV build and no bootloader build were made.

---

## 4. Results

Both targets built successfully, exit code 0.

| artifact | size (bytes) |
|---|---:|
| `p2_3.00alpha.bin` | 415 120 |
| `p2_3.00alphao.bin` | 415 120 |
| `p2_3.00alpha.elf` | 2 384 364 |
| `p2_3.00alphao.elf` | 2 384 364 |

### Comparison with the unmodified vosim base

The base commit was built first with the identical toolchain and commands, for a like for
like comparison.

| | base 2.21b | new 3.00 alpha | delta |
|---|---:|---:|---:|
| `.bin` size | 413 936 | 415 120 | **+1 184** |
| `.text` | 359 168 | 360 352 | +1 184 |
| `.data` | 54 768 | 54 768 | 0 |
| `.bss` | 100 208 | 100 216 | +8 |
| `.ccm` (`0x6198`) | 24 984 | 24 984 | 0 |
| `.ccmnoload` (`0x848c`) | 33 932 | 33 932 | 0 |

The 8 extra bytes of `.bss` are the new `MidiDecoder` members: `currentEventTimbreCount`,
`editorCommandDoneThisEvent` and `editorValueMsbSeen[4]`.

### Linker limits

From `linker/stm32f4xx.ld`:

```
RAM    (xrw) : ORIGIN = 0x20000000, LENGTH = 112K   =  114 688
CCMRAM (xrw) : ORIGIN = 0x10000000, LENGTH =  64K   =   65 536
FLASH  (rx)  : ORIGIN = 0x08040000, LENGTH = 768K   =  786 432
```

| region | used | limit | headroom |
|---|---:|---:|---:|
| FLASH (`.bin`) | 415 120 | 786 432 | 52.8 % used, 371 312 free |
| RAM (`.data` + `.jcr` + `.bss`) | 96 072 | 114 688 | 83.8 % used, 18 616 free |
| CCMRAM (`.ccm` + `.ccmnoload`) | 58 916 | 65 536 | 89.9 % used, 6 620 free, unchanged |

Everything is inside the linker limits, and the link step produced no overflow diagnostic.

### `pfm` and `pfmo` are byte identical

Both binaries hash to the same SHA-256. This is not a build mistake: `-DOVERCLOCK` is added
by the `Makefile` for the `pfmo` target, but `grep -rn OVERCLOCK src/` finds **no reference
to it anywhere in the sources of the vosim branch**. The same is true for the unmodified
base, whose `pfm` and `pfmo` binaries are likewise identical. This is a property of the
upstream branch, not of this change. The overclock binary is shipped anyway, under its
expected file name, so nothing downstream has to change.

---

## 5. Compiler warnings

**48 warnings in the new build. 48 warnings in the unmodified base. The two warning sets
are identical, compared line by line — this change introduces no new warning.**

None originate in the new code. All of them are pre-existing, in the vendor USB stack and
in legacy C++ headers:

| count | warning |
|---:|---|
| 20 | non-static data member initializers only available with `-std=c++11` (`src/synth/LfoOsc.h`) |
| 10 | deprecated conversion from string constant to `char*` `[-Wwrite-strings]` |
| 6 | invalid conversion from `void*` to `USBH_HOST*` `[-fpermissive]` |
| 3 | invalid conversion from `const void*` to `void*` `[-fpermissive]` |
| 2 | `"NULL" redefined` |
| 7 | further `[-fpermissive]` argument conversions in the ST USB host library |

No warning was suppressed, no `-w` and no pragma was added, and no warning flag was
changed. The complete build logs are the raw make output referenced in section 7.

---

## 6. SHA-256 checksums

> **Superseded builds, do not flash.** Two earlier 3.00 alpha binaries were produced during
> development and both contain a store defect described in `FIRMWARE_SAFETY_REVIEW.md`
> section 12. Neither is in this directory any more, but if a copy is lying around, discard
> it:
>
> ```
> 5fe73ed8b155da8f61eea8e91884ea3cb0e9fab064296b0f292ae144009b7737   lone CC38 double store
> 7d77d70db68afd7372145cde2f6a4490c4f3ee4ad94e1bf59c8492c15b3d33df   CC96/97 derived store
> ```


See `SHA256SUMS` in this directory:

```
da9944f01a6f87390f33b5b58000698ba7cb4c80a1c687f2b3ec9f022b65e82e  p2_3.00alpha.bin
da9944f01a6f87390f33b5b58000698ba7cb4c80a1c687f2b3ec9f022b65e82e  p2_3.00alphao.bin
e2afba54fc34674dc703adeb7f4db8cb8bd8d27f5b53761c8998ad6f68b70129  p2_3.00alpha.elf
e2afba54fc34674dc703adeb7f4db8cb8bd8d27f5b53761c8998ad6f68b70129  p2_3.00alphao.elf
ef840dd403e0eeadcfd49caf5ef7295d0cfbfff42ee28a40e9dbfa9ca3a26fa0  editor-protocol-3.00alpha.patch
```

Base build, for reference:

```
p2_2.21b.bin   md5 e71d0eb38e598e1f44ff26a9cd26ea35   413 936 bytes
p2_2.21bo.bin  md5 e71d0eb38e598e1f44ff26a9cd26ea35   413 936 bytes
```

---

## 7. Artifact paths

All inside the repository, under `release/editor-protocol-3.00alpha/`:

| file | content |
|---|---|
| `p2_3.00alpha.bin` | regular firmware |
| `p2_3.00alphao.bin` | overclock firmware, byte identical, see section 4 |
| `p2_3.00alpha.elf` | regular firmware with debug symbols |
| `p2_3.00alphao.elf` | overclock firmware with debug symbols |
| `p2_3.00alpha_symbol.txt` | symbol map, regular |
| `p2_3.00alphao_symbol.txt` | symbol map, overclock |
| `SHA256SUMS` | checksums |
| `editor-protocol-3.00alpha.patch` | full git diff `6ed604a…f7cdfcf` |
| `EDITOR_PROTOCOL.md` | protocol specification |
| `BUILD_REPORT.md` | this file |
| `FIRMWARE_SAFETY_REVIEW.md` | independent review of the store data path |
| `CHANGELOG_EDITOR_PRERELEASE.md` | change log |
| `HARDWARE_SMOKE_TEST.md` | procedure for the first hardware test |

The build directory `build/` is git ignored and holds the same binaries after a build.

---

## 8. Version number and boot screen

`Makefile` line 1 previously held one variable that served two incompatible purposes. The
display string `3.00 alpha` contains a space, and the same variable is used as a build path
component, where make would split `build/p2_3.00 alpha.elf` into two words. The two uses are
now separate:

```makefile
PFM2_VERSION_NUMBER=3.00alpha              # file names: build/p2_3.00alpha.bin
PFM2_VERSION_DISPLAY=3.00 alpha            # what the firmware shows
PFM2_VERSION:='"${PFM2_VERSION_DISPLAY}"'  # single quoted, see below
```

The single quotes matter. `-DPFM2_VERSION=\"3.00 alpha\"` would reach the shell as two
arguments and break the compile. `-DPFM2_VERSION='"3.00 alpha"'` is passed to the compiler
as one argument, space included. Verified by a real build, not by inspection.

`PFM2_BOOTLOADER_VERSION` is untouched and still uses the old escaping, since it has no
space.

### Boot screen

`src/PreenFM_init.cpp` defines three lines, on a 20 column by 4 row display:

```c
const char* line1 = "preenfm2 v"PFM2_VERSION""CVIN_STRING;   // "preenfm2 v3.00 alpha", 20
const char* line2 = "  By Hosxe & tAUREON";                  // 20
const char* line3 = "          24bits DAC";                  // 20
```

`line1` is now exactly 20 characters, which both render paths handle:

- `MCP4922_screenBoot()` walks columns 0..19 and stops early at the terminating zero. At 20
  characters it simply never stops early;
- `CS4344_screenBoot()` right aligns with `setCursor(20 - length, 0)`, which becomes
  `setCursor(0, 0)`.

`line2` changed from `"     By Xavier Hosxe"` to `"  By Hosxe & tAUREON"`. **The length of
20 is load bearing:** `MCP4922_screenBoot()` prints `line2[r]` for `r` = 0..19 with **no
terminator check**, so a shorter string would be read past its end. The replacement is
exactly 20 characters, and the script that applied it asserts that length.

Xavier Hosxe keeps his attribution as the original author: on the boot screen, in every GPL
source header, in the USB manufacturer string `USBD_MANUFACTURER_STRING` in
`src/usb/usbd_midi_desc.c`, and in the README. This build's author is credited alongside,
not in place of him.

### Verified against the binary

The strings were read back out of the built `.bin`, not inferred from the source:

```
preenfm2 v3.00 alpha     20 characters
  By Hosxe & tAUREON     20 characters
          24bits DAC     20 characters
Xavier Hosxe             USB manufacturer string, unchanged
```

The menu version item `V:` shows `3.00 alpha` (`src/hardware/Menu.cpp:38`, 13 characters
including the label, well inside 20 columns).

The firmware no longer reports itself as `2.21b`.

## 9. Tests that could not be run

**No hardware test was performed. No successful hardware test is claimed.**

The following are open and can only be closed on a real PreenFM2 with a usb key:

- writing to a real `.bnk` file on a real usb key, and reading the slot back,
- store while notes are sounding, including the audible gap the blocking usb write causes,
- behaviour on a full, unplugged, or write protected usb key,
- DIN and USB response timing against a real editor,
- the boot screen and menu rendering of the new version string on real hardware,
- confirming that VOSIM algorithms 29-32 and LFO shapes 6-8 still sound correct.

What *was* verified, and how:

| check | method |
|---|---|
| both targets compile and link | real build with the prescribed compiler |
| no new compiler warning | line by line diff of both warning sets |
| flash, RAM and CCM inside the linker limits | `readelf -S` and `arm-none-eabi-size` against `stm32f4xx.ld` |
| NRPN page 4 collides with nothing | mechanical range check in `test/host/protocol_sim_test.py` |
| decode path, status codes, target encoding | simulation, `test/host/protocol_sim_test.py`, 23 cases, 89 checks |
| store data flow | static review, `FIRMWARE_SAFETY_REVIEW.md` |
| synthesis untouched | the diff touches no synth engine file, see `editor-protocol-3.00alpha.patch` |

There is no host C++ toolchain on this machine, and adding one would have meant shipping a
second build system beside the firmware `Makefile`. The protocol check is therefore a
python simulation of the C++ control flow that reads its constants out of
`src/midi/MidiDecoder.h`, so specification, simulation and firmware cannot drift apart. It
is explicitly **not** a firmware unit test.
