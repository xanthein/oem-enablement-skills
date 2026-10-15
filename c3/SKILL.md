---
name: c3
description: Use when finding a DUT's IP in Canonical C3 by CID/label.
version: 1.0.0
author: dirk
license: MIT
metadata:
  hermes:
    tags: [c3, certification, dut, oem-enablement, lab]
    related_skills: [ssh]
---

# C3 API (certification.canonical.com)

## When to Use

Look up lab DUT info in C3: find a machine's IP from its Canonical ID (CID) or canonical_label, then SSH to it (see the `ssh` skill).

## Secrets (never put them in this skill)

The tool reads OAuth client credentials from an INI file (chmod 600, outside any repo). Default location: ~/.config/c3/secrets.ini

    [c3 secret]
    client_id = <id>
    client_secret = <secret>

If the file is missing, stop and ask the user to create it. Never ask for or print the values in chat. Do not cat the file.

## Usage

Script: scripts/c3_api.py in this skill dir (source: https://git.launchpad.net/~dirksu/+git/tools/tree/transfer_dut/c3_api.py). Needs python3 with `requests`; system python3 lacks it, so use the Hermes venv python.

    PY=/home/dirk/.hermes/hermes-agent/venv/bin/python
    S=~/.hermes/skills/oem-enablement/c3/scripts/c3_api.py

    # IP by CID
    $PY $S --secrets ~/.config/c3/secrets.ini get-ip 202401-12345

    # IP by canonical_label (add --holder if several machines share the label)
    $PY $S --secrets ~/.config/c3/secrets.ini get-ip-by-label <label> [--holder <name>]

A canonical_label can match several machines (different holders). On the ambiguity error, retry with `--holder dirksu` (the user's own machines) rather than asking, and mention which other holder/CID also matched. Ticket CIDs map directly to `get-ip <cid>`. To get another holder's machine, take its canonical_id from the ambiguity log and run `get-ip <cid>`; such machines may sit on a different subnet (10.101.x vs 10.102.x).

IP is printed on stdout; DEBUG logs go to stderr, so capture with `2>/dev/null`. Exit code 1 if not found or ambiguous (candidates are listed in the log).

## Library use

`c3api(secrets_file)` in c3_api.py also offers c3_get/c3_patch/c3_post, get_linked_labresource, find_labresource, link/detach_labresource, update_machine_location. The write helpers (detach/link/update_machine_location/update_c3_info) MODIFY C3 records, and update_machine_location hardcodes holder 'kevinyeh'. Confirm with the user before any write.

## Notes

- If secrets.ini is world-readable (ls -l shows more than -rw-------), tell the user to `chmod 600` it. Do not cat it.
- Read-only commands (get-ip, get-ip-by-label) are safe to run freely.
- A token is fetched via client_credentials (scope 'read write') on every run.
