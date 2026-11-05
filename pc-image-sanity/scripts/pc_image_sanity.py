#!/usr/bin/env python3
"""Batch-test an OEM PC ISO on DUTs via Testflinger, rerun wireless, file Jira bugs."""
import argparse
import importlib.util
import json
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait as _futures_wait
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILLS = SKILL_DIR.parent
RENDER = SKILLS / "render_testflinger" / "scripts" / "render_testflinger.py"
RERUN = SKILLS / "checkbox-rerun" / "scripts" / "rerun.py"
FILE_BUGS = SKILLS / "checkbox-jira-bugger" / "scripts" / "file_bugs.py"
JIRA_LOCK = threading.Lock()
JIRA_PAUSE = 5
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


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cid", action="append", default=[])
    p.add_argument("--cids-file")
    p.add_argument("--iso", required=True)
    p.add_argument("--form-factor", required=True, choices=["laptop", "desktop"])
    p.add_argument("--lp-user", required=True)
    for flag in ("-p", "-m", "-a", "-l"):
        p.add_argument(flag, required=True)
    p.add_argument("--manifest")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--no-fetch", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--resume", metavar="RESULTS_JSON")
    p.add_argument("--workdir")
    p.add_argument("--poll-interval", type=int, default=60)
    p.add_argument("--max-wait", type=int, default=43200)
    return p.parse_args(argv)


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


def check_jira_args(args, cids):
    fb = _load_file_bugs()
    with tempfile.TemporaryDirectory(prefix="pcis-jira-") as d:
        tarball = Path(d) / f"submission_{cids[0]}_x_rerun.tar.xz"
        tarball.touch()
        try:
            fb.preflight(str(tarball), args.p, args.l, args.m,
                         os.path.expanduser(fb.DEFAULT_SECRETS),
                         os.path.expanduser(fb.DEFAULT_C3),
                         os.path.expanduser(fb.DEFAULT_JIRA_CONFIG), False)
        except fb.PreflightError as e:
            raise PreflightError(str(e)) from e


def preflight(args):
    for path in (RENDER, RERUN, FILE_BUGS):
        if not Path(path).is_file():
            raise PreflightError(f"missing sibling script: {path}")
    repo = Path(args.repo).expanduser()
    if not (repo / ".git").exists():
        raise PreflightError(f"--repo is not a git directory: {repo}")
    bundled = SKILL_DIR / "manifests" / f"manifest_{args.form_factor}.json"
    if not bundled.is_file():
        raise PreflightError(f"missing bundled manifest: {bundled}")
    if args.manifest:
        try:
            data = json.loads(Path(args.manifest).read_text())
        except (OSError, ValueError) as e:
            raise PreflightError(f"cannot read --manifest {args.manifest}: {e}")
        if not isinstance(data, dict):
            raise PreflightError(f"--manifest is not a JSON object: {args.manifest}")
    try:
        req = urllib.request.Request(args.iso, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        raise PreflightError(f"ISO URL {args.iso} returned HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise PreflightError(f"ISO URL {args.iso} unreachable: {e}")
    if status != 200:
        raise PreflightError(f"ISO URL {args.iso} returned HTTP {status}")


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


def render_job(cid, iso, manifest, lp_user, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mf = out_dir / "manifest.json"
    mf.write_text(json.dumps(manifest))
    job = out_dir / "job.yaml"
    r = subprocess.run(
        [sys.executable, str(RENDER), "provision-and-test", "-q", cid,
         "--url", iso, "-u", "pc-sanity-smoke-test-26-04",
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


def wait_for_reserve(job_id, poll_interval, max_wait, sleep=time.sleep, now=time.monotonic):
    start = now()
    while True:
        status = tf_status(job_id)
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


def tf_cancel(job_id: str) -> None:
    try:
        r = _tf(["cancel", job_id])
        if r.returncode != 0:
            print(f"warning: cancel {job_id} failed: {r.stderr}", file=sys.stderr)
    except Exception as e:  # never raise
        print(f"warning: cancel {job_id} failed: {e}", file=sys.stderr)


@dataclass
class CidResult:
    cid: str
    status: str = "pending"
    stage: str = ""
    job_id: str = ""
    manifest_source: str = ""
    submission: str = ""
    rerun_tarball: str = ""
    dut_ip: str = ""
    image_ok: "bool | None" = None
    jira_exit: "int | None" = None
    released: bool = False
    error: str = ""


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


def _file_bugs(res, args, tarball, workdir, stage, sleep):
    with JIRA_LOCK:
        stage("jira")
        try:
            return _run_logged([sys.executable, str(FILE_BUGS), str(tarball), "-p", args.p,
                                "-m", args.m, "-a", args.a, "-l", args.l],
                               workdir / "jira.log", 1800)
        finally:
            if JIRA_PAUSE:
                sleep(JIRA_PAUSE)


def run_cid(cid, args, manifest, source, workdir, on_update, job_id=None, sleep=time.sleep,
            rerun_tarball=None):
    workdir = Path(workdir)
    res = CidResult(cid=cid, manifest_source=source, job_id=job_id or "")
    last_status = None

    def stage(name):
        res.stage = name
        log(cid, f"stage: {name}")
        on_update(res)

    def fail(msg):
        res.status = "failed"
        res.error = msg
        log(cid, f"failed: {msg}")

    if args.dry_run:
        try:
            stage("render")
            render_job(cid, args.iso, manifest, args.lp_user, workdir)
            res.status, res.stage = "done", "dry-run"
            log(cid, "done (dry-run)")
        except Exception as e:
            fail(str(e))
        on_update(res)
        return res
    try:
        if rerun_tarball:
            log(cid, f"resuming at Jira with {rerun_tarball}")
            res.rerun_tarball = str(rerun_tarball)
            res.released = True
            last_status = "cancelled"
            stage("jira_wait")
            rc = _file_bugs(res, args, rerun_tarball, workdir, stage, sleep)
            res.jira_exit = rc
            log(cid, f"Jira exit {rc}")
            if rc != 0:
                fail(f"file_bugs exited {rc}")
                return res
            res.status = res.stage = "done"
            log(cid, "done")
            return res
        if not job_id:
            stage("render")
            job_yaml = render_job(cid, args.iso, manifest, args.lp_user, workdir)
            stage("submit")
            res.job_id = job_id = tf_submit(job_yaml)
            log(cid, f"submitted job {job_id}")
            on_update(res)
            last_status = "submitted"
        else:
            log(cid, f"resumed job {job_id}")
        stage("wait")
        last_status = wait_for_reserve(job_id, args.poll_interval, args.max_wait)
        if last_status != "reserve":
            fail(f"job ended {last_status} before reserve")
            try:
                tf_artifacts(job_id, workdir / "artifacts")
            except Exception:
                pass
            return res
        stage("dut_ip")
        ip = None
        for attempt in range(10):
            ip = tf_dut_ip(job_id)
            if ip:
                break
            if attempt < 9:
                sleep(30)
        if not ip:
            fail("no DUT IP in reserve output")
            return res
        res.dut_ip = ip
        log(cid, f"DUT {ip}")
        stage("image")
        ok, dcd = verify_image(ip, args.iso)
        res.image_ok = ok
        if ok:
            log(cid, f"image OK ({dcd})")
        if not ok:
            fail(f"image mismatch: DUT DCD {dcd}")
            return res
        stage("submission")
        sub = fetch_submission(ip, cid, workdir / "artifacts")
        res.submission = str(sub)
        stage("rerun")
        name = Path(sub).name
        if name.endswith(".tar.xz"):
            name = name[: -len(".tar.xz")]
        out = workdir / f"{name}_rerun.tar.xz"
        rc = _run_logged([sys.executable, str(RERUN), str(sub), "--host", ip,
                          "--output", str(out), "--force"],
                         workdir / "rerun.log", 6000)
        if rc != 0:
            fail(f"rerun exited {rc}")
            return res
        res.rerun_tarball = str(out)
        if last_status not in ("complete", "cancelled"):
            last_status = "cancelled"
            try:
                tf_cancel(job_id)
            except Exception:
                pass
        res.released = True
        on_update(res)
        log(cid, "DUT released")
        log(cid, "waiting for Jira lock")
        stage("jira_wait")
        rc = _file_bugs(res, args, out, workdir, stage, sleep)
        res.jira_exit = rc
        log(cid, f"Jira exit {rc}")
        if rc != 0:
            fail(f"file_bugs exited {rc}")
            return res
        res.status = "done"
        res.stage = "done"
        log(cid, "done")
        return res
    except Exception as e:
        fail(str(e))
        return res
    finally:
        if job_id:
            if last_status not in ("complete", "cancelled"):
                tf_cancel(job_id)
            res.released = True
        on_update(res)


RESUMABLE_STAGES = ("wait", "dut_ip", "image", "submission", "rerun", "jira")


def wait_futures(futs, timeout=None):
    _futures_wait(futs, timeout=timeout)


def print_summary(results):
    cols = ["CID", "job", "manifest", "stage", "status", "jira", "released", "error"]
    rows = [cols]
    for r in results:
        rows.append([r.cid, r.job_id or "-", r.manifest_source or "-", r.stage or "-",
                     r.status, "-" if r.jira_exit is None else str(r.jira_exit),
                     "yes" if r.released else "no", r.error.replace("\n", " ")[:80]])
    widths = [max(len(row[i]) for row in rows) for i in range(len(cols))]
    for row in rows:
        print(" | ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())


def _write_results(path, iso, form_factor, results, lock):
    with lock:
        data = {"iso": iso, "form_factor": form_factor,
                "cids": {c: asdict(r) for c, r in results.items()}}
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)


def main(argv=None):
    if threading.current_thread() is not threading.main_thread():
        return _main(argv)

    def _interrupt(signum, frame):
        raise KeyboardInterrupt

    old = {s: signal.signal(s, _interrupt) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        return _main(argv)
    finally:
        for s, h in old.items():
            signal.signal(s, h)


def _main(argv=None):
    args = parse_args(argv)
    try:
        check_testflinger_login()
    except PreflightError as e:
        print(f"preflight failed: {e}", file=sys.stderr)
        return 3

    results = {}
    resume_jobs = {}
    jira_resume = {}
    iso, form_factor = args.iso, args.form_factor
    if args.resume:
        try:
            saved = json.loads(Path(args.resume).read_text())
            known = {f.name for f in fields(CidResult)}
            for c, d in saved["cids"].items():
                results[c] = CidResult(**{k: v for k, v in d.items() if k in known})
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            print(f"cannot read --resume {args.resume}: {e}", file=sys.stderr)
            return 2
        args.workdir = str(Path(args.resume).resolve().parent)
    try:
        cids = load_cids(args.cid, args.cids_file)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not cids and args.resume:
        cids = list(results)
    if not cids:
        print("error: no CIDs given", file=sys.stderr)
        return 2

    try:
        check_jira_args(args, cids)
        preflight(args)
        if not args.no_fetch:
            git_fetch(str(Path(args.repo).expanduser()))
    except PreflightError as e:
        print(f"preflight failed: {e}", file=sys.stderr)
        return 3

    to_run = [c for c in cids if not (c in results and results[c].status == "done")]
    manifests = {}
    bad = []
    for c in to_run:
        try:
            manifests[c] = resolve_manifest(c, str(Path(args.repo).expanduser()),
                                            args.form_factor, args.manifest, not args.no_fetch)
        except (PreflightError, OSError, ValueError) as e:
            bad.append(f"{c}: {e}")
    if bad:
        print("manifest resolution failed:\n  " + "\n  ".join(bad), file=sys.stderr)
        return 3
    for c in to_run:
        print(f"{c}  {manifests[c][1]}")

    batch = Path(args.workdir) if args.workdir else Path(
        "/tmp/opencode/pc-image-sanity") / datetime.now().strftime("%Y%m%d-%H%M%S")
    batch.mkdir(parents=True, exist_ok=True)
    rpath = batch / "results.json"
    lock = threading.Lock()
    for c in cids:
        results.setdefault(c, CidResult(cid=c))
    for c in to_run:
        old = results[c]
        if (old.stage in ("jira_wait", "jira") and old.released and old.rerun_tarball
                and Path(old.rerun_tarball).is_file()):
            jira_resume[c] = old.rerun_tarball
        elif old.job_id and not old.released and old.stage in RESUMABLE_STAGES:
            resume_jobs[c] = old.job_id

    def on_update(res):
        snap = CidResult(**asdict(res))
        with lock:
            results[res.cid] = snap
        _write_results(rpath, iso, form_factor, results, lock)

    _write_results(rpath, iso, form_factor, results, lock)
    executor = ThreadPoolExecutor(max_workers=max(1, len(to_run)))
    futs = []
    try:
        for c in to_run:
            manifest, source = manifests[c]
            extra = {"rerun_tarball": jira_resume[c]} if c in jira_resume else {}
            futs.append(executor.submit(run_cid, c, args, manifest, source, batch / c,
                                        on_update, resume_jobs.get(c), **extra))
        wait_futures(futs)
    except KeyboardInterrupt:
        try:
            wait_futures(futs, timeout=30)
        except KeyboardInterrupt:
            pass
        with lock:
            pending = [r.job_id for r in results.values() if r.job_id and not r.released]
        for jid in pending:
            tf_cancel(jid)
        with lock:
            for r in results.values():
                if r.job_id in pending:
                    r.released = True
        _write_results(rpath, iso, form_factor, results, lock)
        print_summary([results[c] for c in cids])
        executor.shutdown(wait=False)
        return 130
    executor.shutdown(wait=True)
    for c, f in zip(to_run, futs):
        try:
            results[c] = f.result()
        except Exception as e:
            results[c] = CidResult(cid=c, status="failed", error=str(e))
    _write_results(rpath, iso, form_factor, results, lock)
    ordered = [results[c] for c in cids]
    print_summary(ordered)
    print(f"results: {rpath}")
    return 0 if all(r.status == "done" for r in ordered) else 1


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush()
    os._exit(rc) if rc == 130 else sys.exit(rc)
