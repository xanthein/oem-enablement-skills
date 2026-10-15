---
name: libfprint-sru
description: "SRU libfprint: quilt DEP-3 patch, branch, commit, push."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [sru, ubuntu, debian, quilt, libfprint, launchpad, packaging]
    related_skills: [upstream-bug-triage, github-repo-management]
---

# libfprint SRU workflow

Workflow for adding a device-ID / hwdb-table style SRU patch to the Ubuntu
libfprint packaging repo at `/home/dirk/project/sru/libfprint/libfprint`
(salsa.debian.org, remotes `origin`=dirksu fork, `upstream`=ubuntu-dev-team),
given a Launchpad SRU bug like
`https://bugs.launchpad.net/ubuntu/+source/libfprint/+bug/NNNNNNN`.

## 1. Read the bug and find the upstream fix

- `curl -sL <launchpad-bug-url> | python3 -c "strip tags, html.unescape"` to
  get readable text (web_extract's ddgs backend can't extract Launchpad pages).
- The bug's "[Other Info]" section names the target **codename/branch**
  ("Resolute", "Stonking" etc.) and usually links the exact upstream commit
  (gitlab.freedesktop.org/libfprint/libfprint/-/commit/<sha>). Fetch the raw
  patch with `curl -sL <commit-url>.patch -o /tmp/upstream.patch`.

## 2. Pick the right branch(es) — this repo tracks MULTIPLE Ubuntu series

`git branch -a` shows per-series branches: `noble`, `resolute`, `ubuntu`
(devel/latest, currently codename e.g. "stonking"), plus historical
focal/jammy/oracular/questing etc. **An SRU bug against a specific release
must be committed on that release's branch** — do not assume the checked-out
branch is correct; check the bug page's target milestone/codename first.
If the user says "do the same in X branch", repeat the whole workflow on
that branch from a clean checkout — each branch has its own patch series,
its own already-applied device IDs, and its own line numbers, so a patch
built against one branch's file state will NOT apply to another with
different offsets even for the "same" logical change.

```
git checkout --track origin/resolute   # if ambiguous, matches 2 remotes:
                                        # use --track origin/<name> explicitly
```

## 3. Build the DEP-3 quilt patch with `quilt new` + `quilt add` (not manual diff)

Manually hand-writing a patch file with guessed hunk line numbers is fragile —
line numbers shift release to release as prior patches get merged upstream.
Instead, drive quilt itself so it computes the diff against the real tree:

```
export QUILT_PATCHES=debian/patches
quilt push -a                          # apply the existing series cleanly first
quilt new synaptics-Add-new-PID-0x010B.patch
quilt add data/autosuspend.hwdb libfprint/drivers/synaptics/synaptics.c
# now hand-edit the two files with the patch tool (find exact insertion point
# by grepping neighboring PIDs — e.g. insert 0x010B numerically after 0x010A)
quilt refresh                          # regenerates the patch body from the diff
```

Then prepend the DEP-3 header block to the top of the generated patch file
(above the `Index:`/`---` diff body quilt wrote):

```
Origin: upstream, https://gitlab.freedesktop.org/libfprint/libfprint/-/commit/<sha>
Bug-Ubuntu: https://bugs.launchpad.net/bugs/<bugno>
```

Add the new patch filename to the end of `debian/patches/series` (quilt new
already does this automatically — just confirm with `tail debian/patches/series`).

## 4. Verify apply/pop cleanly, then restore pristine tree

```
quilt pop -a && rm -rf .pc            # drop patch state back to nothing
quilt push -a                          # re-apply the WHOLE series from scratch
grep -n "<new id string>" data/autosuspend.hwdb drivers-file.c   # confirm present
quilt pop -a && rm -rf .pc            # MUST pop again — dpkg-buildpackage
                                        # applies patches itself at build time;
                                        # never leave the tree quilt-applied
git status --short                     # should show only debian/* changes,
                                        # no .pc/, no modified source files
```

## 5. Changelog entry via `dch`, not hand-editing debian/changelog

```
DEBEMAIL="you@canonical.com" DEBFULLNAME="Your Name" \
  dch -v <epoch>:<upstream-version>-<debian-rev> -D <codename> \
  "d/p: <short description> (LP: #<bugno>)"
```
Bump the Debian revision (`-1ubuntu1` -> `-1ubuntu2`, or the `~24.04.N`
backport suffix) appropriately per branch — check the existing top changelog
entry's version/codename on THAT branch before picking the next one; noble,
resolute, and ubuntu(devel) each have independently incrementing versions.

## 6. Commit message convention (matches existing repo history)

Follow the exact convention already used by prior commits on that branch:
`d/p: <patch summary> (LP: #<bugno>)` — this is the literal message style
seen in `debian/changelog` and in `git log`, e.g.:
`d/p: synaptics: Add new PID 0x10b (LP: #2166829)`
Do NOT invent a different style (no multi-line body, no upstream-only
subject line without the `d/p:` / `(LP: #...)` wrapper) unless the user
explicitly asks — if unsure, `git log --oneline -10` on the target branch
first and mirror the most recent entries' shape exactly.

```
git add debian/changelog debian/patches/series debian/patches/<new>.patch
git commit -m "d/p: <summary> (LP: #<bugno>)"
```

## 7. Push to origin (per branch)

```
git push origin <branch>
```
`origin` here is the user's personal salsa.debian.org fork (dirksu/libfprint),
not upstream-dev-team — pushing directly to a personal branch like
`resolute`/`ubuntu` is fine without a merge request, but push warns with a
merge-request URL; that's informational, not an error.

## Pitfalls

- Don't build the patch on the wrong branch — always confirm codename from
  the Launchpad bug page vs. `git branch --show-current` before starting.
- Don't trust a hand-crafted patch's line numbers against a different
  branch's file state; always regenerate the hunk via `quilt refresh`
  against that branch's actual pristine tree.
- Always `quilt pop -a && rm -rf .pc` before committing — a leftover `.pc/`
  directory or an applied-patch working tree will pollute `git status` and
  can get accidentally committed.
- `quilt new` copies no header text — the DEP-3 `Origin`/`Bug-Ubuntu` fields
  must be added manually after `quilt refresh`, they don't survive a second
  `quilt refresh` call automatically either, so add them as the LAST step
  before the final pop/verify cycle, not before.
