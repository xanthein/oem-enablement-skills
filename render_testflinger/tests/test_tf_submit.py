import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tf_submit.py"
spec = importlib.util.spec_from_file_location("tf_submit", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

JOB_ID = "0f3c6a2e-1b2c-4d5e-8f90-123456789abc"


def test_load_credentials(tmp_path):
    f = tmp_path / "s.ini"
    f.write_text("# c\nexport TESTFLINGER_CLIENT_ID='id1'\nTESTFLINGER_SECRET_KEY=\"s=e\"\n")
    assert m.load_tf_credentials(f) == {
        "TESTFLINGER_CLIENT_ID": "id1", "TESTFLINGER_SECRET_KEY": "s=e"}


def test_load_credentials_incomplete_or_missing(tmp_path):
    f = tmp_path / "s.ini"
    f.write_text("TESTFLINGER_CLIENT_ID=id1\nTESTFLINGER_SECRET_KEY=\n")
    assert m.load_tf_credentials(f) is None
    assert m.load_tf_credentials(tmp_path / "nope") is None


def _fake_run(seen, stdout=JOB_ID + "\n", rc=0):
    def run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, rc, stdout, "")
    return run


def test_submit_uses_stdin_and_credentials(tmp_path, monkeypatch):
    sec = tmp_path / "s.ini"
    sec.write_text("TESTFLINGER_CLIENT_ID=id1\nTESTFLINGER_SECRET_KEY=sek\n")
    job = tmp_path / "job.yaml"
    job.write_text("job_queue: q\n")
    seen = {}
    monkeypatch.setattr(m.subprocess, "run", _fake_run(seen))
    assert m.submit(job, secrets=sec) == JOB_ID
    assert seen["cmd"] == ["testflinger", "submit", "--quiet", "-"]
    assert seen["kw"]["input"] == "job_queue: q\n"
    assert seen["kw"]["env"]["TESTFLINGER_SECRET_KEY"] == "sek"


def test_submit_without_credentials_uses_plain_env(tmp_path, monkeypatch):
    job = tmp_path / "job.yaml"
    job.write_text("x: 1\n")
    seen = {}
    monkeypatch.setattr(m.subprocess, "run", _fake_run(seen))
    m.submit(job, secrets=tmp_path / "nope")
    assert seen["kw"]["env"] is None


def test_submit_failure_does_not_leak_secret(tmp_path, monkeypatch):
    sec = tmp_path / "s.ini"
    sec.write_text("TESTFLINGER_CLIENT_ID=id1\nTESTFLINGER_SECRET_KEY=sek\n")
    job = tmp_path / "job.yaml"
    job.write_text("x: 1\n")
    monkeypatch.setattr(m.subprocess, "run", _fake_run({}, stdout="denied", rc=1))
    with pytest.raises(RuntimeError) as e:
        m.submit(job, secrets=sec)
    assert "sek" not in str(e.value)
