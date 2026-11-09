#!/usr/bin/env python3
"""Step-wise Testflinger/Checkbox/Jira image sanity helpers and per-CID state."""
import argparse
import fcntl
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILLS = SKILL_DIR.parent
RENDER = SKILLS / "render_testflinger" / "scripts" / "render_testflinger.py"
RERUN = SKILLS / "checkbox-rerun" / "scripts" / "rerun.py"
FILE_BUGS = SKILLS / "checkbox-jira-bugger" / "scripts" / "file_bugs.py"
SERIES = {"noble": "24.04", "resolute": "26.04"}
_LOG_LOCK = threading.Lock()

def log(cid, msg):
    line = f"[{time.strftime('%H:%M:%S')}] {cid}  {msg}\n"
    try:
        with _LOG_LOCK:
            sys.stderr.write(line)
            sys.stderr.flush()
    except OSError:
        pass

CID_RE = re.compile(r"^\d{6}-\d{5}$")
DEFAULT_REPO = "~/source/canonical/ce-oem-dut-checkbox-configuration"
LOGIN_MSG = "Testflinger not logged in: run `testflinger login` and retry"


class PreflightError(Exception):
    pass


def load_cids(cids, cids_file):
    items = list(cids)
    if cids_file:
        items += Path(cids_file).read_text().splitlines()
    out = []
    bad = []
    for raw in items:
        c = raw.strip()
        if not c or c.startswith("#"):
            continue
        if not CID_RE.match(c):
            bad.append(c)
        elif c not in out:
            out.append(c)
    if bad:
        raise ValueError(f"invalid CID(s): {', '.join(bad)}")
    return out


def check_testflinger_login():
    try:
        r = subprocess.run(
            ["testflinger", "list-queues", "--json"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        raise PreflightError("testflinger CLI not found in PATH")
    except subprocess.TimeoutExpired:
        raise PreflightError(LOGIN_MSG)
    if r.returncode != 0 or "oauth2/device" in (r.stdout or "") + (r.stderr or ""):
        raise PreflightError(LOGIN_MSG)


def _load_file_bugs():
    spec = importlib.util.spec_from_file_location("file_bugs", FILE_BUGS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_jira_args(p, m, l, cids):
    fb = _load_file_bugs()
    with tempfile.TemporaryDirectory(prefix="pcis-jira-") as d:
        tarball = Path(d) / f"submission_{cids[0]}_x_rerun.tar.xz"
        tarball.touch()
        try:
            fb.preflight(str(tarball), p, l, m,
                         os.path.expanduser(fb.DEFAULT_SECRETS),
                         os.path.expanduser(fb.DEFAULT_C3),
                         os.path.expanduser(fb.DEFAULT_JIRA_CONFIG), False)
        except fb.PreflightError as e:
            raise PreflightError(str(e)) from e


def preflight_checks(repo, form_factor, manifest, iso):
    for path in (RENDER, RERUN, FILE_BUGS):
        if not Path(path).is_file():
            raise PreflightError(f"missing sibling script: {path}")
    repo = Path(repo).expanduser()
    if not (repo / ".git").exists():
        raise PreflightError(f"--repo is not a git directory: {repo}")
    bundled = SKILL_DIR / "manifests" / f"manifest_{form_factor}.json"
    if not bundled.is_file():
        raise PreflightError(f"missing bundled manifest: {bundled}")
    if manifest:
        try:
            data = json.loads(Path(manifest).read_text())
        except (OSError, ValueError) as e:
            raise PreflightError(f"cannot read --manifest {manifest}: {e}")
        if not isinstance(data, dict):
            raise PreflightError(f"--manifest is not a JSON object: {manifest}")
    try:
        req = urllib.request.Request(iso, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        raise PreflightError(f"ISO URL {iso} returned HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise PreflightError(f"ISO URL {iso} unreachable: {e}")
    if status != 200:
        raise PreflightError(f"ISO URL {iso} returned HTTP {status}")



def git_fetch(repo):
    r = subprocess.run(["git", "-C", repo, "fetch", "--quiet", "origin"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise PreflightError(f"git fetch failed in {repo}: {r.stderr.strip()}")


def _git_show(repo, ref_path):
    r = subprocess.run(["git", "-C", repo, "show", ref_path],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def resolve_manifest(cid, repo, form_factor, override, fetch):
    # `fetch` is accepted for interface compatibility; the batch caller runs git_fetch once.
    if override:
        return json.loads(Path(override).read_text()), f"file:{override}"
    candidates = [
        (f"origin/{cid}:pc/{cid}/manifest.json", f"branch:origin/{cid}"),
        (f"origin/main:pc/{cid}/manifest.json", f"main:pc/{cid}"),
    ]
    for ref, source in candidates:
        out = _git_show(repo, ref)
        if out is not None:
            try:
                return json.loads(out), source
            except json.JSONDecodeError as e:
                raise ValueError(f"invalid JSON in {ref}: {e}") from e
    bundled = SKILL_DIR / "manifests" / f"manifest_{form_factor}.json"
    return json.loads(bundled.read_text()), f"bundled:{form_factor}"


def render_job(cid, iso, manifest, lp_user, out_dir, test_plan):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mf = out_dir / "manifest.json"
    mf.write_text(json.dumps(manifest))
    job = out_dir / "job.yaml"
    r = subprocess.run(
        [sys.executable, str(RENDER), "provision-and-test", "-q", cid,
         "--url", iso, "-u", test_plan,
         "--manifest", str(mf), "-o", str(job)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    text = job.read_text()
    if not text.endswith("\n"):
        text += "\n"
    text += (f'    _put "$SUBMISSION_TAR_PATH" /home/ubuntu/submission_{cid}.tar.xz\n'
             '    cp "$SUBMISSION_TAR_PATH" artifacts/ || true\n')
    text += f"reserve_data:\n  ssh_keys:\n    - lp:{lp_user}\n  timeout: 7200\n"
    job.write_text(text)
    return job

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
TF_TIMEOUT = 120
TF_ARTIFACTS_TIMEOUT = 1800


def _tf(args, timeout=TF_TIMEOUT, input=None):
    # The testflinger snap cannot read or write /tmp, so files go via stdin or $HOME.
    kw = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    return subprocess.run(
        ["testflinger", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        **kw,
    )


def tf_submit(job_yaml: Path) -> str:
    r = _tf(["submit", "--quiet", "-"], input=Path(job_yaml).read_text())
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not UUID_RE.match(out):
        raise RuntimeError(f"testflinger submit failed: {out} {r.stderr or ''}".strip())
    return out


def tf_status(job_id: str) -> str:
    r = _tf(["status", job_id])
    if r.returncode != 0:
        raise RuntimeError(f"testflinger status failed: {r.stdout} {r.stderr}".strip())
    return (r.stdout or "").strip().lower()


MAX_STATUS_ERRORS = 10


def wait_for_reserve(job_id, poll_interval, max_wait, sleep=time.sleep, now=time.monotonic):
    start = now()
    errors = 0
    while True:
        try:
            status = tf_status(job_id)
            errors = 0
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            errors += 1
            if errors >= MAX_STATUS_ERRORS:
                raise RuntimeError(f"testflinger status failed {errors} times in a row: {e}") from e
            print(f"[{job_id}] transient status error ({errors}/{MAX_STATUS_ERRORS}): {e}", file=sys.stderr)
            status = None
        if status in ("reserve", "complete", "cancelled"):
            return status
        if status is None and now() - start + poll_interval > max_wait:
            raise TimeoutError(f"job {job_id} not reserved after {max_wait}s (status unavailable)")
        if status in ("reserve", "complete", "cancelled"):
            return status
        if now() - start + poll_interval > max_wait:
            raise TimeoutError(f"job {job_id} not reserved after {max_wait}s (last status: {status})")
        sleep(poll_interval)


def tf_dut_ip(job_id: str):
    r = _tf(["poll", "--oneshot", "-p", "reserve", job_id])
    m = re.search(r"ubuntu@(\d{1,3}(?:\.\d{1,3}){3})", (r.stdout or "") + (r.stderr or ""))
    return m.group(1) if m else None


def tf_artifacts(job_id: str, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    tgz = dest / "artifacts.tgz"
    with tempfile.TemporaryDirectory(dir=Path.home(), prefix="pc-image-sanity-") as snap_dir:
        snap_tgz = Path(snap_dir) / "artifacts.tgz"
        r = _tf(["artifacts", "--filename", str(snap_tgz), job_id], timeout=TF_ARTIFACTS_TIMEOUT)
        if r.returncode != 0 or not snap_tgz.exists():
            return None
        shutil.copy(snap_tgz, tgz)
    with tarfile.open(tgz) as t:
        t.extractall(dest, filter="data")
    found = sorted(dest.rglob("submission_*.tar.xz"))
    return found[0] if found else None


def fetch_submission(ip, cid, dest: Path) -> Path:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"submission_{cid}.tar.xz"
    env = dict(os.environ, SSHPASS="insecure")
    r = subprocess.run(
        ["sshpass", "-e", "scp", "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=/dev/null",
         f"ubuntu@{ip}:/home/ubuntu/submission_{cid}.tar.xz", str(out)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env, timeout=600,
    )
    if r.returncode != 0:
        raise RuntimeError(f"scp submission failed: {r.stderr}".strip())
    return out


def tf_cancel(job_id: str) -> bool:
    try:
        r = _tf(["cancel", job_id])
        if r.returncode != 0:
            print(f"warning: cancel {job_id} failed: {r.stderr}", file=sys.stderr)
            return False
        return True
    except Exception as e:  # never raise
        print(f"warning: cancel {job_id} failed: {e}", file=sys.stderr)
        return False

def build_id(text):
    m = re.findall(r"(\d{8}-\d+)", text or "")
    return m[-1] if m else None


def verify_image(ip, iso):
    env = dict(os.environ, SSHPASS="insecure")
    r = subprocess.run(
        ["sshpass", "-e", "ssh", "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=/dev/null", f"ubuntu@{ip}",
         "ubuntu-report show | grep DCD"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env, timeout=60,
    )
    m = re.search(r'"DCD":\s*"([^"]*)"', r.stdout or "")
    if not m:
        return False, ""
    dcd = m.group(1)
    want = build_id(Path(iso.split("?")[0]).name)
    return want is not None and build_id(dcd) == want, dcd


def _run_logged(argv, log, timeout):
    r = subprocess.run(argv, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=timeout)
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    Path(log).write_text((r.stdout or "") + (r.stderr or ""))
    return r.returncode


class StepError(Exception):
    pass


class UsageError(Exception):
    pass


def load_batch(batch: Path) -> dict:
    try:
        return json.loads((Path(batch) / "batch.json").read_text())
    except (OSError, ValueError) as e:
        raise UsageError(f"cannot read batch.json in {batch}: {e}") from e


def save_batch(batch: Path, data: dict) -> None:
    batch = Path(batch)
    batch.mkdir(parents=True, exist_ok=True)
    _atomic_write(batch / "batch.json", data)


def _atomic_write(path: Path, data: dict) -> None:
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def load_state(batch: Path, cid: str) -> dict:
    if cid not in load_batch(batch).get("cids", []):
        raise UsageError(f"{cid} is not in batch {batch}")
    f = Path(batch) / cid / "state.json"
    if not f.is_file():
        raise UsageError(f"no state for {cid} in {batch}")
    return json.loads(f.read_text())


def save_state(batch: Path, state: dict) -> None:
    d = Path(batch) / state["cid"]
    d.mkdir(parents=True, exist_ok=True)
    _atomic_write(d / "state.json", state)


def _begin(state: dict, stage: str) -> None:
    state.update(status="running", stage=stage)
    state.pop("error", None)


def require(state: dict, field: str, set_by: str) -> str:
    if not state.get(field):
        raise UsageError(f"{field} missing; run '{set_by}' first")
    return state[field]


def test_plan_for_iso(iso: str) -> "tuple[str, str]":
    name = Path(iso.split("?")[0]).name
    for part in name.split("-"):
        if part in SERIES:
            return f"pc-sanity-smoke-test-{SERIES[part].replace('.', '-')}", f"iso:{part}"
    raise PreflightError(f"cannot infer series from ISO {name}; known codenames: "
                         + ", ".join(SERIES))


def cmd_preflight(args) -> dict:
    check_testflinger_login()
    check_jira_args(args.p, args.m, args.l, args.cids)
    if args.test_plan:
        plan, source = args.test_plan, "flag"
    else:
        plan, source = test_plan_for_iso(args.iso)
    preflight_checks(args.repo, args.form_factor, args.manifest, args.iso)
    if not args.no_fetch:
        git_fetch(str(Path(args.repo).expanduser()))
    batch = Path(args.batch)
    save_batch(batch, {
        "iso": args.iso, "test_plan": plan, "test_plan_source": source,
        "form_factor": args.form_factor, "lp_user": args.lp_user,
        "p": args.p, "m": args.m, "a": args.a, "l": args.l,
        "repo": args.repo, "manifest": args.manifest, "cids": args.cids,
    })
    for cid in args.cids:
        if not (batch / cid / "state.json").is_file():
            save_state(batch, {"cid": cid, "status": "pending", "stage": ""})
    return {"step": "preflight", "ok": True, "batch": str(batch),
            "test_plan": plan, "cids": args.cids}


BATCH_ROOT = Path("/tmp/opencode/pc-image-sanity")
STATUS_COLS = ["cid", "status", "stage", "job_id", "dut_ip", "error"]


def cmd_status(args) -> dict:
    batch = Path(args.batch)
    states = []
    for cid in load_batch(batch).get("cids", []):
        try:
            states.append(load_state(batch, cid))
        except UsageError:
            states.append({"cid": cid, "status": "pending", "stage": ""})
    _atomic_write(batch / "results.json", states)
    rows = [[str(s.get(c) or "") for c in STATUS_COLS] for s in states]
    widths = [max(len(c), *(len(r[i]) for r in rows)) for i, c in enumerate(STATUS_COLS)]
    for row in [[c.upper() for c in STATUS_COLS], *rows]:
        print("  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip(), file=sys.stderr)
    return {"step": "status", "ok": all(s.get("status") == "done" for s in states),
            "batch": str(batch), "results": str(batch / "results.json")}


def cmd_submit(args) -> dict:
    batch = Path(args.batch)
    state = load_state(batch, args.cid)
    if state.get("job_held") and state.get("job_id") and not args.force and not args.dry_run:
        raise UsageError(f"job {state['job_id']} still held; run 'release' or pass --force")
    b = load_batch(batch)
    if state.get("job_held") and state.get("job_id") and not args.dry_run:
        if not tf_cancel(state["job_id"]):
            print(f"warning: cancel of job {state['job_id']} failed; continuing (--force)",
                  file=sys.stderr)
        state["job_held"] = False
        save_state(batch, state)
    manifest, source = resolve_manifest(args.cid, str(Path(b["repo"]).expanduser()),
                                        b["form_factor"], b.get("manifest"), False)
    job = render_job(args.cid, b["iso"], manifest, b["lp_user"], batch / args.cid, b["test_plan"])
    if args.dry_run:
        return {"step": "submit", "ok": True, "cid": args.cid, "dry_run": True,
                "job_yaml": str(job), "manifest_source": source}
    state["manifest_source"] = source
    _begin(state, "submit")
    save_state(batch, state)
    job_id = tf_submit(job)
    for k in ("dut_ip", "image_ok", "submission", "rerun_tarball", "jira_exit", "error"):
        state.pop(k, None)
    state.update(job_id=job_id, job_held=True, stage="submitted", status="running")
    save_state(batch, state)
    return {"step": "submit", "ok": True, "cid": args.cid, "job_id": job_id,
            "manifest_source": source}


def cmd_release(args) -> dict:
    batch = Path(args.batch)
    state = load_state(batch, args.cid)
    if state.get("job_id") and state.get("job_held"):
        if not tf_cancel(state["job_id"]):
            try:
                released = tf_status(state["job_id"]) in ("complete", "cancelled")
            except Exception:
                released = False
            if not released:
                raise StepError(f"cancel failed for job {state['job_id']}; DUT may still be reserved")
        state["job_held"] = False
        save_state(batch, state)
    return {"step": "release", "ok": True, "cid": args.cid, "job_id": state.get("job_id")}


def _sleep(seconds):
    time.sleep(seconds)


def cmd_wait(args) -> dict:
    batch = Path(args.batch)
    state = load_state(batch, args.cid)
    job_id = require(state, "job_id", "submit")
    for k in ("dut_ip", "image_ok", "dcd", "submission"):
        state.pop(k, None)
    _begin(state, "wait")
    save_state(batch, state)
    try:
        end = wait_for_reserve(job_id, args.poll_interval, args.max_wait, sleep=_sleep)
    except TimeoutError as e:
        raise StepError(str(e)) from e
    if end != "reserve":
        state["job_held"] = False
        save_state(batch, state)
        try:
            tf_artifacts(job_id, batch / args.cid / "artifacts")
        except Exception:
            pass
        raise StepError(f"job ended {end} before reserve")
    ip = None
    for i in range(10):
        ip = tf_dut_ip(job_id)
        if ip:
            break
        if i < 9:
            _sleep(30)
    if not ip:
        raise StepError("no DUT IP in reserve output")
    state.update(dut_ip=ip, stage="reserved")
    save_state(batch, state)
    return {"step": "wait", "ok": True, "cid": args.cid, "dut_ip": ip}


def cmd_verify_image(args) -> dict:
    batch = Path(args.batch)
    state = load_state(batch, args.cid)
    ip = require(state, "dut_ip", "wait")
    iso = load_batch(batch)["iso"]
    state.pop("image_ok", None)
    state.pop("dcd", None)
    _begin(state, "verify-image")
    save_state(batch, state)
    ok, dcd = verify_image(ip, iso)
    state.update(image_ok=ok, dcd=dcd)
    save_state(batch, state)
    if not ok:
        want = build_id(Path(iso.split("?")[0]).name)
        if not dcd:
            raise StepError(f"could not read DCD over SSH from {ip}")
        if want is None:
            raise StepError("ISO name has no build id")
        raise StepError(f"image mismatch: DUT DCD {dcd}, expected {want}")
    return {"step": "verify-image", "ok": True, "cid": args.cid, "dcd": dcd}


def cmd_fetch(args) -> dict:
    batch = Path(args.batch)
    state = load_state(batch, args.cid)
    ip = require(state, "dut_ip", "wait")
    state.pop("submission", None)
    _begin(state, "fetch")
    save_state(batch, state)
    path = fetch_submission(ip, args.cid, batch / args.cid / "artifacts")
    state["submission"] = str(path)
    save_state(batch, state)
    return {"step": "fetch", "ok": True, "cid": args.cid, "submission": str(path)}


def cmd_rerun(args) -> dict:
    batch = Path(args.batch)
    state = load_state(batch, args.cid)
    submission = require(state, "submission", "fetch")
    ip = require(state, "dut_ip", "wait")
    name = Path(submission).name
    if name.endswith(".tar.xz"):
        name = name[:-len(".tar.xz")]
    out = batch / args.cid / f"{name}_rerun.tar.xz"
    log_path = batch / args.cid / "rerun.log"
    for k in ("rerun_tarball", "jira_exit"):
        state.pop(k, None)
    _begin(state, "rerun")
    save_state(batch, state)
    rc = _run_logged([sys.executable, str(RERUN), submission, "--host", ip,
                      "--output", str(out), "--force"], log_path, 6000)
    if rc != 0:
        raise StepError(f"rerun exited {rc}; see {log_path}")
    state["rerun_tarball"] = str(out)
    save_state(batch, state)
    return {"step": "rerun", "ok": True, "cid": args.cid, "rerun_tarball": str(out)}


def _acquire_jira_lock(path, timeout):
    f = open(path, "w")
    start = time.monotonic()
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            if time.monotonic() - start >= timeout:
                f.close()
                raise StepError(f"Jira lock not acquired after {timeout}s")
            _sleep(5)


def cmd_jira(args) -> dict:
    batch = Path(args.batch)
    state = load_state(batch, args.cid)
    tarball = require(state, "rerun_tarball", "rerun")
    if not Path(tarball).is_file():
        raise UsageError(f"rerun_tarball {tarball} does not exist; run 'rerun' first")
    b = load_batch(batch)
    log_path = batch / args.cid / "jira.log"
    state.pop("jira_exit", None)
    _begin(state, "jira")
    save_state(batch, state)
    lock = _acquire_jira_lock(batch / "jira.lock", args.lock_timeout)
    try:
        rc = _run_logged([sys.executable, str(FILE_BUGS), tarball, "-p", b["p"],
                          "-m", b["m"], "-a", b["a"], "-l", b["l"]], log_path, 1800)
        _sleep(5)
    finally:
        lock.close()
    state["jira_exit"] = rc
    save_state(batch, state)
    if rc != 0:
        raise StepError(f"file_bugs exited {rc}; see {log_path}")
    state.update(status="done", stage="done")
    state.pop("error", None)
    save_state(batch, state)
    return {"step": "jira", "ok": True, "cid": args.cid}


HANDLERS = {"preflight": cmd_preflight, "status": cmd_status,
            "submit": cmd_submit, "release": cmd_release,
            "wait": cmd_wait, "verify-image": cmd_verify_image, "fetch": cmd_fetch,
            "rerun": cmd_rerun, "jira": cmd_jira}
PER_CID = {"preflight": False, "status": False, "submit": True, "release": True,
           "wait": True, "verify-image": True, "fetch": True,
           "rerun": True, "jira": True}


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    pf = sub.add_parser("preflight")
    pf.add_argument("--cid", action="append", default=[])
    pf.add_argument("--cids-file")
    pf.add_argument("--iso", required=True)
    pf.add_argument("--form-factor", required=True, choices=["laptop", "desktop"])
    pf.add_argument("--lp-user", required=True)
    for flag in ("-p", "-m", "-a", "-l"):
        pf.add_argument(flag, required=True)
    pf.add_argument("--manifest")
    pf.add_argument("--test-plan")
    pf.add_argument("--repo", default=DEFAULT_REPO)
    pf.add_argument("--no-fetch", action="store_true")
    pf.add_argument("--batch")
    st = sub.add_parser("status")
    st.add_argument("--batch", required=True)
    sm = sub.add_parser("submit")
    sm.add_argument("--batch", required=True)
    sm.add_argument("--cid", required=True)
    sm.add_argument("--dry-run", action="store_true")
    sm.add_argument("--force", action="store_true")
    rl = sub.add_parser("release")
    rl.add_argument("--batch", required=True)
    rl.add_argument("--cid", required=True)
    wt = sub.add_parser("wait")
    wt.add_argument("--batch", required=True)
    wt.add_argument("--cid", required=True)
    wt.add_argument("--poll-interval", type=int, default=60)
    wt.add_argument("--max-wait", type=int, default=43200)
    for name in ("verify-image", "fetch", "rerun", "jira"):
        sp = sub.add_parser(name)
        sp.add_argument("--batch", required=True)
        sp.add_argument("--cid", required=True)
        if name == "jira":
            sp.add_argument("--lock-timeout", type=int, default=3600)
    return p


def _emit(summary):
    print(json.dumps(summary))


def run_step(name, args) -> int:
    """Run a per-CID handler: load state, run, persist failure. Exit 0/1/2."""
    handler = HANDLERS[name]
    try:
        load_state(Path(args.batch), args.cid)
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        summary = handler(args)
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        msg = str(e) if isinstance(e, StepError) else f"{type(e).__name__}: {e}"
        state = load_state(Path(args.batch), args.cid)
        state.update(status="failed", error=msg)
        save_state(Path(args.batch), state)
        print(f"{name} failed: {msg}", file=sys.stderr)
        _emit({**state, "step": name, "ok": False, "error": msg})
        return 1
    _emit(summary)
    return 0 if summary.get("ok", True) else 1


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    name = args.command
    try:
        if PER_CID.get(name):
            return run_step(name, args)
        if name == "preflight":
            try:
                args.cids = load_cids(args.cid, args.cids_file)
            except (ValueError, OSError) as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            if not args.cids:
                print("error: no CIDs given", file=sys.stderr)
                return 2
            if not args.batch:
                args.batch = str(BATCH_ROOT / time.strftime("%Y%m%d-%H%M%S"))
        summary = HANDLERS[name](args)
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except PreflightError as e:
        print(f"preflight failed: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"{name} failed: {msg}", file=sys.stderr)
        _emit({"step": name, "ok": False, "error": msg})
        return 1
    _emit(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
