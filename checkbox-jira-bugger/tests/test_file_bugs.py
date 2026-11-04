import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import file_bugs  # noqa: E402

RERUN = "submission_202607-39079_517476_rerun.tar.xz"
ORIG = "submission_202607-39079_517476.tar.xz"

CONFIG = {
    "OEM_X86_JIRA_CONF": {
        "somerville": {
            "jira_project": "CASC",
            "jira_components": [
                {"component_name": "jellyfish-gogogo", "jira_enable_platform_card": "CASC-1"}
            ],
        }
    }
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    files = {}
    for name in ("jira_secrets.ini", "c3_config.ini"):
        p = tmp_path / name
        p.write_text("x")
        files[name] = str(p)
    cfg = tmp_path / "jira_project_config.json"
    cfg.write_text(json.dumps(CONFIG))
    tar = tmp_path / RERUN
    tar.write_text("x")
    orig = tmp_path / ORIG
    orig.write_text("x")
    return {
        "tarball": str(tar),
        "orig": str(orig),
        "jira_secrets": files["jira_secrets.ini"],
        "c3_config": files["c3_config.ini"],
        "jira_config": str(cfg),
    }


def pf(env, **over):
    kw = dict(
        tarball=env["tarball"], project="somerville", label="jellyfish-gogogo",
        milestone="fc", jira_secrets=env["jira_secrets"], c3_config=env["c3_config"],
        jira_config=env["jira_config"], allow_original=False,
    )
    kw.update(over)
    return file_bugs.preflight(**kw)


def test_cid_from_tarball():
    assert file_bugs.cid_from_tarball("/x/" + RERUN) == "202607-39079"
    assert file_bugs.cid_from_tarball("foo.tar.xz") is None


def test_is_rerun_tarball():
    assert file_bugs.is_rerun_tarball(RERUN)
    assert not file_bugs.is_rerun_tarball(ORIG)


def test_preflight_ok(env):
    assert pf(env) == "FC"


def test_preflight_rejects_original(env):
    with pytest.raises(file_bugs.PreflightError):
        pf(env, tarball=env["orig"])


def test_preflight_allow_original(env):
    assert pf(env, tarball=env["orig"], allow_original=True) == "FC"


@pytest.mark.parametrize("key", ["tarball", "jira_secrets", "c3_config", "jira_config"])
def test_preflight_missing_file(env, key):
    with pytest.raises(file_bugs.PreflightError) as e:
        pf(env, **{key: env[key] + ".missing"})
    assert ".missing" in str(e.value)


def test_preflight_unknown_project(env):
    with pytest.raises(file_bugs.PreflightError):
        pf(env, project="nope")


def test_preflight_unknown_label(env):
    with pytest.raises(file_bugs.PreflightError) as e:
        pf(env, label="other")
    assert "jellyfish-gogogo" in str(e.value)


def test_preflight_label_case_mismatch(env):
    with pytest.raises(file_bugs.PreflightError):
        pf(env, label="Jellyfish-Gogogo")


def test_preflight_bad_milestone(env):
    with pytest.raises(file_bugs.PreflightError):
        pf(env, milestone="XX")


def test_preflight_cli_missing(env, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(file_bugs.PreflightError):
        pf(env)


def test_build_cmd_dryrun():
    tb = "/a b/" + RERUN
    cmd = file_bugs.build_cmd(tb, "somerville", "jellyfish-gogogo", "FC", "a@b.c",
                              "202607-39079", "s", "c3", "cfg", True)
    assert cmd[0] == "checkbox-jira-bugger"
    assert "--dryrun" in cmd
    assert "--rerun-on-fail" not in cmd and "-i" not in cmd
    assert cmd[-1] == tb
    assert file_bugs.build_cmd(tb, "p", "l", "FC", "a", "c", "s", "c3", "cfg", False).count("--dryrun") == 0


def test_output_has_error():
    assert file_bugs.output_has_error("JIRA:  ERROR: 2026: boom")
    assert file_bugs.output_has_error("Traceback (most recent call last):\n x")
    assert not file_bugs.output_has_error("JIRA:   INFO: 2026: fine")


class Runner:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        rc, out, err = self.results.pop(0)
        return subprocess.CompletedProcess(cmd, rc, out, err)


def run_main(env, monkeypatch, runner, extra=(), tarball=None, drop=None):
    monkeypatch.setattr(subprocess, "run", runner)
    args = [tarball or env["tarball"], "-p", "somerville", "-m", "FC",
            "-a", "a@b.c", "-l", "jellyfish-gogogo",
            "--jira-secrets", env["jira_secrets"], "--c3-config", env["c3_config"],
            "--jira-config", env["jira_config"], *extra]
    if drop:
        i = args.index(drop)
        del args[i:i + 2]
    return file_bugs.main(args)


OK = (0, "JIRA: INFO: ok\n", "")


def test_main_happy(env, monkeypatch):
    r = Runner(OK, (0, "", ""))
    assert run_main(env, monkeypatch, r) == 0
    assert len(r.calls) == 2
    assert "--dryrun" in r.calls[0] and "--dryrun" not in r.calls[1]


def test_main_dryrun_nonzero(env, monkeypatch):
    r = Runner((1, "", "bad"))
    assert run_main(env, monkeypatch, r) == 4
    assert len(r.calls) == 1


def test_main_dryrun_error_line(env, monkeypatch):
    r = Runner((0, "", "JIRA:  ERROR: 2026: x"))
    assert run_main(env, monkeypatch, r) == 4
    assert len(r.calls) == 1


def test_main_real_fails(env, monkeypatch):
    r = Runner(OK, (1, "", ""))
    assert run_main(env, monkeypatch, r) == 5


def test_main_preflight_fail(env, monkeypatch):
    r = Runner()
    assert run_main(env, monkeypatch, r, tarball=env["orig"]) == 3
    assert r.calls == []


def test_main_no_cid(env, monkeypatch, tmp_path):
    odd = tmp_path / "foo_rerun.tar.xz"
    odd.write_text("x")
    r = Runner()
    assert run_main(env, monkeypatch, r, tarball=str(odd)) == 2
    assert r.calls == []


def test_main_cid_override(env, monkeypatch, tmp_path):
    odd = tmp_path / "foo_rerun.tar.xz"
    odd.write_text("x")
    r = Runner(OK, OK)
    assert run_main(env, monkeypatch, r, extra=["--cid", "202401-12345"], tarball=str(odd)) == 0
    assert "202401-12345" in r.calls[0]


def test_main_missing_required_arg(env, monkeypatch):
    with pytest.raises(SystemExit) as e:
        run_main(env, monkeypatch, Runner(), drop="-a")
    assert e.value.code == 2


def test_main_real_error_line_with_exit_zero(env, monkeypatch):
    r = Runner(OK, (0, "", "JIRA:  ERROR: 2026: could not create"))
    assert run_main(env, monkeypatch, r) == 5


def test_output_has_error_critical_and_prefix_only():
    assert file_bugs.output_has_error("JIRA:CRITICAL: 2026: x")
    assert not file_bugs.output_has_error("JIRA:   INFO: 2026: job ERROR: in name")
