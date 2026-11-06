---
name: render_testflinger
description: Use when you need to render a Testflinger job YAML (provision, test, or provision-and-test) from the bundled Jinja2 templates.
version: 1.0.0
author: dirk
license: MIT
metadata:
  hermes:
    tags: [testflinger, jinja2, checkbox, oem-enablement]
    related_skills: [pc-image-sanity, checkbox-rerun]
---

# Render Testflinger job

## When to Use

You need a Testflinger job YAML for a DUT queue (usually the CID, e.g. `202602-38401`): provision an ISO, run a Checkbox test plan, or both.

## Prereqs

- `python3` with `jinja2` (`pip install jinja2`).

## Usage

```bash
R=~/.config/opencode/skills/oem-enablement-skills/render_testflinger/scripts/render_testflinger.py

# provision only
$R provision -q <queue> --url <iso-url> -o provision.yaml

# test only (unit is the plan name, without the com.canonical.certification:: prefix)
$R test -q <queue> -u <unit> [--manifest manifest.json] -o job.yaml

# provision + test
$R provision-and-test -q <queue> --url <iso-url> -u <unit> [--manifest manifest.json] -o job.yaml
```

- Output defaults to stdout (`-o -`).
- `--manifest` is a JSON object; its keys are written to the launcher `[manifest]` section.
- `-t FILE` uses a custom template instead of the bundled one in `scripts/`.
- Submit the result with the `testflinger` CLI.

## Pitfalls

- `test_job.yaml.j2` and `provision_and_test_job.yaml.j2` hard-code lab values (SSIDs via env vars, BT addresses, iperf targets, stress iteration counts). Copy a template and pass it with `-t` to change them.
- The submission tarball is named `submission_<queue>.tar.xz`.
