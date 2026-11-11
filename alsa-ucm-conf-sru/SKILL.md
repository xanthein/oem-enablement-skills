---
name: alsa-ucm-conf-sru
description: Use when doing an alsa-ucm-conf SRU for an upstream UCM commit - file the Launchpad bug, build git-ubuntu branches per series, build test packages in a PPA.
---

# alsa-ucm-conf SRU

Take an upstream alsa-ucm-conf commit (e.g. a new codec UCM config) through a
Launchpad bug, git-ubuntu branches per series, and a test PPA.
Uses the `launchpad` skill for `lp-api`. Work under `/tmp/opencode`.

## 0. Challenge the request first
- Read the upstream PR/commit `.patch` (`curl -sL <commit-url>.patch`). Use the
  **merged commit**, not the PR head; the PR may differ (e.g. extra files).
- Check the user-given bug actually matches the change (same hardware).
  A bug already tagged `verification-done-*` is finished: file a **new** bug.
- Confirm what the hardware is (codec vs amp) before writing the test plan.
- Get the target series. Check the kernel has the driver for each series.
  Current dev series: `lp-api get ubuntu | jq .current_series_link`.

## 1. File the bug
SRU template: Impact / Test Plan / Where problems could occur / Other Info
(upstream commit URL). Write it to a file, then:
```sh
lp-api post bugs ws.op=createBug title='[SRU] ...' description=@desc.txt \
  target=https://api.launchpad.net/devel/ubuntu/+source/alsa-ucm-conf --print=hb --pretty=none
# Location header = bug URL. Add a task per series:
lp-api post bugs/ID ws.op=addTask target=https://api.launchpad.net/devel/ubuntu/<series>/+source/alsa-ucm-conf
```

## 2. Clone and branch (per series)
```sh
git ubuntu clone alsa-ucm-conf && cd alsa-ucm-conf
git branch -r | grep pkg/ubuntu/    # series: pkg/ubuntu/<series>-devel
```
- `ubuntu/devel` is the development series; confirm `pkg/ubuntu/<dev>-devel`
  has the same commit. Older series: branch from `pkg/ubuntu/<series>-devel`.
- `git checkout -b lp<bug>-<what>-<series> pkg/ubuntu/<series>-devel`

## 3. Patch
- Patch name: `debian/patches/lp<bug>-<upstream-subject>.patch`, append to
  `debian/patches/series` (check it ends with newline).
- DEP-3 header: `From`, `Subject`, `Origin: upstream, <commit URL>`,
  `Bug-Ubuntu: https://bugs.launchpad.net/bugs/<bug>`, `Forwarded: not-needed`.
- Upstream hunks often fail on older series (regexes like `MultiCodecRegex`
  differ). Refresh by editing the tree and `diff -uN` against pristine, using
  `a/` `b/` labels. Beware: don't drop the last line when extracting a new file
  from the `.patch`; diff the result against upstream.
- Verify in a **copy** of the tree (git-ubuntu trees are unapplied; a `quilt
  push -a` in place leaves the tree dirty):
  `QUILT_PATCHES=debian/patches quilt push -a`
- Changelog entry (`[ Name ]`, `(LP: #bug)`, `- d/p/<patch>`); version e.g.
  `<old>ubuntu1.N+1` for stable, next `ubuntuN` for devel. Don't reuse a
  version already in the PPA/archive.
- Commit message mirrors the changelog; `git push dirksu <branch>`.
  MP target: `ubuntu/<series>-devel` (devel for the dev series).

## 4. Test PPA
- Create a **new** PPA named after the bug (same version cannot be re-uploaded
  to an existing PPA):
  `lp-api post '~USER' ws.op=createPPA name=lp<bug> displayname=... description=...`
- Version gets `~oem1` suffix. Do this only in an export dir, not on the branch:
```sh
d=up/<s>/alsa-ucm-conf-<upstream-ver>; mkdir -p $d; git archive <branch> | tar -x -C $d
# orig tarball: pull-lp-source -d alsa-ucm-conf <series> in up/<s>/
cd $d && sed -i '1s/^alsa-ucm-conf ([^)]*)/alsa-ucm-conf (<ver>~oem1)/' debian/changelog
update-maintainer    # needed when base is Debian packaging, else dpkg-source fails
dpkg-buildpackage -S -sa -d -k<KEYID>
dput ppa:USER/lp<bug> ../*_source.changes
```
- Check: `lp-api get '~USER/+archive/ubuntu/lp<bug>' ws.op==getPublishedSources`.
  Wait for builds; test on hardware before SRU upload.

## Gotchas
- Don't run long chains in one tool call; a timeout interrupts mid-build.
- Existing PPA version collision: check `getPublishedSources` first.
- SRU uploads to `<series>-proposed` need sponsor/SRU team; add bug tag
  `verification-needed-<series>` later per SRU process.
