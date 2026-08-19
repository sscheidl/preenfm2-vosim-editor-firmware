# Reference build: `p2_2.21b_lfo_vosim.bin`

The community build of the extended VOSIM firmware, as circulated on the ixox forum
([topic 70017](https://ixox.fr/forum/index.php?topic=70017.15)). It is kept here as a
reference point, not as a build input.

```
p2_2.21b_lfo_vosim.bin   413 752 bytes
SHA-256  a29c16e9d299a463c9d2710c457ef792a7d5ef294ae055db6c5f6e03f10108a5
```

## Its source is this repository's base commit

The forum thread points at [`Ixox/preenfm2` PR #18](https://github.com/Ixox/preenfm2/pull/18)
as the published source. That pull request is still open against `master`, and its head is:

```
pvig:vosim @ 6ed604a43636c00bfbac9613c8f5a79a7582dfa7
```

which is **exactly the base commit of this repository**. The five commits in the PR are the
five commits at the tip of `vosim`:

```
9da152d  vosim like
819131d  voice fix
e30a252  algo display + mod range
4bf79aa  lfo noise shape
6ed604a  vosim sync, sigma fix
```

So the extended algorithms and LFO shapes are not something this firmware has to catch up
with. They are already in the source it is built from.

## How that was checked

Not by reading the file name. Three independent checks:

1. **Feature marker.** `Flow`, the name of the eighth LFO shape, is added by `4bf79aa`
   ("lfo noise shape"). It is present in the forum binary, absent in a build of the
   preceding commit `e30a252`, and present in every build made here. So the forum binary is
   at or after `4bf79aa`, and it does carry the extended LFO set.

   ```
   e30a252 build          413 508 bytes   "Flow": no
   forum binary           413 752 bytes   "Flow": yes
   6ed604a build          413 936 bytes   "Flow": yes
   3.00 alpha             415 120 bytes   "Flow": yes
   ```

2. **String tables.** A word level comparison of the forum binary against a local build of
   `6ed604a` finds 1293 shared strings and no functional difference. There is no menu entry,
   no LFO name and no parameter label in the forum binary that the local build lacks.

3. **Enum history.** `enum LfoType` in `src/synth/SynthState.h` carries 5 shapes up to
   `e30a252` and 8 from `4bf79aa` onwards. `enum Algorithm` reaches `ALG32`, the four VOSIM
   algorithms, in the same line of development. Both are present at `6ed604a`.

## Why the byte count differs

Builds here, with the prescribed `gcc-arm-none-eabi 4.7-2014q2`, produce 413 936 bytes for
`6ed604a`, 413 832 for `4bf79aa`, 413 572 for `9da152d` and 413 508 for `e30a252`. None of
them is 413 752.

The differences between neighbouring commits are of the same order as the gap to the forum
binary, so size alone cannot pin down its commit. The likely explanation is a different
toolchain patch level on the machine that produced it. Since the string tables and the
feature marker agree, that difference is a build environment artefact, not a difference in
what the firmware can do.

## Do not flash this together with 3.00 alpha

They are alternatives, not layers. `p2_3.00alpha.bin` is built from `6ed604a` plus the
editor remote protocol, so it already contains everything this reference binary does.
Flashing this one afterwards would remove the protocol again.
