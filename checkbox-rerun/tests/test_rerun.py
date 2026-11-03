import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import rerun  # noqa: E402


def test_select_default():
    results = [
        {"id": "wireless/a", "status": "fail"},
        {"id": "wireless/b", "status": "pass"},
        {"id": "after-suspend-wireless/c", "status": "skip"},
        {"id": "audio/x", "status": "fail"},
    ]
    assert [r["id"] for r in rerun.select_jobs(results, False)] == [
        "wireless/a",
        "after-suspend-wireless/c",
    ]
    assert [r["id"] for r in rerun.select_jobs(results, True)] == [
        "wireless/a",
        "wireless/b",
        "after-suspend-wireless/c",
    ]


def test_wifi_env():
    launcher = {
        "environment": {
            "WPA_BE_SSID": "s",
            "WPA_BE_PSK": "p",
            "OPEN_BE_SSID": "o",
            "BTDEVADDR": "aa",
        }
    }
    env = rerun.wifi_env(launcher, {"WPA_BE_PSK": "x"})
    assert "BTDEVADDR" not in env
    assert env["WPA_BE_PSK"] == "x"
    assert env["OPEN_BE_SSID"] == "o"


def test_build_command():
    assert rerun.build_command("wireless/w", {"OPEN_BE_SSID": "cert-be-open-tel-l10"}) == (
        "sudo OPEN_BE_SSID=cert-be-open-tel-l10 checkbox-cli run "
        "com.canonical.certification::device com.canonical.certification::wireless/w"
    )


NAME = "Connect to unencrypted 802.11be Wi-Fi network on wlp194s0"


def summary(*lines):
    return "Outcome: job passed\nFinalizing session\n" + "\n".join(lines) + "\n"


def test_parse_pass():
    out = summary("  job passed   : Resource job", f"  job passed   : {NAME}")
    assert rerun.parse_outcome(out, NAME) == "pass"


def test_parse_fail_despite_passing_dependency():
    out = summary(f"  job failed   : {NAME}", "  job passed   : Automated test of suspend function")
    assert rerun.parse_outcome(out, NAME) == "fail"


def test_parse_dependency_failed_target_absent_is_not_pass():
    out = summary("  job failed   : Other job", "  job passed   : Automated test of suspend function")
    assert rerun.parse_outcome(out, NAME) == "unknown"


def test_parse_cannot_be_started_is_skip():
    out = summary("  job passed   : Resource job", f"job cannot be started (unmet manifest): {NAME}")
    assert rerun.parse_outcome(out, NAME) == "skip"


def test_parse_name_must_match_exactly():
    out = summary(f"  job passed   : {NAME} after suspend (S3)")
    assert rerun.parse_outcome(out, NAME) == "unknown"


def test_parse_unknown():
    assert rerun.parse_outcome("Traceback", NAME) == "unknown"
    assert rerun.parse_outcome("Outcome: job" + " passed\n", NAME) == "unknown"


def test_apply_pass_after_suspend():
    res = {
        "id": "after-suspend-wireless/x",
        "status": "fail",
        "outcome": "fail",
        "comments": None,
        "io_log": "old",
    }
    out = rerun.apply_pass(res, "new", "10.0.0.5", "2026-11-03T00:00:00")
    assert out["status"] == "pass" and out["outcome"] == "pass"
    assert out["io_log"] == "new"
    assert "without suspend" not in out["comments"]
    assert out["comments"].startswith("Re-run on 10.0.0.5")
    assert out["rerun"]["original_status"] == "fail"
    assert out["rerun"]["host"] == "10.0.0.5"
    assert res["status"] == "fail"


import json  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

SAMPLE_DMI = [
    {
        "category": "SYSTEM",
        "system_serial": "0006770CYN",
        "system_uuid": "104e9ce9-405b-4f05-8c42-5b09401bf740",
    }
]


def test_cid_from_name():
    assert rerun.cid_from_submission("submission_202607-39079_517476.tar.xz", {}) == "202607-39079"
    launcher = {"transport:local_file": {"path": "/home/ubuntu/artifacts/submission_202601-11111.tar.xz"}}
    assert rerun.cid_from_submission("foo.tar.xz", launcher) == "202601-11111"
    assert rerun.cid_from_submission("foo.tar.xz", {}) is None


def test_expected_identity():
    assert rerun.expected_identity(SAMPLE_DMI) == {
        "serial": "0006770CYN",
        "uuid": "104e9ce9-405b-4f05-8c42-5b09401bf740",
    }


def test_verify_dut_mismatch():
    exp = rerun.expected_identity(SAMPLE_DMI)
    with pytest.raises(RuntimeError, match="0006770CYN"):
        rerun.verify_dut(lambda c: (0, "OTHER\n" + exp["uuid"] + "\n"), exp)


def test_verify_dut_match_case():
    exp = rerun.expected_identity(SAMPLE_DMI)
    out = "0006770cyn\n" + exp["uuid"].upper() + "\n"
    rerun.verify_dut(lambda c: (0, out), exp)


def test_resolve_host(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("print('10.0.0.5')")
    bad = tmp_path / "bad.py"
    bad.write_text("import sys; sys.exit(1)")
    import sys as _s

    assert rerun.resolve_host("202607-39079", _s.executable, str(ok), "s.ini") == "10.0.0.5"
    with pytest.raises(RuntimeError, match="C3 lookup failed"):
        rerun.resolve_host("202607-39079", _s.executable, str(bad), "s.ini")


def test_iface_missing_skips():
    calls = []
    jobs = [{"id": "wireless/wireless_scanning_wlp1s0", "name": "Scan on wlp1s0"}]
    rows = rerun.rerun_jobs(jobs, {}, lambda c: calls.append(c) or (0, ""), {"wlp2s0"})
    assert rows[0]["verdict"] == "skipped: interface missing"
    assert calls == []


def test_runner_timeout_is_error():
    def runner(cmd):
        if "wireless/a" in cmd:
            raise TimeoutError
        return 0, "  job passed   : N"

    rows = rerun.rerun_jobs([{"id": "wireless/a", "name": "N"}, {"id": "wireless/b", "name": "N"}], {}, runner, set())
    assert [r["verdict"] for r in rows] == ["error", "pass"]


def test_rerun_pass():
    rows = rerun.rerun_jobs(
        [{"id": "wireless/wireless_scanning_wlp1s0", "name": "N on wlp1s0"}], {}, lambda c: (0, "  job passed   : N on wlp1s0"), {"wlp1s0"}
    )
    assert rows[0]["verdict"] == "pass"
    assert rows[0]["output"] == "  job passed   : N on wlp1s0"


def test_dut_ifaces():
    out = "lo UNKNOWN\nwlp194s0 UP 1.2.3.4\nenx00 UP\n"
    assert rerun.dut_ifaces(lambda c: (0, out)) == {"lo", "wlp194s0", "enx00"}


import io  # noqa: E402
import tarfile  # noqa: E402

JUNIT = """<?xml version="1.0" encoding="UTF-8"?>
  <testsuites failures="2" name="" tests="3" skipped="0" errors="0">
    <testcase classname="com.canonical.certification::wireless/a" name="x" time="1"><failure type="">
      boom
      </failure>
    </testcase>
    <testcase classname="com.canonical.certification::wireless/b" name="x" time="1"><failure type="">
      boom2
      </failure>
    </testcase>
    <testcase classname="com.canonical.certification::wireless/c" name="x" time="1">
    </testcase>
  </testsuites>
"""


def test_patch_junit():
    out = rerun.patch_junit(JUNIT, {"com.canonical.certification::wireless/a"})
    assert 'failures="1"' in out
    assert out.count("<failure") == 1
    assert "boom2" in out and "boom\n" not in out
    assert out.count("<testcase ") == 3


def make_tar(path, names_prefix="/"):
    results = [
        {
            "id": "wireless/a",
            "name": "N",
            "full_id": "com.canonical.certification::wireless/a",
            "type": "test",
            "status": "fail",
            "outcome": "fail",
            "comments": None,
            "io_log": "old",
        },
        {
            "id": "wireless/ok",
            "name": "N",
            "full_id": "com.canonical.certification::wireless/ok",
            "type": "test",
            "status": "pass",
            "outcome": "pass",
            "comments": None,
            "io_log": "",
        },
    ]
    sub = {
        "results": results,
        "launcher": {"environment": {"WPA_BE_SSID": "s", "WPA_BE_PSK": "secretpsk", "BTDEVADDR": "z"}},
        "raw-devices-dmi": SAMPLE_DMI,
    }
    files = {
        "submission.json": json.dumps(sub).encode(),
        "submission.junit": JUNIT.encode(),
        "submission.html": b"<html/>",
        "test_output/com.canonical.certification__wireless_a": b"old",
    }
    with tarfile.open(path, "w:xz") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(names_prefix + name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return files


def names(path):
    with tarfile.open(path) as t:
        return sorted(m.name for m in t.getmembers())


def test_roundtrip_layout(tmp_path):
    src = tmp_path / "s_202607-39079_1.tar.xz"
    files = make_tar(src)
    d = rerun.extract(src, tmp_path / "w")
    out = tmp_path / "o.tar.xz"
    rerun.pack(d, out)
    assert names(out) == sorted(files)


def test_nothing_selected(tmp_path, capsys):
    src = tmp_path / "s_202607-39079_1.tar.xz"
    make_tar(src)
    code = rerun.main([str(src), "--host", "1.2.3.4", "--force-host", "--workdir", str(tmp_path / "w"), "--only", "zzz"])
    assert code == 0
    assert "nothing to re-run" in capsys.readouterr().out
    assert not (tmp_path / "s_202607-39079_1_rerun.tar.xz").exists()


def test_dry_run_does_not_touch_dut(tmp_path, capsys, monkeypatch):
    src = tmp_path / "s_202607-39079_1.tar.xz"
    make_tar(src)

    def boom(*a, **k):
        raise AssertionError("ssh used")

    monkeypatch.setattr(rerun, "ssh_run", boom)
    code = rerun.main([str(src), "--host", "1.2.3.4", "--dry-run", "--workdir", str(tmp_path / "w")])
    assert code == 0
    assert "checkbox-cli run" in capsys.readouterr().out


def test_main_no_dut(tmp_path):
    src = tmp_path / "plain.tar.xz"
    make_tar(src)
    assert rerun.main([str(src), "--workdir", str(tmp_path / "w")]) == 2


def test_main_end_to_end(tmp_path, monkeypatch):
    src = tmp_path / "s_202607-39079_1.tar.xz"
    make_tar(src)
    before = src.stat().st_mtime_ns

    def fake(host, user, pw, cmd, timeout):
        if "dmi" in cmd:
            return 0, "0006770CYN\n104e9ce9-405b-4f05-8c42-5b09401bf740\n"
        if "ip -br" in cmd:
            return 0, "lo UNKNOWN\n"
        return 0, "  job passed   : N\n"

    monkeypatch.setattr(rerun, "ssh_run", fake)
    code = rerun.main([str(src), "--host", "1.2.3.4", "--workdir", str(tmp_path / "w")])
    assert code == 0
    out = tmp_path / "s_202607-39079_1_rerun.tar.xz"
    assert src.stat().st_mtime_ns == before
    assert "RERUN.md" in names(out)
    with tarfile.open(out) as t:
        sub = json.load(t.extractfile("submission.json"))
        junit = t.extractfile("submission.junit").read().decode()
        to = t.extractfile("test_output/com.canonical.certification__wireless_a").read()
    a = sub["results"][0]
    assert a["status"] == "pass" and a["rerun"]["original_status"] == "fail"
    assert sub["results"][1]["status"] == "pass" and "rerun" not in sub["results"][1]
    assert 'failures="1"' in junit
    assert to == b"  job passed   : N\n"


def test_main_mismatch_aborts(tmp_path, monkeypatch):
    src = tmp_path / "s_202607-39079_1.tar.xz"
    make_tar(src)
    monkeypatch.setattr(rerun, "ssh_run", lambda *a: (0, "WRONG\nWRONG\n"))
    assert rerun.main([str(src), "--host", "1.2.3.4", "--workdir", str(tmp_path / "w")]) == 3
    assert not (tmp_path / "s_202607-39079_1_rerun.tar.xz").exists()


def test_verify_dut_ignores_ssh_warning():
    exp = rerun.expected_identity(SAMPLE_DMI)
    out = "Warning: Permanently added '1.2.3.4' (ED25519) to the list of known hosts.\n0006770CYN\n" + exp["uuid"] + "\n"
    rerun.verify_dut(lambda c: (0, out), exp)


def test_ssh_opts_quiet():
    assert "LogLevel=ERROR" in rerun.SSH_OPTS


# ---- review fixes ----
import os  # noqa: E402


def good_fake(host, user, pw, cmd, timeout):
    if "dmi" in cmd:
        return 0, "0006770CYN\n104e9ce9-405b-4f05-8c42-5b09401bf740\n"
    if "ip -br" in cmd:
        return 0, "lo UNKNOWN\n"
    return 0, "  job passed   : N\n"


def test_output_equal_source_refused(tmp_path, monkeypatch):
    src = tmp_path / "s_202607-39079_1.tar.xz"
    make_tar(src)
    before = src.read_bytes()
    monkeypatch.setattr(rerun, "ssh_run", good_fake)
    code = rerun.main([str(src), "--host", "h", "--workdir", str(tmp_path / "w"), "--output", str(src)])
    assert code == 4
    assert src.read_bytes() == before


def test_existing_output_needs_force(tmp_path, monkeypatch):
    src = tmp_path / "s_202607-39079_1.tar.xz"
    make_tar(src)
    out = tmp_path / "s_202607-39079_1_rerun.tar.xz"
    out.write_bytes(b"previous")
    monkeypatch.setattr(rerun, "ssh_run", good_fake)
    assert rerun.main([str(src), "--host", "h", "--workdir", str(tmp_path / "w")]) == 4
    assert out.read_bytes() == b"previous"
    assert rerun.main([str(src), "--host", "h", "--workdir", str(tmp_path / "w2"), "--force"]) == 0


def test_verify_dut_nothing_to_compare():
    with pytest.raises(RuntimeError, match="cannot verify"):
        rerun.verify_dut(lambda c: (0, "a\nb\n"), {"serial": None, "uuid": None})
    with pytest.raises(RuntimeError, match="cannot verify"):
        rerun.verify_dut(lambda c: (0, "a\nb\n"), {"serial": "To be filled by O.E.M.", "uuid": ""})


def test_placeholder_serial_ignored_uuid_still_checked():
    exp = {"serial": "Default string", "uuid": "abc"}
    rerun.verify_dut(lambda c: (0, "whatever\nABC\n"), exp)
    with pytest.raises(RuntimeError, match="uuid"):
        rerun.verify_dut(lambda c: (0, "whatever\nzzz\n"), exp)


def test_verify_dut_ssh_failure_is_unreachable():
    with pytest.raises(RuntimeError, match="unreachable"):
        rerun.verify_dut(lambda c: (255, "Permission denied"), {"serial": "S", "uuid": "U"})


def test_extract_cleans_stale_dest(tmp_path):
    src = tmp_path / "s.tar.xz"
    make_tar(src)
    dest = tmp_path / "w"
    dest.mkdir()
    (dest / "old.txt").write_text("stale")
    rerun.extract(src, dest)
    assert not (dest / "old.txt").exists()
    assert (dest / "submission.json").exists()


def test_roundtrip_preserves_mode_and_mtime(tmp_path):
    src = tmp_path / "s.tar.xz"
    with tarfile.open(src, "w:xz") as t:
        ti = tarfile.TarInfo("submission.json")
        ti.size = 2
        ti.mode = 0o600
        ti.mtime = 1_000_000
        t.addfile(ti, io.BytesIO(b"{}"))
    d = rerun.extract(src, tmp_path / "w")
    out = tmp_path / "o.tar.xz"
    rerun.pack(d, out)
    with tarfile.open(out) as t:
        m = t.getmember("submission.json")
    assert m.mode == 0o600 and m.mtime == 1_000_000


def test_extract_warns_on_dropped_members(tmp_path, capsys):
    src = tmp_path / "s.tar.xz"
    with tarfile.open(src, "w:xz") as t:
        ti = tarfile.TarInfo("link")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "x"
        t.addfile(ti)
    rerun.extract(src, tmp_path / "w")
    assert "link" in capsys.readouterr().err


def test_patch_junit_self_closing_case():
    xml = (
        '<testsuites failures="1" tests="2">\n'
        '<testcase classname="com.canonical.certification::wireless/skip" name="s" time="0"/>\n'
        '<testcase classname="com.canonical.certification::wireless/a" name="a" time="1"><failure type="">boom</failure>\n</testcase>\n'
        "</testsuites>\n"
    )
    out = rerun.patch_junit(xml, {"com.canonical.certification::wireless/a"})
    assert "<failure" not in out and 'failures="0"' in out
    assert 'wireless/skip' in out
    out2 = rerun.patch_junit(xml, {"com.canonical.certification::wireless/skip"})
    assert "boom" in out2 and 'failures="1"' in out2


def test_ssh_failure_rc255_is_error():
    rows = rerun.rerun_jobs([{"id": "wireless/a", "name": "N"}], {}, lambda c: (255, "Connection closed"), set())
    assert rows[0]["verdict"] == "error"


def test_ssh_run_uses_sshpass_env_not_argv(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["env"] = argv, kw.get("env", {})
        return subprocess.CompletedProcess(argv, 0, stdout="ok")

    monkeypatch.setattr(rerun.subprocess, "run", fake_run)
    rerun.ssh_run("h", "u", "topsecret", "cmd", 5)
    assert "topsecret" not in seen["argv"]
    assert "-e" in seen["argv"] and seen["env"]["SSHPASS"] == "topsecret"
    assert any("ServerAliveInterval" in a for a in seen["argv"])


def test_env_override_validated(tmp_path):
    src = tmp_path / "s_202607-39079_1.tar.xz"
    make_tar(src)
    with pytest.raises(SystemExit):
        rerun.main([str(src), "--host", "h", "--env", "FOO", "--workdir", str(tmp_path / "w")])
    with pytest.raises(SystemExit):
        rerun.main([str(src), "--host", "h", "--env", "BAD;rm=1", "--workdir", str(tmp_path / "w")])


def test_dry_run_masks_psk(tmp_path, capsys):
    src = tmp_path / "s_202607-39079_1.tar.xz"
    make_tar(src)
    rerun.main([str(src), "--host", "h", "--dry-run", "--workdir", str(tmp_path / "w")])
    out = capsys.readouterr().out
    assert "secretpsk" not in out and "WPA_BE_PSK=REDACTED" in out


def test_apply_pass_clears_skip_reason():
    res = {"id": "wireless/x", "status": "skip", "outcome": "skip", "comments": None, "io_log": "", "skip_reason": "manifest"}
    assert rerun.apply_pass(res, "o", "h", "t")["skip_reason"] is None


def test_iface_check_failure_does_not_skip():
    assert rerun.dut_ifaces(lambda c: (1, "boom")) is None
    rows = rerun.rerun_jobs(
        [{"id": "wireless/s_wlp1s0", "name": "S on wlp1s0"}], {}, lambda c: (0, "  job passed   : S on wlp1s0"), None
    )
    assert rows[0]["verdict"] == "pass"


def test_iface_only_when_name_mentions_it():
    calls = []
    rows = rerun.rerun_jobs(
        [{"id": "wireless/do_enable", "name": "Do enable"}], {}, lambda c: calls.append(c) or (0, "  job passed   : Do enable"), {"wlp2s0"}
    )
    assert rows[0]["verdict"] == "pass" and calls


def test_interrupt_saves_partial(tmp_path, monkeypatch):
    src = tmp_path / "s_202607-39079_1.tar.xz"
    make_tar(src)

    def fake(host, user, pw, cmd, timeout):
        if "checkbox-cli" in cmd:
            raise KeyboardInterrupt
        return good_fake(host, user, pw, cmd, timeout)

    monkeypatch.setattr(rerun, "ssh_run", fake)
    code = rerun.main([str(src), "--host", "h", "--workdir", str(tmp_path / "w")])
    assert code == 130
    assert (tmp_path / "s_202607-39079_1_rerun.tar.xz").exists()


def test_all_reports_unchanged_for_already_pass(tmp_path, monkeypatch):
    src = tmp_path / "s_202607-39079_1.tar.xz"
    make_tar(src)
    monkeypatch.setattr(
        rerun, "ssh_run", lambda h, u, p, cmd, t: good_fake(h, u, p, cmd, t)
    )
    rerun.main([str(src), "--host", "h", "--all", "--workdir", str(tmp_path / "w")])
    with tarfile.open(tmp_path / "s_202607-39079_1_rerun.tar.xz") as t:
        report = t.extractfile("RERUN.md").read().decode()
    assert "| wireless/ok | pass | pass | no (already pass) |" in report
