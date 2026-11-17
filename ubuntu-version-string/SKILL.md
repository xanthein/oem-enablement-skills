---
name: ubuntu-version-string
description: Use when choosing or reviewing a debian/changelog version for an Ubuntu package upload (devel upload, SRU, backport, merge, sync, rebuild, rollback), or when the sponsoring bot / reviewer says "The proposed version can't be used", "was already published in <series>", or "does not match expected version".
---

# Ubuntu package version strings

Source of truth: https://ubuntu.com/project/docs/how-ubuntu-is-made/concepts/version-strings/
Version = `[epoch:]upstream-debian_rev[ubuntu_rev]`, e.g. `1.2.16.1-1ubuntu2`
(upstream `1.2.16.1`, Debian rev `1`, Ubuntu rev `ubuntu2`).

**Hard rule:** a version string can be uploaded to the archive only ONCE across ALL series (shared pool). Always verify, never guess:

```bash
rmadison -u ubuntu alsa-ucm-conf          # every series incl. -updates/-proposed
dpkg --compare-versions NEW gt OLD && echo ok   # NEW must sort above every OLD you must upgrade from
```
Or `lp-api` `getPublishedSources` (see `launchpad` skill) to check pool collisions.

## Pick the version

Let `cur` = version currently in the target series (pocket incl. -proposed).

| Situation | Rule | Example (cur -> new) |
|---|---|---|
| Devel release change | bump N in `ubuntuN`; after `buildN` restart at `ubuntu1` | `2.0-2`->`2.0-2ubuntu1`; `2.0-3build2`->`2.0-3ubuntu1` |
| Merge from Debian | new Debian ver + `ubuntu1` | `2.1-1ubuntu2`->`3.1-2ubuntu1` |
| Sync from Debian | identical to Debian, no suffix | `2.0-2`->`2.0-3` |
| No-change rebuild | `buildN` (no delta) / bump `ubuntuN` (has delta) | `2.0-2`->`2.0-2build1` |
| Stable SRU, no prior delta | `ubuntu0.1` | `2.0-2`->`2.0-2ubuntu0.1` |
| Stable SRU, prior `ubuntuX` | add/bump `.Y` only, never X | `ubuntu3`->`ubuntu3.1`->`ubuntu3.2` |
| Stable SRU, `buildN` before | treat as no delta | `2.0-2build1`->`2.0-2ubuntu0.1` |
| Same fix in several stable series | insert `YY.MM` before `.Y` | `2.0-2ubuntu0.24.04.1`, `...0.25.04.1` |
| Backport new upstream | `-0ubuntu0.YY.MM.1` | `3.1-0ubuntu0.22.04.1` |
| Backport of devel pkg | devel version + `~YY.MM.1` (sorts below devel) | `3.1-1ubuntu2~22.04.1` |
| Backport of devel, devel is pure Debian | `~ubuntu0.YY.MM.1` | `3.1-2~ubuntu0.22.04.1` |
| Ahead of Debian | `-0ubuntuN` | `3.1-0ubuntu1` |
| Upstream pre-release | `~pre1`/`~rc1` | `3.1~pre1-0ubuntu1` |
| Upstream git snapshot | `~gitYYYYMMDD.sha7` (before next rel) / `+git...` (after prev rel) | `3.1~git20260716.f1eeced-0ubuntu1` |
| Syncable change | `maysync1` (no prior delta) / `willsync1` (prior delta) | `2.0-1maysync1` |
| Rollback | `<current>+really<old>` | `3.1+really2.0-2ubuntu2` |
| Native pkg | no `-`; devel `2.0ubuntu1`, SRU `2.0ubuntu0.1` | |

`~` sorts before everything (`1.0~rc1 < 1.0`); `+` sorts after.

## Collision handling (devel series version already used in another series)

Symptom: bot says `X was already published in <other series>`. Typical cause: a later
series (e.g. tenacious) was uploaded with `ubuntu2` while devel (stonking) still has `ubuntu1`.

Invariant: the version in an OLDER series must sort BELOW the version in every NEWER series
(otherwise upgrade offers a downgrade), and the string must be unique in the pool.

Case: the target series is older than a series that already has the version (e.g. stonking,
while the newer tenacious already has `1.2.16.1-1ubuntu2`). Do not reuse or exceed it.
Use the multi-series form `<base>ubuntuX.<YY.MM>.Y`, e.g. `1.2.16.1-1ubuntu1.26.10.1`
(verified: it sorts above `1.2.16.1-1ubuntu1` and below `1.2.16.1-1ubuntu2`). The sponsoring bot's
suggestion is correct here.

Steps:
1. `rmadison` all series; order them by release (oldest -> newest).
2. Newest series takes the plain next `ubuntuN`; older series take the `ubuntuX.<YY.MM>.Y` form.
3. Run `dpkg --compare-versions` for every adjacent pair of series (older lt newer) before pushing.

Only the newest series may use a plain `ubuntuN` that is above everything else.

## Quick checks before pushing

- Series in `debian/changelog` header matches target branch (`ubuntu/<series>-devel`).
- `dpkg --compare-versions NEW gt <cur in this series>` and `gt` every older series.
- NEW not already in `rmadison` output or PPA (PPA needs unique too; OEM PPA builds add `~oem1`).
- First Ubuntu change on a pure-Debian version starts at `ubuntu1` (devel) or `ubuntu0.1` (stable).

## Common mistakes

| Mistake | Fix |
|---|---|
| Bump only the local branch's series, ignore other series | `rmadison` all series first |
| `ubuntu1`->`ubuntu2` on a stable SRU | use `ubuntu1.1` |
| Increment X in SRU `ubuntuX.Y` | only Y |
| Multi-series SRU with identical string | add `YY.MM` per series |
| Using `build1` when adding a real change | use `ubuntuN` |
