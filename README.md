# OEM Enablement Skills

Agent skills (OpenCode / Hermes style, each a `SKILL.md`) for Canonical OEM
enablement work: lab DUT access, Checkbox runs, Testflinger, Jira bug filing,
and Ubuntu package SRUs.

This README lists what you must set up before the skills work for you.

## Install

The skills hardcode this location in their example commands, so clone there:

```sh
git clone <this-repo> ~/.config/opencode/skills/oem-enablement-skills
```

OpenCode discovers each `*/SKILL.md` below that directory. Skills that ship
scripts call them as
`~/.config/opencode/skills/oem-enablement-skills/<skill>/scripts/...`.

Scratch output goes under `/tmp/opencode/`.

## Skills overview

| Skill | Purpose | Needs |
|---|---|---|
| `ssh` | SSH/SCP to a DUT (`ubuntu`/`insecure`), `dut` wrapper | `sshpass`; `c3` for CID lookup |
| `c3` | Find DUT IP / info in Canonical C3 by CID or label | C3 secrets file, python3 `requests` |
| `render_testflinger` | Render Testflinger job YAML from Jinja2 templates | python3 `jinja2` |
| `image-lookup` | Resolve the latest OEM PC image ISO URL, wait for cache | network access to tel-image-cache |
| `checkbox-config-clone` | Create `checkbox.conf`/`manifest.json` for a new CID from a same-label donor | `c3`, local config repo |
| `checkbox-rerun` | Re-run failed wireless jobs of a submission on the DUT, repack | `sshpass`, `c3`, `ssh` |
| `checkbox-jira-bugger` | File Jira bugs from a `*_rerun.tar.xz` | `checkbox-jira-bugger` CLI + 3 config files |
| `pc-image-sanity` | End-to-end batch: provision ISO, smoke test, rerun, release, Jira | Testflinger auth + all sibling skills |
| `launchpad` | Read/update Launchpad via `lp-api` | `oem-scripts`, `httpie`, `jq`, LP auth |
| `alsa-ucm-conf-sru` | alsa-ucm-conf SRU: bug, branches, PPA | `launchpad`, git-ubuntu, packaging tools |
| `libfprint-sru` | libfprint SRU patch with quilt/DEP-3 | packaging tools, a libfprint checkout |
| `ubuntu-version-string` | Rules for choosing `debian/changelog` versions | `rmadison` (`ubuntu-dev-tools`) |

## Dependency graph

```
pc-image-sanity ─┬─ render_testflinger
                 ├─ checkbox-rerun ───┬─ ssh ─── c3
                 │                    └─ c3
                 ├─ checkbox-jira-bugger ─ c3 (via its own config)
                 └─ (manifests: checkbox-config-clone repo)
checkbox-config-clone ─ c3
alsa-ucm-conf-sru ─ launchpad, ubuntu-version-string
```

## Required tools

Install the ones for the skills you use.

```sh
sudo apt install python3 python3-requests python3-jinja2 sshpass git jq httpie \
    quilt devscripts ubuntu-dev-tools dput git-ubuntu   # last group: SRU skills
```

Also needed:
- `checkbox-jira-bugger` CLI from <https://github.com/xanthein/checkbox-jira-bugger>
  (see below)
- `testflinger` CLI (pc-image-sanity)
- `lp-api` from `oem-scripts` (launchpad, SRU skills)
- Network/VPN access to the lab, C3, Jira, and `tel-image-cache.canonical.com`

## checkbox-jira-bugger (external tool)

The `checkbox-jira-bugger` and `pc-image-sanity` skills call this separate
CLI, which must be on `PATH`:

```sh
git clone https://github.com/xanthein/checkbox-jira-bugger
pipx install ./checkbox-jira-bugger     # or: pip install -e . in a venv
checkbox-jira-bugger --help
```

Its config files (details in its README):
- `~/.config/secrets/jira_secrets.ini`: `[jira account]` `jira_email`, `jira_apitoken`, and
  `[mattermost token]` `mt_token`
- `~/.config/secrets/c3_secrets.ini`: `[c3 secret]` `client_id`, `client_secret`
  (same file the `c3` skill uses)
- `~/.checkbox-jira-bugger/jira_project_config/jira_project_config.json`: generate with `checkbox-jira-config-generator`
- `~/.checkbox-jira-bugger/secrets/dut_ssh_config.ini`: only for `--rerun-on-fail`, which these skills never use

## Configuration and secrets

Keep secrets out of the repo, `chmod 600`, and never paste them into chat.
The skills are written to check these files without printing them.

| File | Used by | Contents |
|---|---|---|
| `~/.config/secrets/c3_secrets.ini` | `c3`, `checkbox-config-clone`, `checkbox-rerun`, `checkbox-jira-bugger` | INI: `[c3 secret]` with `client_id`, `client_secret` |
| `~/.config/secrets/tf_secrets.ini` | `pc-image-sanity` | dotenv: `TESTFLINGER_CLIENT_ID=`, `TESTFLINGER_SECRET_KEY=` (alternative: run `testflinger login`) |
| `~/.config/pc-image-sanity.ini` | `pc-image-sanity` (optional) | dotenv: `LP_USER=`, `JIRA_ASSIGNEE=`, `JIRA_PROJECT=` |
| `~/.config/secrets/jira_secrets.ini` | `checkbox-jira-bugger`, `pc-image-sanity` | Jira + Mattermost credentials (see section above) |
| `~/.checkbox-jira-bugger/jira_project_config/jira_project_config.json` | same | Valid Jira projects and platform labels |
| `~/.config/oem-scripts/config.ini` or `LAUNCHPAD_TOKEN` | `launchpad`, SRU skills | Launchpad OAuth; created by running `lp-api get people/+me` once and authorizing in the browser |

Example `c3_secrets.ini`:

```ini
[c3 secret]
client_id = <id>
client_secret = <secret>
```

Example `~/.config/pc-image-sanity.ini`:

```
LP_USER=mylpid
JIRA_ASSIGNEE=me
JIRA_PROJECT=PROJ
```

Flags override the file. Label, milestone, ISO, CIDs and form factor are always
per-run.

## Local repositories

- `~/source/canonical/ce-oem-dut-checkbox-configuration`: used by
  `checkbox-config-clone` and `pc-image-sanity` (override with `--repo`).
  `pc-image-sanity` only runs `git fetch` and `git show` on it.
- A libfprint packaging checkout (salsa.debian.org) for `libfprint-sru`.

## Things to adapt to your own setup

Some skills contain the original author's values. Change them before use:

- `libfprint-sru/SKILL.md`: repo path `/home/dirk/project/sru/libfprint/libfprint`
  and remote names (`origin` = personal fork).
- `alsa-ucm-conf-sru/SKILL.md`: `git push dirksu <branch>` and `~USER` PPA names,
  GPG key id for `dpkg-buildpackage -k`.
- `c3/scripts/c3_api.py`: `update_machine_location` hardcodes holder
  `kevinyeh` (write helper, not used by the CLI subcommands).
- `c3` skill: the `--holder dirksu` hint for ambiguous labels.
- `ssh` skill: DUT credentials `ubuntu`/`insecure` are the lab defaults.
- `render_testflinger` templates hard-code lab values (SSID env vars, BT
  addresses, iperf targets). Copy and pass with `-t` to change them.

## Tests

Scripts have pytest tests (`ssh` uses a shell test):

```sh
python3 -m pytest c3/tests checkbox-config-clone/tests checkbox-jira-bugger/tests \
    checkbox-rerun/tests image-lookup/tests pc-image-sanity/tests
bash ssh/tests/test_dut.sh
```

## Repo layout

```
<skill>/SKILL.md      instructions the agent loads
<skill>/scripts/      helper CLIs / templates
<skill>/tests/        tests for the helpers
launchpad/references/ extra workflow notes
docs/superpowers/     design specs and plans
```
