import argparse
import json
import re
import subprocess
import subprocess as _sp
import sys
import tarfile
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import imgsanity as m  # noqa: E402

C1 = "202607-39077"


def test_load_cids_merges_file_and_flags(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("# c\n202607-39077\n\n202607-39076\n")
    assert m.load_cids(["202607-39076"], str(f)) == ["202607-39076", "202607-39077"]


def test_load_cids_rejects_bad():
    with pytest.raises(ValueError, match="2026-1"):
        m.load_cids(["2026-1"], None)


def _fake_skill(tmp_path, monkeypatch, manifest=True):
    skill = tmp_path / "pc-image-sanity"
    (skill / "manifests").mkdir(parents=True)
    if manifest:
        (skill / "manifests" / "manifest_laptop.json").write_text("{}")
    skills = tmp_path
    for p, name in ((m, "RENDER"), (m, "RERUN"), (m, "FILE_BUGS")):
        f = tmp_path / name
        f.write_text("")
        monkeypatch.setattr(p, name, f)
    monkeypatch.setattr(m, "SKILL_DIR", skill)
    monkeypatch.setattr(m, "SKILLS", skills)
    return skill


def test_preflight_missing_bundled_manifest(tmp_path, monkeypatch):
    _fake_skill(tmp_path, monkeypatch, manifest=False)
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(m.PreflightError, match="manifest"):
        m.preflight_checks(str(tmp_path / "repo"), "laptop", None, "http://x/a.iso")


def test_preflight_iso_404(tmp_path, monkeypatch):
    _fake_skill(tmp_path, monkeypatch)
    (tmp_path / "repo" / ".git").mkdir(parents=True)

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

    monkeypatch.setattr(m.urllib.request, "urlopen", boom)
    with pytest.raises(m.PreflightError, match="http://x/a.iso"):
        m.preflight_checks(str(tmp_path / "repo"), "laptop", None, "http://x/a.iso")


@pytest.fixture(autouse=True)
def _no_real_tf_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DEFAULT_TF_SECRETS", str(tmp_path / "none.ini"))


def test_tf_creds_parse_quotes_export_comments(tmp_path):
    f = tmp_path / "tf.ini"
    f.write_text("# c\n\nexport TESTFLINGER_CLIENT_ID='id1'\n"
                 'TESTFLINGER_SECRET_KEY="s=e=c"\nOTHER=x\n')
    assert m.load_tf_credentials(str(f)) == {
        "TESTFLINGER_CLIENT_ID": "id1", "TESTFLINGER_SECRET_KEY": "s=e=c"}


def test_tf_creds_missing_file_none():
    assert m.load_tf_credentials() is None


def test_tf_creds_missing_key_none(tmp_path):
    f = tmp_path / "tf.ini"
    f.write_text("TESTFLINGER_CLIENT_ID=id1\nTESTFLINGER_SECRET_KEY=\n")
    assert m.load_tf_credentials(str(f)) is None


def _run(monkeypatch, out="", rc=0, exc=None):
    def fake(cmd, **kw):
        assert cmd == ["testflinger", "list-queues", "--json"]
        assert kw["stdin"] == subprocess.DEVNULL and kw["timeout"] == 60
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, rc, out, "")

    monkeypatch.setattr(m.subprocess, "run", fake)


def test_login_check_device_flow_raises(monkeypatch):
    _run(monkeypatch, "Please visit https://login.canonical.com/oauth2/device/verify")
    with pytest.raises(m.PreflightError, match="testflinger login"):
        m.check_testflinger_login()


def test_login_check_timeout_raises(monkeypatch):
    _run(monkeypatch, exc=subprocess.TimeoutExpired("testflinger", 60))
    with pytest.raises(m.PreflightError, match="not logged in"):
        m.check_testflinger_login()


def test_login_check_ok(monkeypatch):
    _run(monkeypatch, "{}")
    m.check_testflinger_login()


def test_login_check_missing_binary(monkeypatch):
    _run(monkeypatch, exc=FileNotFoundError("testflinger"))
    with pytest.raises(m.PreflightError, match="not found in PATH"):
        m.check_testflinger_login()


def test_login_check_nonzero_exit(monkeypatch):
    _run(monkeypatch, "boom", rc=1)
    with pytest.raises(m.PreflightError, match="not logged in"):
        m.check_testflinger_login()


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
    mf, s = m.resolve_manifest("202607-39076", manifest_repo, "laptop", None, False)
    assert s == "branch:origin/202607-39076" and mf == {"from": "branch"}


def test_manifest_from_main(manifest_repo):
    mf, s = m.resolve_manifest("202607-39077", manifest_repo, "laptop", None, False)
    assert s == "main:pc/202607-39077" and mf == {"from": "main"}


def test_manifest_branch_without_file_falls_to_main_or_bundled(manifest_repo):
    mf, s = m.resolve_manifest("202607-39078", manifest_repo, "laptop", None, False)
    assert s == "bundled:laptop"
    bundled = m.SKILL_DIR / "manifests" / "manifest_laptop.json"
    assert mf == json.loads(bundled.read_text())


def test_manifest_override(manifest_repo, tmp_path):
    f = tmp_path / "o.json"
    f.write_text('{"o": 1}')
    mf, s = m.resolve_manifest("202607-39076", manifest_repo, "laptop", str(f), False)
    assert s == f"file:{f}" and mf == {"o": 1}


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
    m.git_fetch(manifest_repo)
    with pytest.raises(ValueError):
        m.resolve_manifest("202607-39099", manifest_repo, "laptop", None, False)


def test_git_fetch_failure(tmp_path):
    with pytest.raises(m.PreflightError):
        m.git_fetch(str(tmp_path))


def test_render_job_appends_reserve(tmp_path):
    pytest.importorskip("jinja2")
    yaml = pytest.importorskip("yaml")
    out = tmp_path / "new" / "dir"
    p = m.render_job("202607-39076", "http://x/a.iso", {"k": "v"}, "dirk", out, "pc-sanity-smoke-test-26-04")
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
    p = m.render_job(cid, "http://x/a.iso", {"k": "v"}, "dirk", tmp_path, "pc-sanity-smoke-test-26-04")
    doc = yaml.safe_load(p.read_text())
    lines = doc["test_data"]["test_cmds"].splitlines()
    assert lines[-2:] == [
        f'_put "$SUBMISSION_TAR_PATH" /home/ubuntu/submission_{cid}.tar.xz',
        'cp "$SUBMISSION_TAR_PATH" artifacts/ || true',
    ]
    assert lines[-3].startswith("checkbox-cli submit ")
    assert doc["reserve_data"] == {"ssh_keys": ["lp:dirk"], "timeout": 7200}



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
    assert m.tf_submit(tmp_path / "j.yaml") == UUID
    assert seen["argv"] == ["testflinger", "submit", "--quiet", "-"]
    assert seen["kw"]["input"] == "job_queue: q\n" and seen["kw"]["timeout"]


def test_submit_bad_output_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("Please login\n"))
    (tmp_path / "j.yaml").write_text("x: 1\n")
    with pytest.raises(RuntimeError, match="Please login"):
        m.tf_submit(tmp_path / "j.yaml")


def _seq(statuses):
    it = iter(statuses)
    return lambda a, **k: _cp(next(it) + "\n")


def test_wait_for_reserve_tolerates_transient_errors(monkeypatch):
    calls = iter([RuntimeError("auth timeout"), RuntimeError("auth timeout"), "test", "reserve"])

    def fake(job):
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(m, "tf_status", fake)
    assert m.wait_for_reserve(UUID, 5, 1000, sleep=lambda s: None) == "reserve"


def test_wait_for_reserve_gives_up_after_repeated_errors(monkeypatch):
    def fake(job):
        raise RuntimeError("auth timeout")

    monkeypatch.setattr(m, "tf_status", fake)
    with pytest.raises(RuntimeError, match="in a row"):
        m.wait_for_reserve(UUID, 5, 100000, sleep=lambda s: None)


def test_wait_for_reserve_sequence(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _seq(["setup", "provision", "test", "Reserve"]))
    sleeps = []
    assert m.wait_for_reserve(UUID, 5, 1000, sleep=sleeps.append) == "reserve"
    assert len(sleeps) == 3


def test_wait_ends_on_complete_without_reserve(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _seq(["provision", "complete"]))
    assert m.wait_for_reserve(UUID, 5, 1000, sleep=lambda s: None) == "complete"


def test_wait_timeout(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("test\n"))
    t = [0]

    def sleep(s):
        t[0] += s

    with pytest.raises(TimeoutError):
        m.wait_for_reserve(UUID, 10, 25, sleep=sleep, now=lambda: t[0])


def test_status_timeout_raises(monkeypatch):
    def fake(a, **k):
        raise subprocess.TimeoutExpired(a, 1)

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(subprocess.TimeoutExpired):
        m.tf_status(UUID)


def test_dut_ip_parsed(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("x ssh ubuntu@10.102.1.5 y"))
    assert m.tf_dut_ip(UUID) == "10.102.1.5"
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("nothing"))
    assert m.tf_dut_ip(UUID) is None


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
    r = m.tf_artifacts(UUID, dest)
    assert r.name == "submission_202607-39076.tar.xz" and r.exists()
    assert seen_out[0].parent.parent == tmp_path  # written under $HOME, not /tmp
    assert not list(tmp_path.glob("pc-image-sanity-*"))


def test_artifacts_none_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("", 1))
    assert m.tf_artifacts(UUID, tmp_path / "d") is None


def test_fetch_submission_scp(monkeypatch, tmp_path):
    seen = {}

    def fake(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return _cp()

    monkeypatch.setattr(subprocess, "run", fake)
    dest = tmp_path / "d"
    r = m.fetch_submission("1.2.3.4", "202607-39076", dest)
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
        m.fetch_submission("1.2.3.4", "202607-39076", tmp_path)


def test_cancel_swallow_errors(monkeypatch):
    def fake(a, **k):
        raise subprocess.TimeoutExpired(a, 1)

    monkeypatch.setattr(subprocess, "run", fake)
    m.tf_cancel(UUID)
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("", 1))
    m.tf_cancel(UUID)


DCD = '"DCD": "canonical-oem-stella-resolute-oem-26.04b-edge-20261020-833"\n'


def test_build_id():
    assert m.build_id("canonical-oem-stella-resolute-oem-26.04b-edge-20261020-833") == "20261020-833"
    assert m.build_id("http://h/stella-resolute-oem-26.04a-20261103-1091.iso") == "20261103-1091"
    assert m.build_id("nothing") is None


def test_verify_image_parses_dcd(monkeypatch):
    seen = {}

    def fake(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return _cp(DCD)

    monkeypatch.setattr(subprocess, "run", fake)
    ok, dcd = m.verify_image("1.2.3.4", "http://h/x-20261020-833.iso")
    assert ok and dcd.endswith("20261020-833")
    assert seen["kw"]["env"]["SSHPASS"] == "insecure" and seen["kw"]["timeout"] == 60
    assert seen["argv"][:2] == ["sshpass", "-e"]
    ok, _ = m.verify_image("1.2.3.4", "http://h/x-20261103-1091.iso")
    assert not ok


# ---- Jira arg check ----
@pytest.fixture
def jira_env(monkeypatch, tmp_path):
    fb = m._load_file_bugs()
    for n in ("secrets", "c3"):
        (tmp_path / n).write_text("")
    cfg = tmp_path / "jira.json"
    cfg.write_text(json.dumps({"OEM_X86_JIRA_CONF": {"stella": {"jira_components": [
        {"component_name": "raccoon-milk"}, {"component_name": "raccoon-cream"}]}}}))
    monkeypatch.setattr(fb, "DEFAULT_SECRETS", str(tmp_path / "secrets"))
    monkeypatch.setattr(fb, "DEFAULT_C3", str(tmp_path / "c3"))
    monkeypatch.setattr(fb, "DEFAULT_JIRA_CONFIG", str(cfg))
    monkeypatch.setattr(fb.shutil, "which", lambda c: "/bin/" + c)
    monkeypatch.setattr(m, "_load_file_bugs", lambda: fb)




def test_jira_valid(jira_env):
    m.check_jira_args("stella", "FC", "raccoon-milk", [C1])


def test_jira_milestone_lowercase_ok(jira_env):
    m.check_jira_args("stella", "fc", "raccoon-milk", [C1])


def test_jira_bad_milestone(jira_env):
    with pytest.raises(m.PreflightError, match="milestone"):
        m.check_jira_args("stella", "xx", "raccoon-milk", [C1])


def test_jira_bad_label_lists_valid(jira_env):
    with pytest.raises(m.PreflightError) as ei:
        m.check_jira_args("stella", "FC", "raccoon-sun", [C1])
    assert "raccoon-milk" in str(ei.value) and "raccoon-cream" in str(ei.value)


def test_jira_bad_project(jira_env):
    with pytest.raises(m.PreflightError, match="not in jira config"):
        m.check_jira_args("nope", "FC", "raccoon-milk", [C1])


import re  # noqa: E402


def test_log_format(capsys):
    m.log("202601-38271", "hi")
    cap = capsys.readouterr()
    assert re.match(r"^\[\d\d:\d\d:\d\d\] 202601-38271  hi\n$", cap.err) and cap.out == ""


def test_log_swallows_oserror(monkeypatch):
    class Bad:
        def write(self, s):
            raise OSError("closed")

        def flush(self):
            raise OSError("closed")

    monkeypatch.setattr(sys, "stderr", Bad())
    m.log("c", "m")



def test_state_roundtrip_atomic(tmp_path):
    m.save_batch(tmp_path, {"cids": ["202601-38271"]})
    m.save_state(tmp_path, {"cid": "202601-38271", "status": "pending"})
    assert m.load_state(tmp_path, "202601-38271")["status"] == "pending"
    assert not list((tmp_path / "202601-38271").glob("*.tmp"))


def test_load_state_unknown_cid(tmp_path):
    m.save_batch(tmp_path, {"cids": []})
    with pytest.raises(m.UsageError):
        m.load_state(tmp_path, "202601-00000")


def test_load_state_no_state_file(tmp_path):
    m.save_batch(tmp_path, {"cids": ["202601-38271"]})
    with pytest.raises(m.UsageError):
        m.load_state(tmp_path, "202601-38271")


def test_require_names_step():
    with pytest.raises(m.UsageError, match="dut_ip missing; run 'wait' first"):
        m.require({"dut_ip": ""}, "dut_ip", "wait")


@pytest.mark.parametrize("iso,plan", [
    ("http://h/stella-noble-hwe-proposed-20261106-1174.iso", "pc-sanity-smoke-test-24-04"),
    ("http://h/stella-resolute-oem-26.04a-20261105-1151.iso?x=1", "pc-sanity-smoke-test-26-04"),
])
def test_test_plan_for_iso(iso, plan):
    assert m.test_plan_for_iso(iso)[0] == plan


def test_test_plan_source_label():
    assert m.test_plan_for_iso("http://h/s-noble-20261106-1.iso")[1] == "iso:noble"


def test_test_plan_unknown_codename():
    with pytest.raises(m.PreflightError, match="noble"):
        m.test_plan_for_iso("http://h/foo-jammy-20260101-1.iso")


def test_render_job_uses_given_plan(tmp_path):
    pytest.importorskip("jinja2")
    p = m.render_job("202607-39076", "http://x/a.iso", {}, "dirk", tmp_path,
                     "pc-sanity-smoke-test-24-04")
    text = p.read_text()
    assert "pc-sanity-smoke-test-24-04" in text and "26-04" not in text


BASE = [
    "--iso", "http://x/pc-noble-20260101-1.iso", "--form-factor", "laptop", "--lp-user", "u",
    "-p", "P", "-m", "m", "-a", "a", "-l", "l",
]


@pytest.fixture
def mainenv(monkeypatch, tmp_path):
    calls = {"fetch": 0}
    monkeypatch.setattr(m, "check_testflinger_login", lambda: None)
    monkeypatch.setattr(m, "check_jira_args", lambda p, mm, l, cids: None)
    monkeypatch.setattr(m, "preflight_checks", lambda *a, **k: None)

    def fetch(r):
        calls["fetch"] += 1

    monkeypatch.setattr(m, "git_fetch", fetch)
    return calls


def test_preflight_writes_batch_and_states(mainenv, tmp_path, capsys):
    rc = m.main(["preflight", "--cid", C1, *BASE, "--batch", str(tmp_path)])
    assert rc == 0
    b = m.load_batch(tmp_path)
    assert b["cids"] == [C1]
    assert b["test_plan"] == "pc-sanity-smoke-test-24-04"
    assert b["test_plan_source"] == "iso:noble"
    s = m.load_state(tmp_path, C1)
    assert s["status"] == "pending" and s["stage"] == ""
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert last["batch"] == str(tmp_path)


def test_preflight_test_plan_flag(mainenv, tmp_path):
    assert m.main(["preflight", "--cid", C1, *BASE, "--test-plan", "tp", "--batch", str(tmp_path)]) == 0
    b = m.load_batch(tmp_path)
    assert (b["test_plan"], b["test_plan_source"]) == ("tp", "flag")


def test_preflight_keeps_existing_state(mainenv, tmp_path):
    m.save_batch(tmp_path, {"cids": [C1]})
    m.save_state(tmp_path, {"cid": C1, "status": "done", "stage": "x"})
    assert m.main(["preflight", "--cid", C1, *BASE, "--batch", str(tmp_path)]) == 0
    assert m.load_state(tmp_path, C1)["status"] == "done"


def test_preflight_bad_cid_exit_2(mainenv, tmp_path):
    assert m.main(["preflight", "--cid", "bad", *BASE, "--batch", str(tmp_path)]) == 2


def test_preflight_login_first_exit_3(mainenv, monkeypatch, tmp_path):
    def nologin():
        raise m.PreflightError("login")

    def bad(*a, **k):
        raise AssertionError("called")

    monkeypatch.setattr(m, "check_testflinger_login", nologin)
    monkeypatch.setattr(m, "check_jira_args", bad)
    monkeypatch.setattr(m, "preflight_checks", bad)
    assert m.main(["preflight", "--cid", C1, *BASE, "--batch", str(tmp_path)]) == 3


def test_preflight_bad_jira_before_rest(mainenv, monkeypatch, tmp_path):
    def jira(*a):
        raise m.PreflightError("label")

    def bad(*a, **k):
        raise AssertionError("called")

    monkeypatch.setattr(m, "check_jira_args", jira)
    monkeypatch.setattr(m, "preflight_checks", bad)
    monkeypatch.setattr(m, "git_fetch", bad)
    assert m.main(["preflight", "--cid", C1, *BASE, "--batch", str(tmp_path)]) == 3
    assert not (tmp_path / "batch.json").exists()


def test_preflight_iso_404_exit_3(mainenv, monkeypatch, tmp_path):
    def pc(*a, **k):
        raise m.PreflightError("HTTP 404")

    monkeypatch.setattr(m, "preflight_checks", pc)
    assert m.main(["preflight", "--cid", C1, *BASE, "--batch", str(tmp_path)]) == 3


def test_preflight_no_fetch(mainenv, tmp_path):
    m.main(["preflight", "--cid", C1, *BASE, "--no-fetch", "--batch", str(tmp_path / "a")])
    assert mainenv["fetch"] == 0
    m.main(["preflight", "--cid", C1, *BASE, "--batch", str(tmp_path / "b")])
    assert mainenv["fetch"] == 1


def test_missing_batch_exit_2(tmp_path):
    assert m.main(["status", "--batch", str(tmp_path / "nope")]) == 2


def test_unexpected_exception_marks_failed(mainenv, monkeypatch, tmp_path):
    m.main(["preflight", "--cid", C1, *BASE, "--batch", str(tmp_path)])

    def boom(args):
        raise OSError("disk")

    monkeypatch.setitem(m.HANDLERS, "dummy", boom)
    monkeypatch.setitem(m.PER_CID, "dummy", True)
    assert m.run_step("dummy", argparse.Namespace(batch=str(tmp_path), cid=C1)) == 1
    s = m.load_state(tmp_path, C1)
    assert s["status"] == "failed" and s["error"].startswith("OSError")


def test_step_error_and_usage_error(mainenv, monkeypatch, tmp_path):
    m.main(["preflight", "--cid", C1, *BASE, "--batch", str(tmp_path)])
    exc = {}

    def h(args):
        raise exc["e"]

    monkeypatch.setitem(m.HANDLERS, "dummy", h)
    monkeypatch.setitem(m.PER_CID, "dummy", True)
    ns = argparse.Namespace(batch=str(tmp_path), cid=C1)
    exc["e"] = m.UsageError("u")
    assert m.run_step("dummy", ns) == 2
    assert m.load_state(tmp_path, C1)["status"] == "pending"
    exc["e"] = m.StepError("bad")
    assert m.run_step("dummy", ns) == 1
    assert m.load_state(tmp_path, C1)["error"] == "bad"


def test_status_writes_results_json(tmp_path, capsys):
    m.save_batch(tmp_path, {"cids": [C1, "202607-39076"]})
    m.save_state(tmp_path, {"cid": C1, "status": "done", "stage": "x"})
    m.save_state(tmp_path, {"cid": "202607-39076", "status": "failed", "stage": "y", "error": "e"})
    assert m.main(["status", "--batch", str(tmp_path)]) == 0
    res = json.loads((tmp_path / "results.json").read_text())
    assert [r["cid"] for r in res] == [C1, "202607-39076"]
    cap = capsys.readouterr()
    assert C1 in cap.err
    assert json.loads(cap.out.strip().splitlines()[-1])["ok"] is False


def test_default_batch_dir(mainenv, monkeypatch, tmp_path, capsys):
    assert str(m.BATCH_ROOT) == "/tmp/opencode/pc-image-sanity"
    monkeypatch.setattr(m, "BATCH_ROOT", tmp_path)
    assert m.main(["preflight", "--cid", C1, *BASE]) == 0
    b = Path(json.loads(capsys.readouterr().out.strip().splitlines()[-1])["batch"])
    assert b.parent == tmp_path and re.fullmatch(r"\d{8}-\d{6}", b.name)


def test_non_per_cid_unexpected_exception_exit_1(mainenv, monkeypatch, tmp_path, capsys):
    def boom(r):
        raise OSError("net")

    monkeypatch.setattr(m, "git_fetch", boom)
    assert m.main(["preflight", "--cid", C1, *BASE, "--batch", str(tmp_path)]) == 1
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert last["ok"] is False and last["step"] == "preflight" and "OSError" in last["error"]


def test_status_malformed_json_exit_1(tmp_path):
    m.save_batch(tmp_path, {"cids": [C1]})
    (tmp_path / C1).mkdir()
    (tmp_path / C1 / "state.json").write_text("{bad")
    assert m.main(["status", "--batch", str(tmp_path)]) == 1


def test_failure_summary_carries_state(mainenv, monkeypatch, tmp_path, capsys):
    m.main(["preflight", "--cid", C1, *BASE, "--batch", str(tmp_path)])
    capsys.readouterr()

    def h(args):
        raise m.StepError("bad")

    monkeypatch.setitem(m.HANDLERS, "dummy", h)
    monkeypatch.setitem(m.PER_CID, "dummy", True)
    m.run_step("dummy", argparse.Namespace(batch=str(tmp_path), cid=C1))
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert last["cid"] == C1 and last["status"] == "failed" and last["stage"] == "" and last["ok"] is False


@pytest.fixture
def sub(mainenv, monkeypatch, tmp_path):
    m.main(["preflight", "--cid", C1, *BASE, "--batch", str(tmp_path)])
    calls = []
    monkeypatch.setattr(m, "resolve_manifest", lambda *a, **k: ({"a": 1}, "bundled:laptop"))
    monkeypatch.setattr(m, "render_job", lambda cid, iso, mf, lp, out, plan: calls.append(("render", plan)) or Path(out) / "job.yaml")
    monkeypatch.setattr(m, "tf_submit", lambda j: calls.append(("submit",)) or "job-new")
    monkeypatch.setattr(m, "tf_cancel", lambda j: calls.append(("cancel", j)) or True)
    return calls


def _sub_args(tmp_path, *extra):
    return ["submit", "--batch", str(tmp_path), "--cid", C1, *extra]


def test_submit_records_job(sub, tmp_path):
    s0 = m.load_state(tmp_path, C1)
    s0.update(dut_ip="1.2.3.4", error="x", image_ok=True)
    m.save_state(tmp_path, s0)
    assert m.main(_sub_args(tmp_path)) == 0
    s = m.load_state(tmp_path, C1)
    assert s["job_id"] == "job-new" and s["job_held"] is True
    assert s["manifest_source"] == "bundled:laptop"
    assert s["stage"] == "submitted" and s["status"] == "running"
    assert not s.get("dut_ip") and not s.get("error") and not s.get("image_ok")


def test_submit_dry_run_no_tf_call(sub, tmp_path):
    assert m.main(_sub_args(tmp_path, "--dry-run")) == 0
    s = m.load_state(tmp_path, C1)
    assert s["stage"] == "" and "job_id" not in s
    assert [c[0] for c in sub] == ["render"]


def test_submit_refuses_when_held(sub, tmp_path, capsys):
    s = m.load_state(tmp_path, C1)
    s.update(job_id="old", job_held=True)
    m.save_state(tmp_path, s)
    before = m.load_state(tmp_path, C1)
    assert m.main(_sub_args(tmp_path)) == 2
    assert "job old still held; run 'release' or pass --force" in capsys.readouterr().err
    assert ("submit",) not in sub
    assert m.load_state(tmp_path, C1) == before


def test_submit_force_cancels_then_submits(sub, tmp_path):
    s = m.load_state(tmp_path, C1)
    s.update(job_id="old", job_held=True)
    m.save_state(tmp_path, s)
    assert m.main(_sub_args(tmp_path, "--force")) == 0
    assert [c[0] for c in sub] == ["cancel", "render", "submit"]
    assert sub[0] == ("cancel", "old")
    assert m.load_state(tmp_path, C1)["job_id"] == "job-new"


def test_release_idempotent(sub, tmp_path):
    a = ["release", "--batch", str(tmp_path), "--cid", C1]
    assert m.main(a) == 0
    assert sub == []
    s = m.load_state(tmp_path, C1)
    s.update(job_id="j", job_held=True)
    m.save_state(tmp_path, s)
    assert m.main(a) == 0
    assert sub == [("cancel", "j")]
    assert m.load_state(tmp_path, C1)["job_held"] is False
    assert m.main(a) == 0
    assert sub == [("cancel", "j")]


def test_submit_force_then_submit_failure_persists_release(sub, monkeypatch, tmp_path):
    s = m.load_state(tmp_path, C1)
    s.update(job_id="old", job_held=True)
    m.save_state(tmp_path, s)

    def boom(j):
        raise RuntimeError("tf down")

    monkeypatch.setattr(m, "tf_submit", boom)
    assert m.main(_sub_args(tmp_path, "--force")) == 1
    s = m.load_state(tmp_path, C1)
    assert s["job_held"] is False and s["status"] == "failed"


def test_submit_force_dry_run_no_cancel(sub, tmp_path):
    s = m.load_state(tmp_path, C1)
    s.update(job_id="old", job_held=True)
    m.save_state(tmp_path, s)
    assert m.main(_sub_args(tmp_path, "--force", "--dry-run")) == 0
    assert ("cancel", "old") not in sub
    s = m.load_state(tmp_path, C1)
    assert s["job_held"] is True and s["job_id"] == "old"


def _seed(tmp_path, **kw):
    s = m.load_state(tmp_path, C1)
    s.update(kw)
    m.save_state(tmp_path, s)


def _wait_args(tmp_path, *extra):
    return ["wait", "--batch", str(tmp_path), "--cid", C1, *extra]


def test_wait_sets_dut_ip(sub, monkeypatch, tmp_path):
    _seed(tmp_path, job_id="j", job_held=True)
    monkeypatch.setattr(m, "wait_for_reserve", lambda *a, **k: "reserve")
    monkeypatch.setattr(m, "tf_dut_ip", lambda j: "10.0.0.5")
    assert m.main(_wait_args(tmp_path)) == 0
    s = m.load_state(tmp_path, C1)
    assert s["dut_ip"] == "10.0.0.5" and s["stage"] == "reserved" and s["job_held"] is True


def test_wait_passes_options(sub, monkeypatch, tmp_path):
    _seed(tmp_path, job_id="j", job_held=True)
    seen = {}
    monkeypatch.setattr(m, "wait_for_reserve", lambda j, p, mw, **k: seen.update(p=p, mw=mw) or "reserve")
    monkeypatch.setattr(m, "tf_dut_ip", lambda j: "1.2.3.4")
    assert m.main(_wait_args(tmp_path)) == 0
    assert seen == {"p": 60, "mw": 43200}
    assert m.main(_wait_args(tmp_path, "--poll-interval", "5", "--max-wait", "99")) == 0
    assert seen == {"p": 5, "mw": 99}


def test_wait_without_job_exit_2(sub, tmp_path):
    assert m.main(_wait_args(tmp_path)) == 2


def test_wait_ended_marks_not_held(sub, monkeypatch, tmp_path):
    _seed(tmp_path, job_id="j", job_held=True)
    monkeypatch.setattr(m, "wait_for_reserve", lambda *a, **k: "complete")
    arts = []
    monkeypatch.setattr(m, "tf_artifacts", lambda j, d: arts.append((j, d)))
    assert m.main(_wait_args(tmp_path)) == 1
    s = m.load_state(tmp_path, C1)
    assert s["job_held"] is False and s["status"] == "failed"
    assert s["error"] == "job ended complete before reserve"
    assert arts == [("j", tmp_path / C1 / "artifacts")]


def test_wait_ended_artifacts_failure_swallowed(sub, monkeypatch, tmp_path):
    _seed(tmp_path, job_id="j", job_held=True)
    monkeypatch.setattr(m, "wait_for_reserve", lambda *a, **k: "cancelled")

    def boom(j, d):
        raise RuntimeError("x")

    monkeypatch.setattr(m, "tf_artifacts", boom)
    assert m.main(_wait_args(tmp_path)) == 1
    assert m.load_state(tmp_path, C1)["error"] == "job ended cancelled before reserve"


def test_wait_timeout_keeps_held(sub, monkeypatch, tmp_path):
    _seed(tmp_path, job_id="j", job_held=True)

    def to(*a, **k):
        raise TimeoutError("not reserved")

    monkeypatch.setattr(m, "wait_for_reserve", to)
    assert m.main(_wait_args(tmp_path)) == 1
    s = m.load_state(tmp_path, C1)
    assert s["job_held"] is True and s["status"] == "failed"


def test_dut_ip_retries_then_succeeds(sub, monkeypatch, tmp_path):
    _seed(tmp_path, job_id="j", job_held=True)
    monkeypatch.setattr(m, "wait_for_reserve", lambda *a, **k: "reserve")
    ips = iter([None, None, "9.9.9.9"])
    monkeypatch.setattr(m, "tf_dut_ip", lambda j: next(ips))
    sleeps = []
    monkeypatch.setattr(m, "_sleep", sleeps.append)
    assert m.main(_wait_args(tmp_path)) == 0
    assert sleeps == [30, 30]
    assert m.load_state(tmp_path, C1)["dut_ip"] == "9.9.9.9"


def test_dut_ip_never_found(sub, monkeypatch, tmp_path):
    _seed(tmp_path, job_id="j", job_held=True)
    monkeypatch.setattr(m, "wait_for_reserve", lambda *a, **k: "reserve")
    calls = []
    monkeypatch.setattr(m, "tf_dut_ip", lambda j: calls.append(1))
    monkeypatch.setattr(m, "_sleep", lambda s: None)
    assert m.main(_wait_args(tmp_path)) == 1
    assert len(calls) == 10
    assert m.load_state(tmp_path, C1)["error"] == "no DUT IP in reserve output"


def _vi_args(tmp_path):
    return ["verify-image", "--batch", str(tmp_path), "--cid", C1]


def test_verify_image_ok(sub, monkeypatch, tmp_path):
    _seed(tmp_path, dut_ip="1.1.1.1")
    monkeypatch.setattr(m, "verify_image", lambda ip, iso: (True, "x-20260101-1"))
    assert m.main(_vi_args(tmp_path)) == 0
    s = m.load_state(tmp_path, C1)
    assert s["image_ok"] is True and s["dcd"] == "x-20260101-1"


def test_verify_image_mismatch_persists(sub, monkeypatch, tmp_path):
    _seed(tmp_path, dut_ip="1.1.1.1")
    monkeypatch.setattr(m, "verify_image", lambda ip, iso: (False, "other-20250101-9"))
    assert m.main(_vi_args(tmp_path)) == 1
    s = m.load_state(tmp_path, C1)
    assert s["image_ok"] is False and s["dcd"] == "other-20250101-9"
    iso_id = m.build_id(Path(m.load_batch(tmp_path)["iso"].split("?")[0]).name)
    assert s["error"] == f"image mismatch: DUT DCD other-20250101-9, expected {iso_id}"


def test_verify_image_without_dut_ip_exit_2_state_untouched(sub, monkeypatch, tmp_path):
    before = m.load_state(tmp_path, C1)
    monkeypatch.setattr(m, "verify_image", lambda *a: pytest.fail("called"))
    assert m.main(_vi_args(tmp_path)) == 2
    assert m.load_state(tmp_path, C1) == before


def test_fetch_records_submission(sub, monkeypatch, tmp_path):
    _seed(tmp_path, dut_ip="1.1.1.1")
    seen = []
    monkeypatch.setattr(m, "fetch_submission", lambda ip, cid, d: seen.append((ip, cid, d)) or Path(d) / f"submission_{cid}.tar.xz")
    assert m.main(["fetch", "--batch", str(tmp_path), "--cid", C1]) == 0
    assert seen == [("1.1.1.1", C1, tmp_path / C1 / "artifacts")]
    assert m.load_state(tmp_path, C1)["submission"] == str(tmp_path / C1 / "artifacts" / f"submission_{C1}.tar.xz")


def test_fetch_without_dut_ip_exit_2(sub, tmp_path):
    assert m.main(["fetch", "--batch", str(tmp_path), "--cid", C1]) == 2


STALE = dict(dut_ip="6.6.6.6", image_ok=True, dcd="old-20240101-1", submission="/old.tar.xz")


def _assert_cleared(tmp_path, *fields):
    s = m.load_state(tmp_path, C1)
    for f in fields:
        assert f not in s, f


def test_wait_ended_clears_stale(sub, monkeypatch, tmp_path):
    _seed(tmp_path, job_id="j", job_held=True, **STALE)
    monkeypatch.setattr(m, "wait_for_reserve", lambda *a, **k: "complete")
    monkeypatch.setattr(m, "tf_artifacts", lambda j, d: None)
    assert m.main(_wait_args(tmp_path)) == 1
    _assert_cleared(tmp_path, "dut_ip", "image_ok", "dcd", "submission")


def test_wait_no_ip_clears_stale(sub, monkeypatch, tmp_path):
    _seed(tmp_path, job_id="j", job_held=True, **STALE)
    monkeypatch.setattr(m, "wait_for_reserve", lambda *a, **k: "reserve")
    monkeypatch.setattr(m, "tf_dut_ip", lambda j: None)
    monkeypatch.setattr(m, "_sleep", lambda s: None)
    assert m.main(_wait_args(tmp_path)) == 1
    _assert_cleared(tmp_path, "dut_ip", "image_ok", "dcd", "submission")


def test_verify_image_error_clears_stale(sub, monkeypatch, tmp_path):
    _seed(tmp_path, **STALE)

    def boom(ip, iso):
        raise RuntimeError("ssh")

    monkeypatch.setattr(m, "verify_image", boom)
    assert m.main(_vi_args(tmp_path)) == 1
    _assert_cleared(tmp_path, "image_ok", "dcd")
    assert m.load_state(tmp_path, C1)["dut_ip"] == "6.6.6.6"


def test_fetch_error_clears_stale(sub, monkeypatch, tmp_path):
    _seed(tmp_path, **STALE)

    def boom(ip, cid, d):
        raise RuntimeError("scp")

    monkeypatch.setattr(m, "fetch_submission", boom)
    assert m.main(["fetch", "--batch", str(tmp_path), "--cid", C1]) == 1
    _assert_cleared(tmp_path, "submission")


import fcntl  # noqa: E402
import multiprocessing  # noqa: E402
import time  # noqa: E402


def test_rerun_passes_force_and_timeouts(sub, monkeypatch, tmp_path):
    _seed(tmp_path, dut_ip="1.2.3.4", submission=str(tmp_path / C1 / "artifacts" / f"submission_{C1}.tar.xz"),
          rerun_tarball="/old", jira_exit=0)
    seen = []
    monkeypatch.setattr(m, "_run_logged", lambda argv, log, timeout: seen.append((argv, log, timeout)) or 0)
    assert m.main(["rerun", "--batch", str(tmp_path), "--cid", C1]) == 0
    argv, log, timeout = seen[0]
    out = tmp_path / C1 / f"submission_{C1}_rerun.tar.xz"
    assert argv == [sys.executable, str(m.RERUN), str(tmp_path / C1 / "artifacts" / f"submission_{C1}.tar.xz"),
                    "--host", "1.2.3.4", "--output", str(out), "--force"]
    assert timeout == 6000 and Path(log) == tmp_path / C1 / "rerun.log"
    s = m.load_state(tmp_path, C1)
    assert s["rerun_tarball"] == str(out) and "jira_exit" not in s


def test_rerun_nonzero_fails_and_clears(sub, monkeypatch, tmp_path):
    _seed(tmp_path, dut_ip="1.2.3.4", submission="/x/submission_a.tar.xz", rerun_tarball="/old", jira_exit=0)
    monkeypatch.setattr(m, "_run_logged", lambda *a: 3)
    assert m.main(["rerun", "--batch", str(tmp_path), "--cid", C1]) == 1
    s = m.load_state(tmp_path, C1)
    assert s["status"] == "failed" and s["error"] == f"rerun exited 3; see {tmp_path / C1 / 'rerun.log'}"
    assert "rerun_tarball" not in s and "jira_exit" not in s


def test_rerun_requires_submission(sub, tmp_path):
    _seed(tmp_path, dut_ip="1.2.3.4")
    assert m.main(["rerun", "--batch", str(tmp_path), "--cid", C1]) == 2


def _jira_seed(tmp_path):
    tb = tmp_path / C1 / f"submission_{C1}_rerun.tar.xz"
    tb.parent.mkdir(parents=True, exist_ok=True)
    tb.write_text("x")
    _seed(tmp_path, rerun_tarball=str(tb), jira_exit=9, stage="rerun")
    return tb


def _jira_args(tmp_path, *extra):
    return ["jira", "--batch", str(tmp_path), "--cid", C1, *extra]


def test_jira_receives_batch_args_verbatim(sub, monkeypatch, tmp_path):
    tb = _jira_seed(tmp_path)
    b = m.load_batch(tmp_path)
    seen = []
    monkeypatch.setattr(m, "_run_logged", lambda argv, log, timeout: seen.append((argv, log, timeout)) or 0)
    monkeypatch.setattr(m, "_sleep", lambda s: None)
    assert m.main(_jira_args(tmp_path)) == 0
    argv, log, timeout = seen[0]
    assert argv == [sys.executable, str(m.FILE_BUGS), str(tb),
                    "-p", b["p"], "-m", b["m"], "-a", b["a"], "-l", b["l"]]
    assert timeout == 1800 and Path(log) == tmp_path / C1 / "jira.log"
    s = m.load_state(tmp_path, C1)
    assert s["status"] == "done" and s["stage"] == "done" and s["jira_exit"] == 0


def test_jira_nonzero_marks_failed(sub, monkeypatch, tmp_path):
    _jira_seed(tmp_path)
    monkeypatch.setattr(m, "_run_logged", lambda *a: 4)
    monkeypatch.setattr(m, "_sleep", lambda s: None)
    assert m.main(_jira_args(tmp_path)) == 1
    s = m.load_state(tmp_path, C1)
    assert s["status"] == "failed" and s["jira_exit"] == 4
    assert s["error"] == f"file_bugs exited 4; see {tmp_path / C1 / 'jira.log'}"


def test_jira_requires_existing_tarball(sub, tmp_path):
    assert m.main(_jira_args(tmp_path)) == 2
    _seed(tmp_path, rerun_tarball=str(tmp_path / "nope.tar.xz"))
    assert m.main(_jira_args(tmp_path)) == 2


def test_jira_pause_called_inside_lock(sub, monkeypatch, tmp_path):
    _jira_seed(tmp_path)
    monkeypatch.setattr(m, "_run_logged", lambda *a: 0)
    held = []

    def probe(s):
        with open(tmp_path / "jira.lock", "w") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except OSError:
                held.append(True)

    monkeypatch.setattr(m, "_sleep", probe)
    assert m.main(_jira_args(tmp_path)) == 0
    assert held == [True]
    with open(tmp_path / "jira.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _hold_lock(path, ready, hold, released):
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        ready.set()
        time.sleep(hold)
        released.value = time.time()
    # lock released on close


def test_jira_lock_cross_process(sub, monkeypatch, tmp_path):
    _jira_seed(tmp_path)
    ready = multiprocessing.Event()
    released = multiprocessing.Value("d", 0.0)
    p = multiprocessing.Process(target=_hold_lock, args=(str(tmp_path / "jira.lock"), ready, 1.0, released))
    p.start()
    try:
        assert ready.wait(10)
        started = []
        monkeypatch.setattr(m, "_run_logged", lambda *a: started.append(time.time()) or 0)
        monkeypatch.setattr(m, "_sleep", lambda s: time.sleep(min(s, 0.1)))
        assert m.main(_jira_args(tmp_path)) == 0
    finally:
        p.join(10)
    assert released.value > 0 and len(started) == 1
    assert started[0] >= released.value


def test_jira_lock_timeout(sub, monkeypatch, tmp_path):
    _jira_seed(tmp_path)
    monkeypatch.setattr(m, "_run_logged", lambda *a: pytest.fail("called"))
    monkeypatch.setattr(m, "_sleep", lambda s: None)
    with open(tmp_path / "jira.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        assert m.main(_jira_args(tmp_path, "--lock-timeout", "0")) == 1
    s = m.load_state(tmp_path, C1)
    assert s["error"] == "Jira lock not acquired after 0s" and s["status"] == "failed"
    assert "jira_exit" not in s


def test_jira_timeout_expired_releases_lock(sub, monkeypatch, tmp_path):
    tb = _jira_seed(tmp_path)

    def boom(argv, log, timeout):
        raise subprocess.TimeoutExpired(argv, timeout)

    keep = []
    orig = m._acquire_jira_lock
    monkeypatch.setattr(m, "_acquire_jira_lock", lambda *a: keep.append(orig(*a)) or keep[-1])
    monkeypatch.setattr(m, "_run_logged", boom)
    monkeypatch.setattr(m, "_sleep", lambda s: None)
    assert m.main(_jira_args(tmp_path)) == 1
    assert keep and keep[0].closed
    s = m.load_state(tmp_path, C1)
    assert s["status"] == "failed" and s["error"]
    assert s["rerun_tarball"] == str(tb)
    with open(tmp_path / "jira.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _failed_seed(tmp_path, **kw):
    _seed(tmp_path, status="failed", error="old", stage="stale", **kw)


def _assert_running(tmp_path, stage):
    s = m.load_state(tmp_path, C1)
    assert s["status"] == "running" and "error" not in s and s["stage"] == stage


def test_submit_success_resets_failed(sub, tmp_path):
    _failed_seed(tmp_path)
    assert m.main(_sub_args(tmp_path)) == 0
    _assert_running(tmp_path, "submitted")


def test_wait_success_resets_failed(sub, monkeypatch, tmp_path):
    _failed_seed(tmp_path, job_id="j", job_held=True)
    monkeypatch.setattr(m, "wait_for_reserve", lambda *a, **k: "reserve")
    monkeypatch.setattr(m, "tf_dut_ip", lambda j: "10.0.0.5")
    assert m.main(_wait_args(tmp_path)) == 0
    _assert_running(tmp_path, "reserved")


def test_verify_image_success_resets_failed(sub, monkeypatch, tmp_path):
    _failed_seed(tmp_path, dut_ip="1.1.1.1")
    monkeypatch.setattr(m, "verify_image", lambda ip, iso: (True, "x"))
    assert m.main(_vi_args(tmp_path)) == 0
    _assert_running(tmp_path, "verify-image")


def test_fetch_success_resets_failed(sub, monkeypatch, tmp_path):
    _failed_seed(tmp_path, dut_ip="1.1.1.1")
    monkeypatch.setattr(m, "fetch_submission", lambda ip, cid, d: Path(d) / "s.tar.xz")
    assert m.main(["fetch", "--batch", str(tmp_path), "--cid", C1]) == 0
    _assert_running(tmp_path, "fetch")


def test_rerun_success_resets_failed(sub, monkeypatch, tmp_path):
    _failed_seed(tmp_path, dut_ip="1.2.3.4", submission="/x/submission_a.tar.xz")
    monkeypatch.setattr(m, "_run_logged", lambda *a: 0)
    assert m.main(["rerun", "--batch", str(tmp_path), "--cid", C1]) == 0
    _assert_running(tmp_path, "rerun")


def test_jira_success_ends_done(sub, monkeypatch, tmp_path):
    _jira_seed(tmp_path)
    _seed(tmp_path, status="failed", error="old")
    monkeypatch.setattr(m, "_run_logged", lambda *a: 0)
    monkeypatch.setattr(m, "_sleep", lambda s: None)
    assert m.main(_jira_args(tmp_path)) == 0
    s = m.load_state(tmp_path, C1)
    assert s["status"] == "done" and s["stage"] == "done" and "error" not in s


def test_dry_run_held_without_force_ok_and_untouched(sub, tmp_path):
    _seed(tmp_path, job_id="old", job_held=True, status="running", stage="reserved")
    before = m.load_state(tmp_path, C1)
    assert m.main(_sub_args(tmp_path, "--dry-run")) == 0
    assert m.load_state(tmp_path, C1) == before
    assert ("cancel", "old") not in sub


def _rel(tmp_path):
    return ["release", "--batch", str(tmp_path), "--cid", C1]


def test_release_cancel_fails_status_running(sub, monkeypatch, tmp_path):
    _seed(tmp_path, job_id="j", job_held=True)
    monkeypatch.setattr(m, "tf_cancel", lambda j: False)
    monkeypatch.setattr(m, "tf_status", lambda j: "running")
    assert m.main(_rel(tmp_path)) == 1
    s = m.load_state(tmp_path, C1)
    assert s["job_held"] is True and "cancel failed for job j; DUT may still be reserved" in s["error"]


def test_release_cancel_fails_status_complete(sub, monkeypatch, tmp_path):
    _seed(tmp_path, job_id="j", job_held=True)
    monkeypatch.setattr(m, "tf_cancel", lambda j: False)
    monkeypatch.setattr(m, "tf_status", lambda j: "complete")
    assert m.main(_rel(tmp_path)) == 0
    assert m.load_state(tmp_path, C1)["job_held"] is False


def test_release_cancel_fails_status_raises(sub, monkeypatch, tmp_path):
    _seed(tmp_path, job_id="j", job_held=True)
    monkeypatch.setattr(m, "tf_cancel", lambda j: False)
    monkeypatch.setattr(m, "tf_status", lambda j: (_ for _ in ()).throw(RuntimeError("x")))
    assert m.main(_rel(tmp_path)) == 1
    assert m.load_state(tmp_path, C1)["job_held"] is True


def test_release_does_not_change_status_stage(sub, tmp_path):
    _seed(tmp_path, job_id="j", job_held=True, status="running", stage="rerun")
    assert m.main(_rel(tmp_path)) == 0
    s = m.load_state(tmp_path, C1)
    assert s["status"] == "running" and s["stage"] == "rerun"


def test_submit_force_cancel_fails_warns_and_continues(sub, monkeypatch, tmp_path, capsys):
    _seed(tmp_path, job_id="old", job_held=True)
    monkeypatch.setattr(m, "tf_cancel", lambda j: False)
    assert m.main(_sub_args(tmp_path, "--force")) == 0
    assert "cancel of job old failed" in capsys.readouterr().err
    assert m.load_state(tmp_path, C1)["job_id"] == "job-new"


def test_tf_cancel_returns_bool(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("", 0))
    assert m.tf_cancel(UUID) is True
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _cp("", 1))
    assert m.tf_cancel(UUID) is False


def test_verify_image_ssh_failure_message(sub, monkeypatch, tmp_path):
    _seed(tmp_path, dut_ip="1.1.1.1")
    monkeypatch.setattr(m, "verify_image", lambda ip, iso: (False, ""))
    assert m.main(_vi_args(tmp_path)) == 1
    assert m.load_state(tmp_path, C1)["error"] == "could not read DCD over SSH from 1.1.1.1"


def test_verify_image_no_build_id_message(sub, monkeypatch, tmp_path):
    _seed(tmp_path, dut_ip="1.1.1.1")
    b = m.load_batch(tmp_path)
    b["iso"] = "http://x/noble-nobuild.iso"
    m.save_batch(tmp_path, b)
    monkeypatch.setattr(m, "verify_image", lambda ip, iso: (False, "other-20250101-9"))
    assert m.main(_vi_args(tmp_path)) == 1
    assert m.load_state(tmp_path, C1)["error"] == "ISO name has no build id"


def _write_creds(path, mode=0o600):
    path.write_text("TESTFLINGER_CLIENT_ID=id1\nTESTFLINGER_SECRET_KEY=sek\n")
    path.chmod(mode)


def test_tf_passes_creds_env(monkeypatch, tmp_path):
    f = tmp_path / "tf.ini"
    _write_creds(f)
    monkeypatch.setattr(m, "DEFAULT_TF_SECRETS", str(f))
    seen = {}

    def fake(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return _cp("x")

    monkeypatch.setattr(subprocess, "run", fake)
    m._tf(["status", UUID])
    assert seen["kw"]["env"]["TESTFLINGER_SECRET_KEY"] == "sek"
    assert "sek" not in " ".join(seen["argv"])


def test_tf_no_creds_env_none(monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run",
                        lambda a, **kw: seen.update(kw) or _cp("x"))
    m._tf(["status", UUID])
    assert seen.get("env") is None


def test_login_check_creds_rejected(monkeypatch, tmp_path):
    f = tmp_path / "tf.ini"
    _write_creds(f)
    monkeypatch.setattr(m, "DEFAULT_TF_SECRETS", str(f))
    _run(monkeypatch, "Authentication failed", rc=1)
    with pytest.raises(m.PreflightError) as e:
        m.check_testflinger_login()
    assert "rejected credentials" in str(e.value)
    assert "testflinger login" not in str(e.value)
    assert "sek" not in str(e.value)


def test_tf_secrets_warning_loose_perms(monkeypatch, tmp_path):
    f = tmp_path / "tf.ini"
    _write_creds(f, 0o664)
    monkeypatch.setattr(m, "DEFAULT_TF_SECRETS", str(f))
    assert "chmod 600" in m.tf_secrets_warning()
    f.chmod(0o600)
    assert m.tf_secrets_warning() is None


def test_login_check_creds_timeout_not_blamed_on_file(monkeypatch, tmp_path):
    f = tmp_path / "tf.ini"
    _write_creds(f)
    monkeypatch.setattr(m, "DEFAULT_TF_SECRETS", str(f))
    _run(monkeypatch, exc=subprocess.TimeoutExpired("testflinger", 60))
    with pytest.raises(m.PreflightError, match="timed out"):
        m.check_testflinger_login()


def test_tf_creds_undecodable_file_none(tmp_path):
    f = tmp_path / "tf.ini"
    f.write_bytes(b"\xff\xfe\x00bad")
    assert m.load_tf_credentials(str(f)) is None


def test_preflight_summary_reports_auth_no_secret(monkeypatch, tmp_path):
    f = tmp_path / "tf.ini"
    _write_creds(f, 0o664)
    monkeypatch.setattr(m, "DEFAULT_TF_SECRETS", str(f))
    monkeypatch.setattr(m, "check_testflinger_login", lambda: None)
    monkeypatch.setattr(m, "check_jira_args", lambda *a: None)
    monkeypatch.setattr(m, "preflight_checks", lambda *a: None)
    args = argparse.Namespace(
        p="P", m="fc", l="L", cids=["202401-12345"], test_plan="plan",
        iso="x", repo="r", form_factor="laptop", manifest=None, no_fetch=True,
        batch=str(tmp_path / "b"), lp_user="u", a="a")
    out = m.cmd_preflight(args)
    assert out["tf_auth"] == "credentials file"
    assert "chmod 600" in out["warnings"][0]
    assert "sek" not in json.dumps(out)
