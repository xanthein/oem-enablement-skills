---
name: pc-image-sanity
description: Use when sanity-testing an OEM PC image ISO on one or more DUTs (CIDs) before release: provision, pc-sanity-smoke-test-26-04, wireless rerun, Jira bugs.
version: 1.0.0
author: dirk
license: MIT
metadata:
  hermes:
    tags: [testflinger, checkbox, image, sanity, wireless, jira, oem-enablement]
    related_skills: [render_testflinger, testflinger, checkbox-rerun, checkbox-jira-bugger]
---

# PC image sanity batch

## When to Use

You have an OEM PC ISO and one or more DUTs (CIDs, queue name = CID) and want, per CID: provision the ISO, run `pc-sanity-smoke-test-26-04`, reserve the DUT, verify the image, re-run wireless jobs, file Jira bugs, release the DUT. CIDs run in parallel.

## Required inputs

Stop and ask the user if any is missing; never guess.

- CIDs (`YYYYMM-NNNNN`): repeat `--cid`, or `--cids-file` (one per line, `#` comments ok). Both may be combined.
- `--iso` URL (reachable; HEAD-checked).
- `--form-factor laptop|desktop`
- `--lp-user` (Launchpad id; DUT gets `ssh_keys: lp:<user>`)
- Jira `-p -m -a -l` (passed verbatim to `file_bugs.py`, once per batch)

## Prereqs

- Jira args: `-p/-m/-l` are validated up front (via `checkbox-jira-bugger`'s `preflight`: project and label must exist in `jira_project_config.json`, milestone FC/GM, secrets/config files present), even in `--dry-run`, so a bad value fails before any job is submitted. A wrong label error lists the valid labels.
- Testflinger login: the script checks this first, even in `--dry-run`. On exit 3 with `Testflinger not logged in`, ask the user to run `testflinger login` themselves, then retry. Never enter credentials.
- `sshpass`, `jinja2`, `git`; the manifest repo at `~/source/canonical/ce-oem-dut-checkbox-configuration` (`--repo`; only `git fetch` + `git show`, never checkout; `--no-fetch` to skip fetch).
- Sibling skills installed with their prereqs: `render_testflinger`, `checkbox-rerun` (see its prereqs), `checkbox-jira-bugger` (its Jira config files).

## Usage

    S=~/.config/opencode/skills/oem-enablement-skills/pc-image-sanity/scripts/pc_image_sanity.py
    ARGS=(--cid 202601-38271 --cid 202601-38272 --iso <ISO URL> --form-factor laptop \
          --lp-user <lp> -p <proj> -m <FC|GM> -a <assignee> -l <label>)

    # 1. always dry-run first (renders job.yaml only, submits nothing)
    python3 $S "${ARGS[@]}" --dry-run

    # 2. real run
    python3 $S "${ARGS[@]}"

Show the user the dry-run per-CID manifest source lines before the real run. Manifest order: `--manifest FILE` (`file:`) > `origin/<CID>:pc/<CID>/manifest.json` (`branch:`) > `origin/main:pc/<CID>/manifest.json` (`main:`) > bundled `manifests/manifest_<form-factor>.json` (`bundled:`). `pc/default` is never used.

The real run takes hours: start it with the shell tool's `background: true` and do not poll; you are notified on completion. When done, report the summary table and the `results.json` path to the user.

## What it does (per CID)

1. Render provision-and-test job (`pc-sanity-smoke-test-26-04`) + `reserve_data` (timeout 7200 s = 2 h ceiling), submit to queue = CID.
2. Wait for state `reserve`, then get the DUT IP (up to 10 tries, 30 s apart). The job itself copies the submission tarball onto the DUT (`/home/ubuntu/submission_<CID>.tar.xz`) at the end of the test phase; Testflinger artifacts are only uploaded after the job ends, so they are used only as a best-effort record when a job ends without reaching reserve.
3. Image check: build id (`YYYYMMDD-N`) from `ubuntu-report show | grep DCD` on the DUT must equal the build id in the ISO name; mismatch fails the CID. Then fetch the submission from the DUT over SSH (`scp`, ubuntu/insecure).
4. `checkbox-rerun` (timeout 6000 s) on the submission. Then the DUT is released (`testflinger cancel`, Jira does not need it), the CID waits for a batch-wide Jira lock (stage `jira_wait`) and runs `checkbox-jira-bugger` on the rerun tarball. Only one CID files at a time, with a 5 s pause before the next, so parallel CIDs do not file duplicate bugs.
5. In `finally`, `testflinger cancel` releases the DUT unless the job already ended or was already released in step 4.

Progress: each stage change and key event prints `[HH:MM:SS] <CID>  <message>` to stderr (stage, job id, DUT IP, image check, release, Jira lock/exit, done/failed).

Options: `--workdir` (default `/tmp/opencode/pc-image-sanity/<YYYYmmdd-HHMMSS>/<CID>/`), `--poll-interval` (60 s), `--max-wait` (43200 s to reach reserve).

## Results and resume

`<batch>/results.json` is rewritten at every stage (iso, form_factor, per-CID status/stage/job_id/manifest_source/submission/rerun_tarball/dut_ip/image_ok/jira_exit/released/error); logs in `<batch>/<CID>/rerun.log`, `jira.log`.

To continue an interrupted batch, repeat the same required args and add `--resume <batch>/results.json`: CIDs already `done` are skipped; a CID whose saved job is not yet released and in a post-submit stage reuses that job; otherwise it is resubmitted. A CID saved at stage `jira_wait`/`jira` whose `rerun_tarball` still exists resumes at the Jira step only (its DUT is already released). The batch dir is reused.

## Exit codes

0 all CIDs done · 1 any CID failed · 2 bad args / unreadable `--resume` / no CIDs · 3 preflight failed (not logged in, bad Jira -p/-m/-l, missing sibling/repo/manifest, ISO unreachable) · 130 interrupted (Ctrl-C, SIGTERM or SIGHUP: submitted jobs are cancelled on a best-effort basis, results still written).

`--dry-run` still does the login check, ISO HEAD check and `git fetch`; it only skips submitting.

## Verify on the first real run

- A failed test phase still reaches `reserve`.
- The submission lands on the DUT at `/home/ubuntu/submission_<CID>.tar.xz`.
- `testflinger status` prints the expected strings and the reserve output contains `ubuntu@<ip>`.
- ubuntu/insecure password login works on the new image.
- `testflinger cancel` releases the DUT promptly.

## Caveats

- `after-suspend-wireless` jobs suspend the DUT during rerun.
- A CID that fails before reaching `reserve` (provision/test crash) is `failed`: no rerun, no Jira bugs; Testflinger artifacts are still fetched best-effort for the record.
- One CID failing does not affect the others.
