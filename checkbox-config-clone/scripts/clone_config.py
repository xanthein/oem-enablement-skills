#!/usr/bin/env python3
"""Clone checkbox.conf/manifest.json from a machine with the same C3 canonical_label."""
import argparse
import json
import os
import re
import subprocess
import sys

FILES = ("checkbox.conf", "manifest.json")
CID_RE = re.compile(r"^\d{6}-\d{5}$")
C3_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "c3", "scripts", "c3_api.py")


class CloneError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def git(repo, *args, check=True):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise CloneError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout.strip() if p.returncode == 0 else None


def donor_files(repo, cid):
    for ref in ("origin/main", f"origin/{cid}"):
        if git(repo, "rev-parse", "--verify", "-q", ref, check=False) is None:
            continue
        present = [f for f in FILES
                   if subprocess.run(["git", "-C", repo, "cat-file", "-e", f"{ref}:pc/{cid}/{f}"],
                                     capture_output=True).returncode == 0]
        if present:
            return ref, present
    return None


def find_donors(repo, new_cid, machines):
    donors = []
    for m in machines:
        cid = m["canonical_id"]
        if cid == new_cid or not CID_RE.match(cid):
            continue
        found = donor_files(repo, cid)
        if found:
            donors.append({"cid": cid, "holder": m["holder"]["name"],
                           "ref": found[0], "files": found[1]})
    return donors


def pick_donor(donors, from_cid):
    if from_cid:
        for d in donors:
            if d["cid"] == from_cid:
                return d
        raise CloneError(f"{from_cid} is not a donor with the same label and existing files")
    if not donors:
        raise CloneError("no machine with the same label has files in pc/ "
                         "(try utils/manifest-update.py for defaults)")
    if len(donors) > 1:
        lines = [f"  {d['cid']}\t{d['holder']}\t{d['ref']}\t{','.join(d['files'])}" for d in donors]
        raise CloneError("multiple donors, rerun with --from <cid>:\n" + "\n".join(lines), code=2)
    return donors[0]


def c3(*args):
    p = subprocess.run([sys.executable, C3_SCRIPT, *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise CloneError(f"c3 {' '.join(args)} failed: {p.stderr.strip()}")
    return json.loads(p.stdout)


def clone(repo, new_cid, donor, label):
    if git(repo, "status", "--porcelain"):
        raise CloneError("working tree has uncommitted changes")
    if git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{new_cid}", check=False) is not None \
            or git(repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{new_cid}", check=False) is not None:
        raise CloneError(f"branch {new_cid} already exists")
    if git(repo, "ls-tree", "-r", "--name-only", "origin/main", f"pc/{new_cid}/"):
        raise CloneError(f"pc/{new_cid}/ already exists on origin/main")
    raw = {}
    for f in donor["files"]:
        raw[f] = subprocess.run(["git", "-C", repo, "show", f"{donor['ref']}:pc/{donor['cid']}/{f}"],
                                capture_output=True).stdout
    git(repo, "switch", "-q", "-c", new_cid, "origin/main")
    os.makedirs(os.path.join(repo, "pc", new_cid))
    for f, data in raw.items():
        with open(os.path.join(repo, "pc", new_cid, f), "wb") as fh:
            fh.write(data)
    git(repo, "add", f"pc/{new_cid}")
    git(repo, "commit", "-q", "-m",
        f"Add config for {new_cid} (cloned from {donor['cid']} {donor['ref']}, label {label})")
    return git(repo, "rev-parse", "HEAD")


def main(argv=None, c3_call=c3):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("new_cid")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--from", dest="from_cid")
    a = ap.parse_args(argv)
    try:
        if not CID_RE.match(a.new_cid):
            raise CloneError(f"invalid CID: {a.new_cid}")
        git(a.repo, "fetch", "-q", "origin", "--prune")
        label = c3_call("show", a.new_cid)["canonical_label"]
        machines = c3_call("get", "machines/", f"canonical_label={label}")
        donor = pick_donor(find_donors(a.repo, a.new_cid, machines), a.from_cid)
        clone(a.repo, a.new_cid, donor, label)
        print(f"Created branch {a.new_cid} from {donor['cid']} ({donor['ref']}): {','.join(donor['files'])}")
        return 0
    except CloneError as e:
        print(str(e), file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
