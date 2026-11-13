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

The tool reads OAuth client credentials from an INI file (chmod 600, outside any repo). Default location: ~/.config/secrets/c3_secrets.ini

    [c3 secret]
    client_id = <id>
    client_secret = <secret>

If the file is missing, stop and ask the user to create it. Never ask for or print the values in chat. Do not cat the file.

## Usage

Script: scripts/c3_api.py in this skill dir. Needs python3 with `requests` (present in system python3). Secrets default to ~/.config/secrets/c3_secrets.ini, so no `--secrets` is needed. Logs are quiet; add `-v` for DEBUG on stderr.

    S=~/.config/opencode/skills/oem-enablement-skills/c3/scripts/c3_api.py

    python3 $S get-ip 202401-12345                      # IP by CID
    python3 $S get-ip-by-label <label> [--holder H]     # IP by exact canonical_label
    python3 $S find-by-tag <launchpad_tag> [--holder H] # CID, label, holder per line
    python3 $S search <text> [--holder H]               # label contains text (case-insensitive)
    python3 $S show <cid>                               # machines/<cid>/ as JSON
    python3 $S submissions <cid>                        # submissions of a CID as JSON
    python3 $S get <endpoint> [key=value ...]           # raw read-only GET, JSON

Do not write inline python that imports c3_api. If a query is missing, use `get <endpoint> key=value` (e.g. `get machines/ canonical_label__icontains=turmeric`).

A canonical_label can match several machines (different holders). On the ambiguity error, retry with `--holder dirksu` (the user's own machines) rather than asking, and mention which other holder/CID also matched. Ticket CIDs map directly to `get-ip <cid>`. Machines of other holders may sit on a different subnet (10.101.x vs 10.102.x).

IP / table output goes to stdout, errors to stderr. Exit code 1 if not found or ambiguous.

## Library use

`c3api(secrets_file)` in c3_api.py also offers c3_get/c3_patch/c3_post, get_linked_labresource, find_labresource, link/detach_labresource, update_machine_location. The write helpers (detach/link/update_machine_location/update_c3_info) MODIFY C3 records, and update_machine_location hardcodes holder 'kevinyeh'. Confirm with the user before any write.

## Notes

- If secrets.ini is world-readable (ls -l shows more than -rw-------), tell the user to `chmod 600` it. Do not cat it.
- Read-only commands (every CLI subcommand) are safe to run freely.
- A token is fetched via client_credentials (scope 'read write') on every run.
