import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pc_image_sanity as pis  # noqa: E402

BASE = [
    "--iso", "http://x/a.iso", "--form-factor", "laptop", "--lp-user", "u",
    "-p", "P", "-m", "m", "-a", "a", "-l", "l",
]


def test_load_cids_merges_file_and_flags(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("# c\n202607-39077\n\n202607-39076\n")
    assert pis.load_cids(["202607-39076"], str(f)) == ["202607-39076", "202607-39077"]


def test_load_cids_rejects_bad():
    with pytest.raises(ValueError, match="2026-1"):
        pis.load_cids(["2026-1"], None)


def test_parse_args_requires_iso_and_form_factor():
    argv = [a for a in BASE if a not in ("--iso", "http://x/a.iso")]
    with pytest.raises(SystemExit) as e:
        pis.parse_args(argv)
    assert e.value.code == 2


def test_parse_args_defaults():
    a = pis.parse_args(BASE + ["--cid", "202607-39076"])
    assert a.poll_interval == 60 and a.max_wait == 43200 and a.cid == ["202607-39076"]


def _fake_skill(tmp_path, monkeypatch, manifest=True):
    skill = tmp_path / "pc-image-sanity"
    (skill / "manifests").mkdir(parents=True)
    if manifest:
        (skill / "manifests" / "manifest_laptop.json").write_text("{}")
    skills = tmp_path
    for p, name in ((pis, "RENDER"), (pis, "RERUN"), (pis, "FILE_BUGS")):
        f = tmp_path / name
        f.write_text("")
        monkeypatch.setattr(p, name, f)
    monkeypatch.setattr(pis, "SKILL_DIR", skill)
    monkeypatch.setattr(pis, "SKILLS", skills)
    return skill


def test_preflight_missing_bundled_manifest(tmp_path, monkeypatch):
    _fake_skill(tmp_path, monkeypatch, manifest=False)
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    args = pis.parse_args(BASE + ["--cid", "202607-39076", "--repo", str(tmp_path / "repo")])
    with pytest.raises(pis.PreflightError, match="manifest"):
        pis.preflight(args)


def test_preflight_iso_404(tmp_path, monkeypatch):
    _fake_skill(tmp_path, monkeypatch)
    (tmp_path / "repo" / ".git").mkdir(parents=True)

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

    monkeypatch.setattr(pis.urllib.request, "urlopen", boom)
    args = pis.parse_args(BASE + ["--cid", "202607-39076", "--repo", str(tmp_path / "repo")])
    with pytest.raises(pis.PreflightError, match="http://x/a.iso"):
        pis.preflight(args)


def _run(monkeypatch, out="", rc=0, exc=None):
    def fake(cmd, **kw):
        assert cmd == ["testflinger", "list-queues", "--json"]
        assert kw["stdin"] == subprocess.DEVNULL and kw["timeout"] == 60
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, rc, out, "")

    monkeypatch.setattr(pis.subprocess, "run", fake)


def test_login_check_device_flow_raises(monkeypatch):
    _run(monkeypatch, "Please visit https://login.canonical.com/oauth2/device/verify")
    with pytest.raises(pis.PreflightError, match="testflinger login"):
        pis.check_testflinger_login()


def test_login_check_timeout_raises(monkeypatch):
    _run(monkeypatch, exc=subprocess.TimeoutExpired("testflinger", 60))
    with pytest.raises(pis.PreflightError, match="not logged in"):
        pis.check_testflinger_login()


def test_login_check_ok(monkeypatch):
    _run(monkeypatch, "{}")
    pis.check_testflinger_login()


def test_login_check_missing_binary(monkeypatch):
    _run(monkeypatch, exc=FileNotFoundError("testflinger"))
    with pytest.raises(pis.PreflightError, match="not found in PATH"):
        pis.check_testflinger_login()


def test_login_check_nonzero_exit(monkeypatch):
    _run(monkeypatch, "boom", rc=1)
    with pytest.raises(pis.PreflightError, match="not logged in"):
        pis.check_testflinger_login()


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


@pytest.fixture
def manifest_repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", str(seed)], check=True, capture_output=True)
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@x")
    _git(seed, "config", "commit.gpgsign", "false")
    d = seed / "pc/202607-39077"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text('{"from": "main"}')
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "m")
    _git(seed, "branch", "-M", "main")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "origin", "main")
    _git(seed, "checkout", "-b", "202607-39076")
    d = seed / "pc/202607-39076"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text('{"from": "branch"}')
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "b")
    _git(seed, "push", "origin", "202607-39076")
    _git(seed, "checkout", "main")
    _git(seed, "checkout", "-b", "202607-39078")
    _git(seed, "commit", "--allow-empty", "-m", "e")
    _git(seed, "push", "origin", "202607-39078")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    return str(clone)


def test_manifest_from_branch(manifest_repo):
    m, s = pis.resolve_manifest("202607-39076", manifest_repo, "laptop", None, False)
    assert s == "branch:origin/202607-39076" and m == {"from": "branch"}


def test_manifest_from_main(manifest_repo):
    m, s = pis.resolve_manifest("202607-39077", manifest_repo, "laptop", None, False)
    assert s == "main:pc/202607-39077" and m == {"from": "main"}


def test_manifest_branch_without_file_falls_to_main_or_bundled(manifest_repo):
    m, s = pis.resolve_manifest("202607-39078", manifest_repo, "laptop", None, False)
    assert s == "bundled:laptop"
    bundled = pis.SKILL_DIR / "manifests" / "manifest_laptop.json"
    assert m == json.loads(bundled.read_text())


def test_manifest_override(manifest_repo, tmp_path):
    f = tmp_path / "o.json"
    f.write_text('{"o": 1}')
    m, s = pis.resolve_manifest("202607-39076", manifest_repo, "laptop", str(f), False)
    assert s == f"file:{f}" and m == {"o": 1}


def test_manifest_invalid_json_raises(manifest_repo, tmp_path):
    seed = tmp_path / "bad"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "origin.git"), str(seed)], check=True, capture_output=True)
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@x")
    _git(seed, "config", "commit.gpgsign", "false")
    d = seed / "pc/202607-39099"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text("{nope")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "x")
    _git(seed, "push", "origin", "main")
    pis.git_fetch(manifest_repo)
    with pytest.raises(ValueError):
        pis.resolve_manifest("202607-39099", manifest_repo, "laptop", None, False)


def test_git_fetch_failure(tmp_path):
    with pytest.raises(pis.PreflightError):
        pis.git_fetch(str(tmp_path))


def test_render_job_appends_reserve(tmp_path):
    pytest.importorskip("jinja2")
    yaml = pytest.importorskip("yaml")
    out = tmp_path / "new" / "dir"
    p = pis.render_job("202607-39076", "http://x/a.iso", {"k": "v"}, "dirk", out)
    text = p.read_text()
    assert "job_queue: 202607-39076" in text
    assert "provision_data:\n  url: http://x/a.iso" in text
    assert "pc-sanity-smoke-test-26-04" in text
    assert "reserve_data:" in text
    assert "- lp:dirk" in text
    assert "timeout: 7200" in text
    assert yaml.safe_load(text)["reserve_data"]["timeout"] == 7200


def test_render_job_copies_submission_and_reserve_structure(tmp_path):
    pytest.importorskip("jinja2")
    yaml = pytest.importorskip("yaml")
    cid = "202607-39076"
    p = pis.render_job(cid, "http://x/a.iso", {"k": "v"}, "dirk", tmp_path)
    doc = yaml.safe_load(p.read_text())
    lines = doc["test_data"]["test_cmds"].splitlines()
    assert lines[-2:] == [
        f'_put "$SUBMISSION_TAR_PATH" /home/ubuntu/submission_{cid}.tar.xz',
        'cp "$SUBMISSION_TAR_PATH" artifacts/ || true',
    ]
    assert lines[-3].startswith("checkbox-cli submit ")
    assert doc["reserve_data"] == {"ssh_keys": ["lp:dirk"], "timeout": 7200}


# ---- Task 4: testflinger wrapper ----
import subprocess as _sp  # noqa: E402
import tarfile  # noqa: E402

UUID = "12345678-1234-1234-1234-123456789abc"


def _cp(out="", rc=0):
    return _sp.CompletedProcess([], rc, stdout=out, stderr="")


def test_submit_returns_id(monkeypatch, tmp_path):
    seen = {}

    def fake(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return _cp(UUID + "\n")

    monkeypatch.setattr(subprocess, "run", fake)
    (tmp_path / "j.yaml").write_text("job_queue: q\n")
    assert pis.tf_submit(tmp_path / "j.yaml") == UUID
    assert seen["argv"] == ["testflinger", "submit", "--quiet", "-"]
    assert seen["kw"]["input"] == "job_queue: q\n" and seen["kw"]["timeout"]


def test_submit_bad_output_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("Please login\n"))
    (tmp_path / "j.yaml").write_text("x: 1\n")
    with pytest.raises(RuntimeError, match="Please login"):
        pis.tf_submit(tmp_path / "j.yaml")


def _seq(statuses):
    it = iter(statuses)
    return lambda a, **k: _cp(next(it) + "\n")


def test_wait_for_reserve_sequence(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _seq(["setup", "provision", "test", "Reserve"]))
    sleeps = []
    assert pis.wait_for_reserve(UUID, 5, 1000, sleep=sleeps.append) == "reserve"
    assert len(sleeps) == 3


def test_wait_ends_on_complete_without_reserve(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _seq(["provision", "complete"]))
    assert pis.wait_for_reserve(UUID, 5, 1000, sleep=lambda s: None) == "complete"


def test_wait_timeout(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("test\n"))
    t = [0]

    def sleep(s):
        t[0] += s

    with pytest.raises(TimeoutError):
        pis.wait_for_reserve(UUID, 10, 25, sleep=sleep, now=lambda: t[0])


def test_status_timeout_raises(monkeypatch):
    def fake(a, **k):
        raise subprocess.TimeoutExpired(a, 1)

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(subprocess.TimeoutExpired):
        pis.tf_status(UUID)


def test_dut_ip_parsed(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("x ssh ubuntu@10.102.1.5 y"))
    assert pis.tf_dut_ip(UUID) == "10.102.1.5"
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("nothing"))
    assert pis.tf_dut_ip(UUID) is None


def test_artifacts_finds_submission(monkeypatch, tmp_path):
    seen_out = []

    def fake(argv, **kw):
        out = Path(argv[argv.index("--filename") + 1])
        seen_out.append(out)
        src = tmp_path / "src" / "artifacts"
        src.mkdir(parents=True)
        (src / "submission_202607-39076.tar.xz").write_bytes(b"x")
        with tarfile.open(out, "w:gz") as t:
            t.add(src, arcname="artifacts")
        return _cp()

    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    dest = tmp_path / "dest"
    r = pis.tf_artifacts(UUID, dest)
    assert r.name == "submission_202607-39076.tar.xz" and r.exists()
    assert seen_out[0].parent.parent == tmp_path  # written under $HOME, not /tmp
    assert not list(tmp_path.glob("pc-image-sanity-*"))


def test_artifacts_none_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("", 1))
    assert pis.tf_artifacts(UUID, tmp_path / "d") is None


def test_fetch_submission_scp(monkeypatch, tmp_path):
    seen = {}

    def fake(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return _cp()

    monkeypatch.setattr(subprocess, "run", fake)
    dest = tmp_path / "d"
    r = pis.fetch_submission("1.2.3.4", "202607-39076", dest)
    assert r == dest / "submission_202607-39076.tar.xz"
    assert seen["argv"] == [
        "sshpass", "-e", "scp", "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "ubuntu@1.2.3.4:/home/ubuntu/submission_202607-39076.tar.xz", str(r)]
    assert seen["kw"]["env"]["SSHPASS"] == "insecure"
    assert seen["kw"]["stdin"] == subprocess.DEVNULL and seen["kw"]["timeout"] == 600


def test_fetch_submission_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run",
                        lambda a, **k: _sp.CompletedProcess(a, 1, "", "no such file"))
    with pytest.raises(RuntimeError, match="no such file"):
        pis.fetch_submission("1.2.3.4", "202607-39076", tmp_path)


def test_cancel_swallow_errors(monkeypatch):
    def fake(a, **k):
        raise subprocess.TimeoutExpired(a, 1)

    monkeypatch.setattr(subprocess, "run", fake)
    pis.tf_cancel(UUID)
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("", 1))
    pis.tf_cancel(UUID)


# ---- Task 5: per-CID pipeline ----
DCD = '"DCD": "canonical-oem-stella-resolute-oem-26.04b-edge-20261020-833"\n'


def test_build_id():
    assert pis.build_id("canonical-oem-stella-resolute-oem-26.04b-edge-20261020-833") == "20261020-833"
    assert pis.build_id("http://h/stella-resolute-oem-26.04a-20261103-1091.iso") == "20261103-1091"
    assert pis.build_id("nothing") is None


def test_verify_image_parses_dcd(monkeypatch):
    seen = {}

    def fake(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return _cp(DCD)

    monkeypatch.setattr(subprocess, "run", fake)
    ok, dcd = pis.verify_image("1.2.3.4", "http://h/x-20261020-833.iso")
    assert ok and dcd.endswith("20261020-833")
    assert seen["kw"]["env"]["SSHPASS"] == "insecure" and seen["kw"]["timeout"] == 60
    assert seen["argv"][:2] == ["sshpass", "-e"]
    ok, _ = pis.verify_image("1.2.3.4", "http://h/x-20261103-1091.iso")
    assert not ok


class Env:
    def __init__(self, monkeypatch, tmp_path, mismatch=False, rerun_exc=None,
                 final="reserve", dry=False):
        self.calls = []
        c = self.calls
        monkeypatch.setattr(pis, "JIRA_PAUSE", 0, raising=False)
        self.args = pis.parse_args(BASE + (["--dry-run"] if dry else []))
        sub = tmp_path / "art" / "submission_202607-39076.tar.xz"
        sub.parent.mkdir(parents=True, exist_ok=True)
        sub.write_bytes(b"x")
        monkeypatch.setattr(pis, "render_job", lambda *a, **k: c.append("render") or tmp_path / "job.yaml")
        monkeypatch.setattr(pis, "tf_submit", lambda y: c.append("submit") or UUID)
        monkeypatch.setattr(pis, "wait_for_reserve", lambda *a, **k: c.append("wait") or final)
        monkeypatch.setattr(pis, "tf_dut_ip", lambda j: "1.2.3.4")
        monkeypatch.setattr(pis, "tf_artifacts", lambda j, d: c.append("artifacts") or sub)
        monkeypatch.setattr(pis, "fetch_submission",
                            lambda ip, cid, d: c.append("submission") or sub)
        monkeypatch.setattr(pis, "tf_cancel", lambda j: c.append("cancel"))
        monkeypatch.setattr(pis, "verify_image",
                            lambda ip, iso: (not mismatch, "dcd-val"))

        def run(argv, **kw):
            if "rerun.py" in str(argv[1]):
                c.append("rerun")
                self.rerun_argv = argv
                if rerun_exc:
                    raise rerun_exc
            else:
                c.append("jira")
                self.jira_argv = argv
            return _cp("out")

        monkeypatch.setattr(subprocess, "run", run)
        self.wd = tmp_path / "wd"
        self.updates = []

    def go(self):
        return pis.run_cid("123456-12345", self.args, {}, "bundled:laptop", self.wd,
                           lambda r: self.updates.append((r.stage, r.status)))


def test_happy_path_cancel_before_jira(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    r = e.go()
    assert e.calls == ["render", "submit", "wait", "submission", "rerun", "cancel", "jira"]
    assert r.status == "done" and r.released and r.image_ok and r.jira_exit == 0
    assert r.rerun_tarball.endswith("submission_202607-39076_rerun.tar.xz")
    assert e.rerun_argv[-3:] == ["--output", r.rerun_tarball, "--force"]
    assert e.updates


def test_job_ends_before_reserve_no_rerun_no_jira(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, final="complete")
    r = e.go()
    assert r.status == "failed" and r.error == "job ended complete before reserve"
    assert "rerun" not in e.calls and "jira" not in e.calls
    assert "artifacts" in e.calls and "submission" not in e.calls
    assert "cancel" not in e.calls and r.released


def test_image_mismatch_skips_rerun_and_releases(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, mismatch=True)
    r = e.go()
    assert r.status == "failed" and r.error == "image mismatch: DUT DCD dcd-val"
    assert "rerun" not in e.calls and e.calls[-1] == "cancel" and r.released


def test_rerun_exception_marks_failed_and_releases(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, rerun_exc=OSError("boom"))
    r = e.go()
    assert r.status == "failed" and "boom" in r.error
    assert "jira" not in e.calls and e.calls[-1] == "cancel" and r.released


def test_dry_run_stops_after_render_no_submit(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, dry=True)
    r = e.go()
    assert e.calls == ["render"] and r.status == "done" and r.stage == "dry-run"


def test_jira_receives_batch_args_verbatim(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    r = e.go()
    assert e.jira_argv[-8:] == ["-p", "P", "-m", "m", "-a", "a", "-l", "l"]
    assert e.jira_argv[2] == r.rerun_tarball


def _rc_env(monkeypatch, tmp_path, rerun_rc=0, jira_rc=0):
    e = Env(monkeypatch, tmp_path)

    def run(argv, **kw):
        if "rerun.py" in str(argv[1]):
            e.calls.append("rerun")
            e.rerun_argv, e.rerun_kw = argv, kw
            return _cp("o", rerun_rc)
        e.calls.append("jira")
        e.jira_kw = kw
        return _cp("o", jira_rc)

    monkeypatch.setattr(subprocess, "run", run)
    return e


def test_rerun_passes_force_and_timeouts(monkeypatch, tmp_path):
    e = _rc_env(monkeypatch, tmp_path)
    e.go()
    assert "--force" in e.rerun_argv
    assert e.rerun_kw["timeout"] == 6000 and e.jira_kw["timeout"] == 1800


def test_resume_with_job_id_starts_at_wait(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    r = pis.run_cid("123456-12345", e.args, {}, "s", e.wd, lambda r: None, job_id=UUID)
    assert "render" not in e.calls and "submit" not in e.calls
    assert e.calls[0] == "wait" and r.status == "done" and r.job_id == UUID


def test_dry_run_with_job_id_does_nothing_remote(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, dry=True)
    r = pis.run_cid("123456-12345", e.args, {}, "s", e.wd, lambda r: None, job_id=UUID)
    assert e.calls == ["render"] and r.status == "done" and r.stage == "dry-run"


def test_jira_serialized_across_threads(monkeypatch, tmp_path):
    import time as _t
    e1 = Env(monkeypatch, tmp_path / "a")
    e2 = Env(monkeypatch, tmp_path / "b")
    state = {"active": 0, "max": 0}
    g = threading.Lock()

    def run(argv, **kw):
        if "rerun.py" in str(argv[1]):
            return _cp("o")
        with g:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        _t.sleep(0.05)
        with g:
            state["active"] -= 1
        return _cp("o")

    monkeypatch.setattr(subprocess, "run", run)
    out = []
    ts = [threading.Thread(target=lambda e=e: out.append(e.go())) for e in (e1, e2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert state["max"] == 1 and [r.status for r in out] == ["done", "done"]


def test_released_before_waiting_for_lock(monkeypatch, tmp_path):
    import time as _t
    e = Env(monkeypatch, tmp_path)
    pis.JIRA_LOCK.acquire()
    out = []
    t = threading.Thread(target=lambda: out.append(e.go()))
    try:
        t.start()
        for _ in range(200):
            if any(st == "jira_wait" for st, _s in e.updates):
                break
            _t.sleep(0.01)
        assert "cancel" in e.calls and "jira" not in e.calls
    finally:
        pis.JIRA_LOCK.release()
        t.join()
    assert e.calls.count("cancel") == 1 and out[0].status == "done"


def test_jira_exception_releases_lock(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    orig = subprocess.run

    def run(argv, **kw):
        if "rerun.py" not in str(argv[1]):
            raise subprocess.TimeoutExpired("x", 1)
        return orig(argv, **kw)

    monkeypatch.setattr(subprocess, "run", run)
    r = e.go()
    assert r.status == "failed" and r.released
    assert pis.JIRA_LOCK.acquire(blocking=False)
    pis.JIRA_LOCK.release()


def test_cancel_raises_still_files_no_double_cancel(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)

    def boom(j):
        e.calls.append("cancel")
        raise RuntimeError("tf down")

    monkeypatch.setattr(pis, "tf_cancel", boom)
    r = e.go()
    assert "jira" in e.calls and e.calls.count("cancel") == 1 and r.released


def test_jira_pause_called_inside_lock(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    monkeypatch.setattr(pis, "JIRA_PAUSE", 7)
    seen = []
    pis.run_cid("123456-12345", e.args, {}, "s", e.wd, lambda r: None,
                sleep=lambda n: seen.append((n, pis.JIRA_LOCK.locked())))
    assert (7, True) in seen


def test_jira_nonzero_marks_failed(monkeypatch, tmp_path):
    e = _rc_env(monkeypatch, tmp_path, jira_rc=3)
    r = e.go()
    assert r.status == "failed" and r.jira_exit == 3 and r.released


def test_rerun_nonzero_skips_jira(monkeypatch, tmp_path):
    e = _rc_env(monkeypatch, tmp_path, rerun_rc=1)
    r = e.go()
    assert r.status == "failed" and "jira" not in e.calls and e.calls[-1] == "cancel"


def test_failing_artifacts_keeps_original_error(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, final="cancelled")

    def boom(j, d):
        raise subprocess.TimeoutExpired("x", 1)

    monkeypatch.setattr(pis, "tf_artifacts", boom)
    r = e.go()
    assert r.status == "failed" and r.error == "job ended cancelled before reserve"


def test_wait_timeout_fails_and_cancels(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)

    def to(*a, **k):
        raise TimeoutError("late")

    monkeypatch.setattr(pis, "wait_for_reserve", to)
    r = e.go()
    assert r.status == "failed" and "late" in r.error
    assert e.calls[-1] == "cancel" and r.released


def test_stage_order_after_reserve(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    e.go()
    stages = []
    for st, _ in e.updates:
        if not stages or stages[-1] != st:
            stages.append(st)
    order = [s_ for s_ in stages
             if s_ in ("dut_ip", "image", "submission", "rerun", "jira_wait", "jira")]
    assert order == ["dut_ip", "image", "submission", "rerun", "jira_wait", "jira"]
    assert "artifacts" not in e.calls


def test_fetch_failure_fails_and_releases(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)

    def boom(ip, cid, d):
        raise RuntimeError("scp broke")

    monkeypatch.setattr(pis, "fetch_submission", boom)
    r = e.go()
    assert r.status == "failed" and "scp broke" in r.error
    assert "rerun" not in e.calls and e.calls[-1] == "cancel" and r.released


def test_job_id_recorded_immediately_after_submit(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    seen = []
    pis.run_cid("123456-12345", e.args, {}, "s", e.wd,
                lambda r: seen.append((r.stage, r.job_id)))
    assert ("submit", UUID) in seen


def test_dut_ip_retries_then_succeeds(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    ips = iter([None, None, "1.2.3.4"])
    monkeypatch.setattr(pis, "tf_dut_ip", lambda j: next(ips))
    sleeps = []
    r = pis.run_cid("123456-12345", e.args, {}, "s", e.wd, lambda r: None,
                    sleep=sleeps.append)
    assert r.status == "done" and sleeps == [30, 30]


def test_dut_ip_gives_up_after_10(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    n = []
    monkeypatch.setattr(pis, "tf_dut_ip", lambda j: n.append(1))
    sleeps = []
    r = pis.run_cid("123456-12345", e.args, {}, "s", e.wd, lambda r: None,
                    sleep=sleeps.append)
    assert r.status == "failed" and r.error == "no DUT IP in reserve output"
    assert len(n) == 10 and sleeps == [30] * 9
    assert e.calls[-1] == "cancel" and "rerun" not in e.calls


# ---- Task 6: main ----
import threading  # noqa: E402

C1, C2 = "202607-39077", "202607-39076"


@pytest.fixture
def mainenv(monkeypatch, tmp_path):
    calls = {"run": [], "cancel": []}
    monkeypatch.setattr(pis, "check_testflinger_login", lambda: None)
    monkeypatch.setattr(pis, "preflight", lambda a: None)
    monkeypatch.setattr(pis, "check_jira_args", lambda a, c: None)
    monkeypatch.setattr(pis, "git_fetch", lambda r: None)
    monkeypatch.setattr(pis, "resolve_manifest", lambda *a, **k: ({}, "bundled:laptop"))
    monkeypatch.setattr(pis, "tf_cancel", lambda j: calls["cancel"].append(j))

    def fake_run(cid, args, manifest, source, workdir, on_update, job_id=None):
        calls["run"].append((cid, job_id, str(workdir)))
        res = pis.CidResult(cid=cid, status="done", stage="done", job_id=job_id or "j-" + cid,
                            manifest_source=source)
        on_update(res)
        return res

    monkeypatch.setattr(pis, "run_cid", fake_run)
    calls["wd"] = tmp_path / "wd"
    return calls


def _argv(env, *extra):
    return [*BASE, "--workdir", str(env["wd"]), *extra]


def test_main_exit_0_all_done(mainenv):
    assert pis.main(_argv(mainenv, "--cid", C1, "--cid", C2)) == 0
    assert {c for c, _, _ in mainenv["run"]} == {C1, C2}
    assert mainenv["run"][0][2].endswith(mainenv["run"][0][0])


def test_main_exit_1_one_failed_others_run(mainenv, monkeypatch):
    def fake_run(cid, args, manifest, source, workdir, on_update, job_id=None):
        mainenv["run"].append(cid)
        res = pis.CidResult(cid=cid, status="failed" if cid == C1 else "done", error="x")
        on_update(res)
        return res

    monkeypatch.setattr(pis, "run_cid", fake_run)
    assert pis.main(_argv(mainenv, "--cid", C1, "--cid", C2)) == 1
    assert sorted(mainenv["run"]) == sorted([C1, C2])


def test_main_bad_cid_exit_2(mainenv):
    assert pis.main(_argv(mainenv, "--cid", "bad")) == 2


def test_main_preflight_exit_3(mainenv, monkeypatch):
    def boom(a):
        raise pis.PreflightError("nope")

    monkeypatch.setattr(pis, "preflight", boom)
    assert pis.main(_argv(mainenv, "--cid", C1)) == 3
    assert mainenv["run"] == []


def test_main_login_checked_first(mainenv, monkeypatch):
    def nologin():
        raise pis.PreflightError("login")

    def bad(*a, **k):
        raise AssertionError("called")

    monkeypatch.setattr(pis, "check_testflinger_login", nologin)
    monkeypatch.setattr(pis, "load_cids", bad)
    monkeypatch.setattr(pis, "preflight", bad)
    assert pis.main(_argv(mainenv, "--cid", C1)) == 3


def test_main_manifest_failure_exit_3(mainenv, monkeypatch):
    def bad(*a, **k):
        raise ValueError("bad json")

    monkeypatch.setattr(pis, "resolve_manifest", bad)
    assert pis.main(_argv(mainenv, "--cid", C1)) == 3
    assert mainenv["run"] == []


def test_results_file_written_and_valid_json(mainenv):
    pis.main(_argv(mainenv, "--cid", C1))
    data = json.loads((mainenv["wd"] / "results.json").read_text())
    assert data["iso"] == "http://x/a.iso" and data["form_factor"] == "laptop"
    assert data["cids"][C1]["status"] == "done"
    assert not list(mainenv["wd"].glob("*.tmp"))


def test_resume_skips_done_and_reuses_job_id(mainenv):
    wd = mainenv["wd"]
    wd.mkdir()
    rf = wd / "results.json"
    rf.write_text(json.dumps({"iso": "http://x/a.iso", "form_factor": "laptop", "cids": {
        C1: {"cid": C1, "status": "done", "stage": "done", "job_id": "old1"},
        C2: {"cid": C2, "status": "failed", "stage": "rerun", "job_id": "old2"},
        "202607-00002": {"cid": "202607-00002", "status": "failed", "stage": "rerun",
                         "job_id": "old3", "released": True},
        "202607-00001": {"cid": "202607-00001", "status": "failed", "stage": "submit", "job_id": ""},
    }}))
    assert pis.main([*BASE, "--resume", str(rf)]) == 0
    runs = {c: j for c, j, _ in mainenv["run"]}
    assert C1 not in runs
    assert runs[C2] == "old2"
    assert runs["202607-00001"] is None
    assert runs["202607-00002"] is None
    data = json.loads(rf.read_text())
    assert data["cids"][C1]["job_id"] == "old1"


def test_keyboard_interrupt_cancels_all_and_exits_130(mainenv, monkeypatch):
    ready = threading.Barrier(3)
    release = threading.Event()

    def fake_run(cid, args, manifest, source, workdir, on_update, job_id=None):
        res = pis.CidResult(cid=cid, status="running", stage="wait", job_id="job-" + cid)
        on_update(res)
        ready.wait(5)
        release.wait(10)
        return res

    monkeypatch.setattr(pis, "run_cid", fake_run)
    orig = pis.wait_futures
    timeouts = []

    def interrupting(futs, timeout=None):
        timeouts.append(timeout)
        if timeout is None:
            ready.wait(5)
            raise KeyboardInterrupt
        return None

    monkeypatch.setattr(pis, "wait_futures", interrupting)
    try:
        rc = pis.main(_argv(mainenv, "--cid", C1, "--cid", C2))
    finally:
        release.set()
        monkeypatch.setattr(pis, "wait_futures", orig)
    assert rc == 130
    assert timeouts == [None, 30]
    assert sorted(mainenv["cancel"]) == sorted(["job-" + C1, "job-" + C2])
    assert (mainenv["wd"] / "results.json").exists()


def test_keyboard_interrupt_skips_released_jobs(mainenv, monkeypatch):
    def fake_run(cid, args, manifest, source, workdir, on_update, job_id=None):
        res = pis.CidResult(cid=cid, status="failed", stage="wait", job_id="job-" + cid,
                            released=(cid == C1))
        on_update(res)
        return res

    monkeypatch.setattr(pis, "run_cid", fake_run)

    def interrupting(futs, timeout=None):
        if timeout is None:
            pis._futures_wait(futs)
            raise KeyboardInterrupt

    monkeypatch.setattr(pis, "wait_futures", interrupting)
    assert pis.main(_argv(mainenv, "--cid", C1, "--cid", C2)) == 130
    assert mainenv["cancel"] == ["job-" + C2]


def test_signal_handlers_installed_and_restored(mainenv, monkeypatch):
    import signal
    before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))
    seen = {}

    def fake_run(cid, args, manifest, source, workdir, on_update, job_id=None):
        seen["term"] = signal.getsignal(signal.SIGTERM)
        seen["hup"] = signal.getsignal(signal.SIGHUP)
        res = pis.CidResult(cid=cid, status="done")
        on_update(res)
        return res

    monkeypatch.setattr(pis, "run_cid", fake_run)
    assert pis.main(_argv(mainenv, "--cid", C1)) == 0
    for h in (seen["term"], seen["hup"]):
        assert callable(h) and h not in before
        with pytest.raises(KeyboardInterrupt):
            h(signal.SIGTERM, None)
    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP)) == before


def test_on_update_stores_snapshot(mainenv, monkeypatch):
    live = {}

    def fake_run(cid, args, manifest, source, workdir, on_update, job_id=None):
        res = pis.CidResult(cid=cid, status="running", stage="wait")
        on_update(res)
        res.stage = "mutated-after"
        live["res"] = res
        return pis.CidResult(cid=cid, status="done", stage="done")

    monkeypatch.setattr(pis, "run_cid", fake_run)
    pis.main(_argv(mainenv, "--cid", C1))
    data = json.loads((mainenv["wd"] / "results.json").read_text())
    assert data["cids"][C1]["stage"] == "done"


# ---- Jira arg check ----
_real_check_jira_args = pis.check_jira_args
@pytest.fixture
def jira_env(monkeypatch, tmp_path):
    fb = pis._load_file_bugs()
    for n in ("secrets", "c3"):
        (tmp_path / n).write_text("")
    cfg = tmp_path / "jira.json"
    cfg.write_text(json.dumps({"OEM_X86_JIRA_CONF": {"stella": {"jira_components": [
        {"component_name": "raccoon-milk"}, {"component_name": "raccoon-cream"}]}}}))
    monkeypatch.setattr(fb, "DEFAULT_SECRETS", str(tmp_path / "secrets"))
    monkeypatch.setattr(fb, "DEFAULT_C3", str(tmp_path / "c3"))
    monkeypatch.setattr(fb, "DEFAULT_JIRA_CONFIG", str(cfg))
    monkeypatch.setattr(fb.shutil, "which", lambda c: "/bin/" + c)
    monkeypatch.setattr(pis, "_load_file_bugs", lambda: fb)


def _jargs(p="stella", l="raccoon-milk", m="FC"):
    return type("A", (), {"p": p, "l": l, "m": m})()


def test_jira_valid(jira_env):
    pis.check_jira_args(_jargs(), [C1])


def test_jira_milestone_lowercase_ok(jira_env):
    pis.check_jira_args(_jargs(m="fc"), [C1])


def test_jira_bad_milestone(jira_env):
    with pytest.raises(pis.PreflightError, match="milestone"):
        pis.check_jira_args(_jargs(m="xx"), [C1])


def test_jira_bad_label_lists_valid(jira_env):
    with pytest.raises(pis.PreflightError) as ei:
        pis.check_jira_args(_jargs(l="raccoon-sun"), [C1])
    assert "raccoon-milk" in str(ei.value) and "raccoon-cream" in str(ei.value)


def test_jira_bad_project(jira_env):
    with pytest.raises(pis.PreflightError, match="not in jira config"):
        pis.check_jira_args(_jargs(p="nope"), [C1])


def test_main_bad_label_exit_3_no_submit(mainenv, monkeypatch, jira_env):
    monkeypatch.setattr(pis, "check_jira_args", _real_check_jira_args)
    def bad(*a, **k):
        raise AssertionError("called")
    monkeypatch.setattr(pis, "tf_submit", bad)
    assert pis.main(_argv(mainenv, "--cid", C1, "-p", "stella", "-m", "FC", "-l", "raccoon-sun")) == 3
    assert mainenv["run"] == []


# ---- stage output ----
import re  # noqa: E402


def test_log_format(capsys):
    pis.log("202601-38271", "hi")
    cap = capsys.readouterr()
    assert re.match(r"^\[\d\d:\d\d:\d\d\] 202601-38271  hi\n$", cap.err) and cap.out == ""


def test_log_swallows_oserror(monkeypatch):
    class Bad:
        def write(self, s):
            raise OSError("closed")

        def flush(self):
            raise OSError("closed")

    monkeypatch.setattr(sys, "stderr", Bad())
    pis.log("c", "m")


def _in_order(text, parts):
    pos = 0
    for p in parts:
        i = text.find(p, pos)
        assert i >= 0, f"{p!r} missing/out of order"
        pos = i + len(p)


def test_run_cid_emits_stage_lines(monkeypatch, tmp_path, capsys):
    e = Env(monkeypatch, tmp_path)
    e.go()
    _in_order(capsys.readouterr().err,
              [f"submitted job {UUID}", "stage: wait", "DUT 1.2.3.4", "image OK",
               "stage: rerun", "DUT released", "waiting for Jira lock", "stage: jira",
               "Jira exit 0", "done"])


def test_run_cid_failure_line(monkeypatch, tmp_path, capsys):
    e = Env(monkeypatch, tmp_path, mismatch=True)
    e.go()
    assert "failed: image mismatch: DUT DCD dcd-val" in capsys.readouterr().err


def test_dry_run_prints_stage_render(monkeypatch, tmp_path, capsys):
    e = Env(monkeypatch, tmp_path, dry=True)
    e.go()
    assert "stage: render" in capsys.readouterr().err


# ---- resume at Jira ----
def test_run_cid_resume_at_jira(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    tb = tmp_path / "x_rerun.tar.xz"
    tb.write_bytes(b"x")
    r = pis.run_cid("123456-12345", e.args, {}, "s", e.wd, lambda r: None,
                    job_id=UUID, rerun_tarball=str(tb))
    assert e.calls == ["jira"] and r.status == "done" and r.released
    assert e.jira_argv[2] == str(tb) and r.rerun_tarball == str(tb)


def _resume_jira_env(mainenv, monkeypatch, tarball):
    seen = {}

    def fake_run(cid, args, manifest, source, workdir, on_update, job_id=None, **kw):
        seen[cid] = (job_id, kw)
        res = pis.CidResult(cid=cid, status="done", stage="done", job_id=job_id or "")
        on_update(res)
        return res

    monkeypatch.setattr(pis, "run_cid", fake_run)
    wd = mainenv["wd"]
    wd.mkdir()
    rf = wd / "results.json"
    rf.write_text(json.dumps({"iso": "http://x/a.iso", "form_factor": "laptop", "cids": {
        C1: {"cid": C1, "status": "failed", "stage": "jira", "job_id": "j1",
             "released": True, "rerun_tarball": str(tarball)}}}))
    assert pis.main([*BASE, "--resume", str(rf)]) == 0
    return seen


def test_main_resume_at_jira_passes_tarball(mainenv, monkeypatch, tmp_path):
    tb = tmp_path / "t_rerun.tar.xz"
    tb.write_bytes(b"x")
    seen = _resume_jira_env(mainenv, monkeypatch, tb)
    assert seen[C1][1].get("rerun_tarball") == str(tb)


def test_main_resume_jira_missing_tarball_resubmits(mainenv, monkeypatch, tmp_path):
    seen = _resume_jira_env(mainenv, monkeypatch, tmp_path / "gone.tar.xz")
    assert seen[C1] == (None, {})


def test_main_resume_jira_unreleased_job_not_jira_only(mainenv, monkeypatch, tmp_path):
    tb = tmp_path / "t_rerun.tar.xz"
    tb.write_bytes(b"x")
    seen = {}

    def fake_run(cid, args, manifest, source, workdir, on_update, job_id=None, **kw):
        seen[cid] = (job_id, kw)
        res = pis.CidResult(cid=cid, status="done", stage="done", job_id=job_id or "")
        on_update(res)
        return res

    monkeypatch.setattr(pis, "run_cid", fake_run)
    wd = mainenv["wd"]
    wd.mkdir()
    rf = wd / "results.json"
    rf.write_text(json.dumps({"iso": "http://x/a.iso", "form_factor": "laptop", "cids": {
        C1: {"cid": C1, "status": "failed", "stage": "jira", "job_id": "j1",
             "released": False, "rerun_tarball": str(tb)}}}))
    assert pis.main([*BASE, "--resume", str(rf)]) == 0
    assert seen[C1] == ("j1", {})


def test_released_published_right_after_cancel(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    snaps = []
    pis.run_cid("123456-12345", e.args, {}, "s", e.wd,
                lambda r: snaps.append((r.stage, r.released)))
    first = next(i for i, (_s, rel) in enumerate(snaps) if rel)
    assert snaps[first][0] == "rerun"
