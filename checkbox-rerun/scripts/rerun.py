#!/usr/bin/env python3
"""Re-run non-passing wireless jobs of a Checkbox submission on the DUT."""
import argparse
import copy
import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from xml.sax.saxutils import unescape

PREFIXES = ("wireless/", "after-suspend-wireless/")
ENV_RE = re.compile(r"^(WPA3?|OPEN)_[A-Z]+_(SSID|PSK)$")
PROVIDER = "com.canonical.certification"
PLACEHOLDERS = {
    "", "none", "default string", "to be filled by o.e.m.", "not specified",
    "system serial number", "not applicable", "unknown", "0", "n/a",
}
SUMMARY_RE = re.compile(r"^\s+job ([a-z ]+?)\s*:\s(.+?)\s*$", re.M)
CANNOT_RE = re.compile(r"^job cannot be started \(.*?\):\s(.+?)\s*$", re.M)
OUTCOME_MAP = {
    "passed": "pass",
    "failed": "fail",
    "crashed": "fail",
    "skipped": "skip",
    "cannot start": "skip",
}


def select_jobs(results, all_):
    return [
        r
        for r in results
        if r["id"].startswith(PREFIXES) and (all_ or r.get("status") != "pass")
    ]


def wifi_env(launcher, overrides):
    env = {k: v for k, v in launcher.get("environment", {}).items() if ENV_RE.match(k)}
    env.update(overrides)
    return env


def build_command(job_id, env):
    pairs = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    prefix = f"sudo {pairs} " if pairs else "sudo "
    return (
        f"{prefix}checkbox-cli run {PROVIDER}::device {PROVIDER}::{job_id}"
    )


def parse_outcome(output, name):
    """Outcome of the job called `name` in checkbox-cli's final summary.

    The summary also lists dependency jobs, so only a line naming the
    target counts; anything else is "unknown".
    """
    found = "unknown"
    for m in SUMMARY_RE.finditer(output):
        if m.group(2) == name:
            found = OUTCOME_MAP.get(m.group(1), "unknown")
    for m in CANNOT_RE.finditer(output):
        if m.group(1) == name:
            found = "skip"
    return found


def apply_pass(result, output, host, now):
    out = copy.deepcopy(result)
    orig = result.get("status")
    note = f"Re-run on {host} at {now}: pass (original: {orig})"
    out["rerun"] = {
        "original_status": orig,
        "original_outcome": result.get("outcome"),
        "original_comments": result.get("comments"),
        "host": host,
        "time": now,
    }
    out["outcome"] = out["status"] = "pass"
    out["io_log"] = output
    out["comments"] = note
    out["skip_reason"] = None
    return out


CID_RE = re.compile(r"\d{6}-\d{5}")
IFACE_RE = re.compile(r"^(wl|en|eth)")
SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=8"]


def cid_from_submission(tar_name, launcher):
    m = CID_RE.search(tar_name)
    if m:
        return m.group(0)
    path = launcher.get("transport:local_file", {}).get("path", "")
    m = CID_RE.search(path)
    return m.group(0) if m else None


def expected_identity(raw_dmi):
    for d in raw_dmi:
        if d.get("category") == "SYSTEM":
            return {"serial": d.get("system_serial"), "uuid": d.get("system_uuid")}
    return {"serial": None, "uuid": None}


def resolve_host(cid, c3_python, c3_script, c3_secrets):
    p = subprocess.run(
        [c3_python, c3_script, "--secrets", c3_secrets, "get-ip", cid],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if p.returncode != 0 or not p.stdout.strip():
        raise RuntimeError(f"C3 lookup failed for {cid}")
    return p.stdout.strip()


def comparable(value):
    return bool(value) and value.strip().lower() not in PLACEHOLDERS


def verify_dut(runner, expected):
    rc, out = runner(
        "sudo cat /sys/class/dmi/id/product_serial /sys/class/dmi/id/product_uuid"
    )
    if rc != 0:
        raise RuntimeError(f"DUT unreachable or DMI read failed (rc={rc}): {out.strip()[:200]}")
    lines = [x.strip() for x in out.strip().splitlines() if x.strip()][-2:]
    actual = {
        "serial": lines[0] if len(lines) > 0 else "",
        "uuid": lines[1] if len(lines) > 1 else "",
    }
    checked = []
    for key in ("serial", "uuid"):
        want = expected.get(key)
        if not comparable(want):
            continue
        if want.lower() != actual[key].lower():
            raise RuntimeError(
                f"DUT {key} mismatch: expected {want}, got {actual[key]}"
            )
        checked.append(key)
    if not checked:
        raise RuntimeError(
            "cannot verify DUT identity: the submission has no usable serial/uuid (use --force-host to skip)"
        )
    return checked


def ssh_run(host, user, password, cmd, timeout):
    argv = ["sshpass", "-e", "ssh", *SSH_OPTS, f"{user}@{host}", cmd]
    try:
        p = subprocess.run(
            argv,
            env={**os.environ, "SSHPASS": password},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise TimeoutError(str(e))
    return p.returncode, p.stdout


def dut_ifaces(runner):
    rc, out = runner("ip -br link")
    if rc != 0:
        return None
    return {line.split()[0] for line in out.splitlines() if line.strip()}


def rerun_jobs(jobs, env, runner, ifaces, rows=None):
    rows = [] if rows is None else rows
    for job in jobs:
        jid = job["id"]
        suffix = jid.rsplit("_", 1)[-1]
        if ifaces is not None and IFACE_RE.match(suffix) and re.search(rf"\bon {re.escape(suffix)}\b", job.get("name", "")) and suffix not in ifaces:
            rows.append({"id": jid, "verdict": "skipped: interface missing", "output": ""})
            continue
        try:
            rc, output = runner(build_command(jid, env))
        except (TimeoutError, OSError):
            rows.append({"id": jid, "verdict": "error", "output": ""})
            continue
        verdict = "error" if rc == 255 else parse_outcome(output, job.get("name", ""))
        rows.append({"id": jid, "verdict": verdict, "output": output})
    return rows


FAILURE_RE = re.compile(r"<failure\b.*?</failure>\s*", re.S)
CASE_RE = re.compile(r"<testcase\b[^>]*?(?:/>|>.*?</testcase>)", re.S)


def patch_junit(text, passed_full_ids):
    removed = 0

    def fix(m):
        nonlocal removed
        case = m.group(0)
        head = re.match(r'<testcase classname="([^"]+)"', case)
        if head and unescape(head.group(1)) in passed_full_ids and "<failure" in case:
            removed += 1
            return FAILURE_RE.sub("", case)
        return case

    text = CASE_RE.sub(fix, text)
    return re.sub(
        r'failures="(\d+)"',
        lambda m: f'failures="{max(int(m.group(1)) - removed, 0)}"',
        text,
        count=1,
    )


def extract(tar_path, dest):
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(tar_path) as t:
        for m in t.getmembers():
            name = m.name.lstrip("/")
            if not name or ".." in Path(name).parts:
                if name:
                    print(f"warning: dropped unsafe member {m.name}", file=sys.stderr)
                continue
            target = dest / name
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(t.extractfile(m).read())
                os.chmod(target, m.mode)
                os.utime(target, (m.mtime, m.mtime))
            else:
                print(f"warning: dropped non-regular member {m.name}", file=sys.stderr)
    return dest


def pack(src, out):
    src = Path(src)
    with tarfile.open(out, "w:xz") as t:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                t.add(f, arcname=str(f.relative_to(src)), recursive=False)


def applied_label(row, applied):
    if row['id'] in applied:
        return 'yes'
    if row['verdict'] == 'pass':
        return 'no (already pass)'
    return 'no'


def write_report(rows, applied, host, cid, now, path):
    lines = [
        "# Re-run report",
        "",
        f"- DUT: {host} (CID {cid})",
        f"- Time: {now}",
        "- `submission.html` still reflects the original run.",
        "",
        "| job | original | re-run | applied |",
        "|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['original']} | {r['verdict']} | {applied_label(r, applied)} |"
        )
    Path(path).write_text("\n".join(lines) + "\n")


def out_name(full_id):
    return full_id.replace("::", "__").replace("/", "_")


def env_arg(text):
    key, sep, value = text.partition("=")
    if not sep or not ENV_RE.match(key):
        raise argparse.ArgumentTypeError(
            f"{text!r}: expected K=V with K like WPA_BE_SSID / OPEN_N_SSID / WPA3_AX_PSK"
        )
    return key, value


def mask(env):
    return {k: ("REDACTED" if k.endswith("_PSK") else v) for k, v in env.items()}


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("submission")
    ap.add_argument("--host")
    ap.add_argument("--cid")
    ap.add_argument("--force-host", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only", help="only jobs whose id contains this text")
    ap.add_argument("--env", action="append", default=[], metavar="K=V", type=env_arg)
    ap.add_argument("--user", default="ubuntu")
    ap.add_argument("--password", default="insecure")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--output")
    ap.add_argument("--force", action="store_true", help="overwrite an existing output file")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workdir", default="/tmp/opencode/checkbox-rerun")
    ap.add_argument("--c3-python", default=sys.executable)
    ap.add_argument(
        "--c3-script",
        default=str(Path(__file__).resolve().parents[2] / "c3/scripts/c3_api.py"),
    )
    ap.add_argument("--c3-secrets", default=str(Path("~/.config/c3/secrets.ini").expanduser()))
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    src = Path(a.submission)
    stem = src.name.removesuffix(".tar.xz")
    work = extract(src, Path(a.workdir) / stem)
    sub_path = work / "submission.json"
    sub = json.loads(sub_path.read_text())
    jobs = select_jobs(sub["results"], a.all)
    if a.only:
        jobs = [j for j in jobs if a.only in j["id"]]
    if not jobs:
        print("nothing to re-run")
        return 0

    overrides = dict(a.env)
    env = wifi_env(sub.get("launcher", {}), overrides)
    out = Path(a.output) if a.output else src.with_name(stem + "_rerun.tar.xz")
    if out.resolve() == src.resolve():
        print(f"refusing to overwrite the original submission: {out}", file=sys.stderr)
        return 4
    if out.exists() and not a.force and not a.dry_run:
        print(f"{out} already exists (use --force to overwrite)", file=sys.stderr)
        return 4
    cid = a.cid or cid_from_submission(src.name, sub.get("launcher", {}))
    host = a.host
    if not host:
        if not cid:
            print("cannot determine DUT: pass --host or --cid", file=sys.stderr)
            return 2
        try:
            host = resolve_host(cid, a.c3_python, a.c3_script, a.c3_secrets)
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 2
    print(f"CID: {cid}  host: {host}")

    if a.dry_run:
        for j in jobs:
            print(f"{j['id']} [{j.get('status')}]\n  {build_command(j['id'], mask(env))}")
        return 0

    def runner(cmd):
        return ssh_run(host, a.user, a.password, cmd, a.timeout)

    if not a.force_host:
        try:
            checked = verify_dut(runner, expected_identity(sub.get("raw-devices-dmi", [])))
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 3
        print(f"DUT identity verified ({', '.join(checked)})")

    rows = []
    code = 0
    try:
        rerun_jobs(jobs, env, runner, dut_ifaces(runner), rows)
    except KeyboardInterrupt:
        print("interrupted: saving jobs completed so far", file=sys.stderr)
        code = 130
    by_id = {j["id"]: j for j in jobs}
    now = datetime.datetime.now().isoformat(timespec="seconds")
    applied = set()
    passed_full = set()
    for r in rows:
        r["original"] = by_id[r["id"]].get("status")
        print(f"{r['id']}: {r['verdict']}")
        if r["verdict"] != "pass":
            continue
        for i, res in enumerate(sub["results"]):
            if res["id"] == r["id"]:
                if res.get("status") != "pass":
                    sub["results"][i] = apply_pass(res, r["output"], host, now)
                    name = out_name(res["full_id"])
                    (work / "test_output").mkdir(exist_ok=True)
                    (work / "test_output" / name).write_text(r["output"])
                    (work / "test_output" / (name + ".stderr")).write_text("")
                    passed_full.add(res["full_id"])
                    applied.add(r["id"])
    sub_path.write_text(json.dumps(sub, ensure_ascii=False))
    junit = work / "submission.junit"
    if junit.exists():
        junit.write_text(patch_junit(junit.read_text(), passed_full))
    write_report(rows, applied, host, cid, now, work / "RERUN.md")
    pack(work, out)
    print(f"wrote {out}")
    return code


if __name__ == "__main__":
    sys.exit(main())
