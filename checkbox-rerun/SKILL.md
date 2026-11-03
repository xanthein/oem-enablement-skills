---
name: checkbox-rerun
description: Use when re-running failed/skipped wireless jobs of a Checkbox submission tarball (submission_*.tar.xz) on the real DUT and repacking the updated submission.
version: 1.0.0
author: dirk
license: MIT
metadata:
  hermes:
    tags: [checkbox, wireless, submission, rerun, oem-enablement]
    related_skills: [ssh, c3]
---

# Checkbox wireless re-run

## When to Use

A submission (e.g. `submission_202607-39079_517476.tar.xz`) has failed or skipped `com.canonical.certification::wireless/*` or `after-suspend-wireless/*` jobs, and you want to re-run them on the DUT and ship a corrected tarball.

## Prereqs

- `sshpass` installed locally; DUT login ubuntu/insecure (see the `ssh` skill).
- DUT IP: pass `--host`, or let the script look it up in C3 from the CID (taken from the tarball name). C3 needs secrets and a python with `requests` (see the `c3` skill): `--c3-python`, `--c3-script`, `--c3-secrets`.

## Usage

    S=~/.config/opencode/skills/oem-enablement-skills/checkbox-rerun/scripts/rerun.py

    # 1. always preview first (no DUT access)
    python3 $S ~/Downloads/submission_X.tar.xz --host <ip> --dry-run

    # 2. real run
    python3 $S ~/Downloads/submission_X.tar.xz --host <ip>

Output: `submission_X_rerun.tar.xz` next to the input (`--output` to change). The original is never modified.

Options: `--all` (re-run passing jobs too), `--only TEXT` (job id filter), `--env K=V` (override AP SSID/PSK; defaults come from the submission's launcher environment), `--timeout` (per job, default 900s), `--force-host` (skip the DUT identity check), `--force` (overwrite an existing output; the original submission is never overwritten).

## What it does

1. Extracts the tarball under `/tmp/opencode/checkbox-rerun/`.
2. Before any job, checks the DUT's DMI serial/uuid match the submission. Mismatch aborts (exit 3). Never use `--force-host` unless the user confirms the machine.
3. Runs `sudo <WIFI ENV> checkbox-cli run ...::device ...::<job>` over SSH per job. Jobs whose interface suffix (e.g. `wlp194s0`) is missing on the DUT are skipped.
4. Only a real **pass** changes a result: status/outcome/io_log/comments are updated, the original is kept in a `rerun` field, `test_output/` is replaced, and `submission.junit` loses the matching `<failure>`. Everything else is left untouched.
5. Writes `RERUN.md` (per-job table) into the tarball.

## Caveats (tell the user)

- `after-suspend-wireless/*` jobs depend on Checkbox's automated suspend job (`suspend/suspend_advanced_auto`), so the DUT really suspends and resumes during the re-run. Warn the user: the DUT is unreachable for a while and a job can fail because a dependency re-ran.
- `submission.html` is NOT updated and still shows the original run.
- Job outcome is read from the checkbox-cli final summary line whose job name equals the result's `name` in submission.json (the summary also lists dependency jobs). `job cannot be started` = skip; no matching line = "unknown". Only "pass" changes anything.
- Exit codes: 0 ok / nothing to re-run, 2 DUT undeterminable or bad args, 3 DUT identity mismatch/unverifiable/unreachable, 4 output path refused, 130 interrupted (jobs finished so far are still saved).
- The identity check needs a usable serial or uuid in the submission (placeholders like "To be filled by O.E.M." are ignored). With none, the run aborts unless `--force-host`.
- Jobs skipped because of the original run's manifest (`unmet manifest`) will skip again: only the Wi-Fi environment is passed, not the manifest.
- `--all`: jobs that were already pass are reported as "no (already pass)" and never modified.
- A job that times out leaves its `checkbox-cli` running on the DUT; check the DUT before re-running.
- Passwords go via `SSHPASS` (not argv); PSKs are masked in `--dry-run`, but they appear in the remote command line and may be in the saved `io_log`.
