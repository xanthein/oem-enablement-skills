---
name: pc-image-sanity
description: Use when sanity-testing an OEM PC image ISO on one or more DUTs (CIDs) before release: provision, smoke test plan from the ISO codename, wireless rerun, Jira bugs.
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

You have an OEM PC ISO and one or more DUTs (CIDs, queue name = CID) and want, per CID: provision the ISO, run the smoke test plan, reserve the DUT, verify the image, re-run wireless jobs, release the DUT, file Jira bugs. You (the main agent) run each step yourself via `imgsanity.py`; CIDs run in parallel.

Test plan comes from the ISO codename: `noble` → `pc-sanity-smoke-test-24-04`, `resolute` → `pc-sanity-smoke-test-26-04`. `preflight --test-plan X` overrides. Unknown codename → preflight exit 3: ask the user which plan to use.

## Required inputs

Stop and ask the user if any is missing; never guess.

- CIDs (`YYYYMM-NNNNN`): repeat `--cid`, or `--cids-file` (one per line, `#` comments ok). Both may be combined.
- `--iso` URL (reachable; HEAD-checked).
- `--form-factor laptop|desktop`
- `--lp-user` (Launchpad id; DUT gets `ssh_keys: lp:<user>`)
- Jira `-p -m -a -l` (stored in the batch, passed to `file_bugs.py` by `jira`)

## Prereqs

- Jira `-p/-m/-l` are validated by `preflight` (project and label must exist in `jira_project_config.json`, milestone FC/GM, config files present). A wrong label error lists the valid labels.
- Testflinger login is checked first. On exit 3 with `Testflinger not logged in`, ask the user to run `testflinger login` themselves, then retry. Never enter credentials.
- `sshpass`, `jinja2`, `git`; manifest repo at `~/source/canonical/ce-oem-dut-checkbox-configuration` (`--repo`; only `git fetch` + `git show`; `--no-fetch` skips fetch).
- Sibling skills installed with their prereqs: `render_testflinger`, `checkbox-rerun`, `checkbox-jira-bugger`.

## Usage

    S=~/.config/opencode/skills/oem-enablement-skills/pc-image-sanity/scripts/imgsanity.py
    python3 $S preflight --cid 202601-38271 --cid 202601-38272 --iso <ISO URL> \
        --form-factor laptop --lp-user <lp> -p <proj> -m <FC|GM> -a <assignee> -l <label> [--test-plan X] [--no-fetch]

Last stdout line of every command is a JSON summary; progress and errors go to stderr. `preflight` prints `{step, ok, batch, test_plan, cids}`; use `batch` as `--batch` below.

1. For each CID run `python3 $S submit --batch B --cid C --dry-run` (renders `job.yaml`, submits nothing; summary has `job_yaml`, `manifest_source`). Show the user the test plan and manifest sources before the real run.
2. Real run, per CID, in order:

| Step | Command (`--batch B --cid C`) | Summary fields on success | Notes |
|---|---|---|---|
| submit | `submit` | `job_id`, `manifest_source` | refuses if a job is still held (`release` or `--force`; `--force` cancels the held job first, warns and continues if the cancel fails); `--dry-run` skips this check |
| wait | `wait` | `dut_ip` | shell `background: true`, do not poll; hours. `--poll-interval` (default 60 s), `--max-wait` (default 43200 s) |
| verify-image | `verify-image` | `dcd` | build id on DUT must match ISO name |
| fetch | `fetch` | `submission` | scp from DUT |
| rerun | `rerun` | `rerun_tarball` | `background: true`; logs `<C>/rerun.log` |
| release | `release` | `job_id` | cancels the held job; no-op if none; fails (exit 1, `job_held` stays true) if cancel fails and the job is not complete/cancelled |
| jira | `jira` | — | logs `<C>/jira.log` |

Manifest order: `--manifest FILE` (`file:`) > `origin/<CID>:pc/<CID>/manifest.json` (`branch:`) > `origin/main:...` (`main:`) > bundled `manifests/manifest_<form-factor>.json` (`bundled:`). `pc/default` is never used.

Run CIDs in parallel as separate background shell jobs from the main agent. Never use subagents for CID steps (only the main agent can ask the user). `jira` takes the batch-wide lock (`--lock-timeout`, default 3600 s), so parallel CIDs do not file duplicates. Read the last stdout JSON line after each step.

`status --batch B` prints a table (CID, STATUS, STAGE, JOB_ID, DUT_IP, ERROR) and writes `B/results.json` (full per-CID state incl. `job_held`).

## Exit codes

0 ok · 1 step failed (state set `failed`; summary has `step`, `ok:false`, `error`) · 2 bad args / unknown batch or CID / prerequisite step not run · 3 preflight failed (not logged in, bad Jira args, missing sibling/repo/manifest, ISO unreachable, unknown codename).

## Failure protocol

On non-zero exit stop that CID only (others continue). Show the user the step, the `error`, and the last ~20 lines of the relevant log (`rerun.log` / `jira.log`), then ask with the `question` tool, first option = suggested. Never retry or skip without the user's answer.

| Failure | Options |
|---|---|
| `wait`: job ended before reserve | inspect `<C>/artifacts` and resubmit (`submit`); skip CID |
| `wait`: timeout (`--max-wait` exceeded) | keep waiting (`wait` again, optionally larger `--max-wait`); `release` + `submit`; skip |
| `wait`: no DUT IP | rerun `wait`; check DUT in C3 (`c3` skill); `release` + `submit` |
| `verify-image`: mismatch | `release` + skip (wrong image, report it); `release` + `submit` |
| `verify-image`/`fetch`: SSH error | retry step; debug with `ssh` skill; `release` + `submit` |
| `rerun` failed | retry `rerun`; inspect `rerun.log`; skip CID with `release` |
| `jira` failed / lock timeout | fix config and retry `jira`; skip filing |

Always run `release` when a CID is skipped, stopped or aborted. If the user aborts, run `release` for every CID with `job_held` true (run `status --batch B`, then list them with `jq -r '.[]|select(.job_held)|.cid' B/results.json`). If `release` fails (cancel failed), tell the user the DUT may still be reserved.

## Verify on the first real run

- A failed test phase still reaches `reserve`.
- The submission lands on the DUT at `/home/ubuntu/submission_<CID>.tar.xz`.
- `testflinger status` prints the expected strings and the reserve output contains `ubuntu@<ip>`.
- ubuntu/insecure password login works on the new image.
- `testflinger cancel` (`release`) frees the DUT promptly.

## Caveats

- `after-suspend-wireless` jobs suspend the DUT during rerun.
- Reserve timeout is 7200 s; release promptly after `rerun`.
- A CID failing before `reserve` has no submission: no rerun, no Jira bugs.
- One CID failing does not affect the others.
