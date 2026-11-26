#!/usr/bin/env python3
"""Submit a Testflinger job, authenticating from ~/.config/secrets/tf_secrets.ini.

Prints only the job id on stdout. Falls back to the CLI's own login when the
credentials file is missing or incomplete.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_TF_SECRETS = "~/.config/secrets/tf_secrets.ini"
TF_CRED_KEYS = ("TESTFLINGER_CLIENT_ID", "TESTFLINGER_SECRET_KEY")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _parse_dotenv(text):
    found = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        if sep:
            found[key] = val
    return found


def load_tf_credentials(path=None):
    """Return both credential keys, or None unless both are set."""
    try:
        text = Path(os.path.expanduser(path or DEFAULT_TF_SECRETS)).read_text()
    except (OSError, UnicodeDecodeError):
        return None
    found = _parse_dotenv(text)
    if all(found.get(k) for k in TF_CRED_KEYS):
        return {k: found[k] for k in TF_CRED_KEYS}
    return None


def submit(job_yaml, secrets=None, timeout=300):
    creds = load_tf_credentials(secrets)
    env = {**os.environ, **creds} if creds else None
    # The testflinger snap cannot read /tmp, so the job goes in via stdin.
    r = subprocess.run(
        ["testflinger", "submit", "--quiet", "-"],
        input=Path(job_yaml).read_text(),
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not UUID_RE.match(out):
        hint = ("check the credentials file" if creds
                else "no credentials file; run `testflinger login`")
        raise RuntimeError(f"testflinger submit failed ({hint}): {out} {r.stderr or ''}".strip())
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("job_yaml")
    p.add_argument("--secrets", default=DEFAULT_TF_SECRETS,
                   help=f"dotenv credentials file (default {DEFAULT_TF_SECRETS})")
    a = p.parse_args(argv)
    if load_tf_credentials(a.secrets) is None:
        print(f"warning: no usable credentials in {a.secrets}; using CLI login",
              file=sys.stderr)
    try:
        print(submit(a.job_yaml, a.secrets))
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
