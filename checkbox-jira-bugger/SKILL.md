---
name: checkbox-jira-bugger
description: Use when filing Jira bugs from a checkbox-rerun output tarball (submission_*_rerun.tar.xz) after a Testflinger run.
version: 1.0.0
author: dirk
license: MIT
metadata:
  hermes:
    tags: [checkbox, jira, bug, submission, oem-enablement]
    related_skills: [checkbox-rerun, c3, testflinger]
---

# Checkbox Jira bugger

## When to Use

`checkbox-rerun` has produced `submission_X_rerun.tar.xz` and the still-failing jobs should become Jira bugs. Runs unattended: if the gate passes it files for real.

The user gives `-p` project, `-m` milestone (FC|GM), `-a` assignee (email or Mattermost name) and `-l` platform label before the Testflinger run. If any is missing, stop and ask. Never guess: filing is irreversible.

## Prereqs

- `checkbox-jira-bugger` on PATH (e.g. `pipx install` the repo).
- These exist (check with `ls`; never cat or print them, they hold secrets):
  `~/.checkbox-jira-bugger/secrets/jira_secrets.ini`, `~/.checkbox-jira-bugger/secrets/c3_config.ini`, `~/.checkbox-jira-bugger/jira_project_config/jira_project_config.json`.

## Usage

    S=~/.config/opencode/skills/oem-enablement-skills/checkbox-jira-bugger/scripts/file_bugs.py
    python3 $S ~/Downloads/submission_X_rerun.tar.xz -p <project> -m <FC|GM> -a <assignee> -l <label>

One command: preflight, dry run, then the real run if the dry run is clean. The CID is read from the tarball name (`--cid` overrides). Other options: `--allow-original` (accept a non-`_rerun` tarball), `--jira-secrets`, `--c3-config`, `--jira-config`.

## What it does

1. Preflight (no network): tarball is `*_rerun.tar.xz`, the three config files exist, `-p` and `-l` exactly match `jira_project_config.json`, milestone is FC/GM, CLI on PATH.
2. `checkbox-jira-bugger --dryrun ...`; aborts on non-zero exit, an `ERROR:`/`CRITICAL:` log line or a traceback.
3. Same command without `--dryrun`; the same checks apply to its output.

`--rerun-on-fail` is never used. Failed non-wireless jobs are filed without retry, by decision.

## Exit codes

0 filed (real run exit 0 and no `ERROR:`/`CRITICAL:` log line); 2 bad args / CID not derivable; 3 preflight failed; 4 dry run failed (nothing filed); 5 real run failed (may be partially filed).

On 2-5, report the stage and message to the user. Do not retry with different values.

## Caveats

- The label is checked against the config before any CLI call because the CLI creates a Jira component for an unknown label even under `--dryrun`.
- The CLI exits 0 when the Jira config file is missing; the script checks the file itself.
- For jobs that passed, the tool may attach the tarball to existing open bugs.
- The dry run is a strong check, not a guarantee: Jira/C3 state can change before the real run, which is a separate invocation.
- Exit 5 can leave some bugs already filed; check Jira before re-running to avoid duplicates.
