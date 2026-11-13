---
name: ssh
description: Use when SSH'ing to a DUT. Default creds ubuntu/insecure.
version: 1.0.0
author: dirk
license: MIT
metadata:
  hermes:
    tags: [ssh, dut, oem-enablement, lab]
    related_skills: []
---

# SSH to DUT (Device Under Test)

## When to Use

Use this skill whenever connecting to an OEM certification lab test device (DUT) over SSH or SCP, or when credentials for a DUT are needed.

Lab test devices (Canonical OEM hardware certification) use default credentials:

- username: ubuntu
- password: insecure

## Usage

Use the `dut` wrapper (credentials, host-key options and CID lookup built in). `<host>` is an IP, hostname or a CID such as 202611-39773 (resolved via the `c3` skill).

    D=~/.config/opencode/skills/oem-enablement-skills/ssh/scripts/dut

    $D run <host> <cmd...>              # run a command; exit status is the remote one
    $D cp ./file <host>:/tmp/           # scp (also $D cp <host>:/path ./)
    $D wait <host> [timeout=600]        # wait until ssh answers again
    $D reboot-wait <host> [timeout=600] # sudo reboot, wait for it to return, print uname -r

Pass a whole remote pipeline as one quoted string: `$D run <host> 'lsusb | grep 0781'`.

### Fallback (raw ssh)

    sshpass -p insecure ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ubuntu@<DUT_IP_OR_HOSTNAME>
    sshpass -p insecure scp -o StrictHostKeyChecking=no <file> ubuntu@<DUT_IP_OR_HOSTNAME>:<path>

If `sshpass` isn't installed: `sudo apt install sshpass`.

## Running privileged commands on a DUT

The ubuntu user has passwordless sudo on DUTs. Check with `sudo -n true` and then run plain `sudo <cmd>` over ssh. Never pipe the password with `sudo -S` (the tool blocks it as password guessing).

For long or noisy steps (apt-get install, update), add `DEBIAN_FRONTEND=noninteractive` and `-y`, and trim output with `| tail -40` on the remote side.

## Common DUT recipes

- Enable resolute-proposed: write `deb http://archive.ubuntu.com/ubuntu <codename>-proposed main restricted universe multiverse` to /etc/apt/sources.list.d/<codename>-proposed.list, then `apt-get update`. Get the codename from `lsb_release -cs`. Then `apt-get install -y <pkg>` (explicit package names need no pinning). Installing a kernel regenerates grub and makes it the default; do not reboot unless asked.
- Warm reboot then wait for return: `$D reboot-wait <host>` (prints the new kernel). For a single check use `$D wait <host>`.
- Multi-iteration reboot loops (reproducing an intermittent bug N times) run past the foreground terminal timeout (~180-420s) partway through an iteration. Launch the whole loop with `terminal(background=true, notify=True)` from the start and poll/wait via `process_manage`, writing each iteration's result to a log file as you go — don't let a foreground call get killed mid-reboot and have to figure out which iteration it died on.
- Before reproducing a bug on a DUT, compare its kernel (`uname -r`), BIOS (`sudo dmidecode -s bios-version`) and attached peripherals (`lsusb`) with the ticket. If they differ (custom kernel, missing drive), tell the user and suggest a sibling DUT from the same ticket instead of running the repro blindly.
- Find the DUT IP from a CID or canonical_label with the `c3` skill.

## Notes

- These are default/known lab credentials for certification test devices, not production secrets — safe to use directly in commands without going through the credential vault.
- Always pass -o StrictHostKeyChecking=no since DUTs get re-imaged often and host keys change.
- If the DUT hostname/IP isn't known, check the Jira ticket (STELLA project) or ask the user.
