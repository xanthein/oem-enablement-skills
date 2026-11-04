#!/usr/bin/env python3
"""File Jira bugs from a checkbox-rerun tarball via checkbox-jira-bugger.

Preflight -> dry run -> real run. Never uses --rerun-on-fail or -i.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

CLI = "checkbox-jira-bugger"
DEFAULT_SECRETS = "~/.checkbox-jira-bugger/secrets/jira_secrets.ini"
DEFAULT_C3 = "~/.checkbox-jira-bugger/secrets/c3_config.ini"
DEFAULT_JIRA_CONFIG = "~/.checkbox-jira-bugger/jira_project_config/jira_project_config.json"

EXIT_BAD_ARGS, EXIT_PREFLIGHT, EXIT_DRYRUN, EXIT_REAL = 2, 3, 4, 5


class PreflightError(Exception):
    pass


def cid_from_tarball(path):
    m = re.match(r"^submission_(\d{6}-\d{4,6})_", os.path.basename(path))
    return m.group(1) if m else None


def is_rerun_tarball(path):
    return os.path.basename(path).endswith("_rerun.tar.xz")


def preflight(tarball, project, label, milestone, jira_secrets, c3_config,
              jira_config, allow_original):
    if shutil.which(CLI) is None:
        raise PreflightError(f"{CLI} not found on PATH")
    for path in (tarball, jira_secrets, c3_config, jira_config):
        if not os.path.isfile(path):
            raise PreflightError(f"file not found: {path}")
    if not is_rerun_tarball(tarball) and not allow_original:
        raise PreflightError(
            f"{tarball} is not a *_rerun.tar.xz; run checkbox-rerun first "
            "or pass --allow-original")
    milestone = milestone.upper()
    if milestone not in ("FC", "GM"):
        raise PreflightError(f"invalid milestone {milestone}, must be FC or GM")
    try:
        with open(jira_config) as f:
            projects = json.load(f)["OEM_X86_JIRA_CONF"]
    except (OSError, ValueError, KeyError) as exc:
        raise PreflightError(f"cannot read jira config {jira_config}: {exc}")
    if project not in projects:
        raise PreflightError(
            f"project {project} not in jira config; known: {sorted(projects)}")
    labels = [c["component_name"] for c in projects[project].get("jira_components", [])]
    if label not in labels:
        raise PreflightError(
            f"label {label} not configured for project {project}; valid: {labels}")
    return milestone


def build_cmd(tarball, project, label, milestone, assignee, cid,
              jira_secrets, c3_config, jira_config, dryrun):
    cmd = [CLI, "-p", project, "-l", label, "-m", milestone, "-a", assignee,
           "--cid", cid, "-s", jira_secrets, "-3", c3_config, "-c", jira_config]
    if dryrun:
        cmd.append("--dryrun")
    cmd.append(tarball)
    return cmd


def output_has_error(text):
    return bool(re.search(r"^JIRA:\s*(ERROR|CRITICAL):", text, re.M)) or \
        "Traceback (most recent call last)" in text


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tarball")
    ap.add_argument("-p", "--project", required=True)
    ap.add_argument("-m", "--milestone", required=True)
    ap.add_argument("-a", "--assignee", required=True)
    ap.add_argument("-l", "--label", required=True)
    ap.add_argument("--cid")
    ap.add_argument("--allow-original", action="store_true")
    ap.add_argument("--jira-secrets", default=os.path.expanduser(DEFAULT_SECRETS))
    ap.add_argument("--c3-config", default=os.path.expanduser(DEFAULT_C3))
    ap.add_argument("--jira-config", default=os.path.expanduser(DEFAULT_JIRA_CONFIG))
    a = ap.parse_args(argv)

    cid = a.cid or cid_from_tarball(a.tarball)
    if not cid:
        print("cannot derive CID from tarball name; pass --cid", file=sys.stderr)
        return EXIT_BAD_ARGS
    try:
        milestone = preflight(a.tarball, a.project, a.label, a.milestone,
                              a.jira_secrets, a.c3_config, a.jira_config,
                              a.allow_original)
    except PreflightError as exc:
        print(f"preflight failed: {exc}", file=sys.stderr)
        return EXIT_PREFLIGHT

    def cmd(dryrun):
        return build_cmd(a.tarball, a.project, a.label, milestone, a.assignee,
                         cid, a.jira_secrets, a.c3_config, a.jira_config, dryrun)

    dry = subprocess.run(cmd(True), capture_output=True, text=True)
    sys.stdout.write(dry.stdout)
    sys.stderr.write(dry.stderr)
    if dry.returncode != 0 or output_has_error(dry.stdout + dry.stderr):
        print("dry run failed; nothing was filed", file=sys.stderr)
        return EXIT_DRYRUN

    real = subprocess.run(cmd(False), capture_output=True, text=True)
    sys.stdout.write(real.stdout)
    sys.stderr.write(real.stderr)
    if real.returncode != 0 or output_has_error(real.stdout + real.stderr):
        print(f"real run failed (exit {real.returncode}); some bugs may be filed",
              file=sys.stderr)
        return EXIT_REAL
    return 0


if __name__ == "__main__":
    sys.exit(main())
