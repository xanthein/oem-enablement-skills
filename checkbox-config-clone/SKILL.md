---
name: checkbox-config-clone
description: Use when creating checkbox.conf and manifest.json for a new CID by cloning from a machine with the same C3 canonical_label.
version: 1.0.0
author: dirk
license: MIT
metadata:
  hermes:
    tags: [checkbox, manifest, c3, oem-enablement]
    related_skills: [c3]
---

# Checkbox config clone

## When to Use

A new DUT (CID) has no `pc/<cid>/` in `ce-oem-dut-checkbox-configuration`, and another machine with the **exact same** C3 `canonical_label` is already configured.

## Usage

    S=~/.config/opencode/skills/oem-enablement-skills/checkbox-config-clone/scripts/clone_config.py
    python3 $S <new-cid> --repo ~/source/canonical/ce-oem-dut-checkbox-configuration [--from <donor-cid>]

It fetches origin, looks up the label via the `c3` skill's script, and finds donors: other CIDs with that label whose `pc/<cid>/` files exist on `origin/main` (preferred) or `origin/<cid>`. With one donor it creates branch `<new-cid>` from `origin/main`, copies `checkbox.conf`/`manifest.json`, and commits. It never pushes.

## Exit codes

- `0` done.
- `2` several donors: show the printed list (CID, holder, ref, files) to the user, ask which one, rerun with `--from`.
- `1` error (no donor, dirty worktree, branch or `pc/<new-cid>/` already exists, bad CID). If no donor, suggest `utils/manifest-update.py` (starts from `pc/default`).

## Notes

- Ask the user before pushing the new branch.
- Read-only toward C3; needs the c3 secrets file (see the `c3` skill).
