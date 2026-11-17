import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import clone_config as cc  # noqa: E402


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


@pytest.fixture
def repo(tmp_path):
    origin = str(tmp_path / 'origin.git')
    work = str(tmp_path / 'work')
    sh(tmp_path, 'git', 'init', '-q', '--bare', '-b', 'main', origin)
    sh(tmp_path, 'git', 'clone', '-q', origin, work)
    sh(work, 'git', 'config', 'user.email', 't@t')
    sh(work, 'git', 'config', 'user.name', 't')
    sh(work, 'git', 'config', 'commit.gpgsign', 'false')
    write(work, 'pc/202510-37994/checkbox.conf', '[environment]\nA = 1\n')
    write(work, 'pc/202510-37994/manifest.json', '{"a": true}\n')
    sh(work, 'git', 'add', '.')
    sh(work, 'git', 'commit', '-q', '-m', 'init')
    sh(work, 'git', 'push', '-q', 'origin', 'main')
    sh(work, 'git', 'switch', '-q', '-c', '202509-11111')
    write(work, 'pc/202509-11111/manifest.json', '{"b": true}\n')
    sh(work, 'git', 'add', '.')
    sh(work, 'git', 'commit', '-q', '-m', 'b')
    sh(work, 'git', 'push', '-q', 'origin', '202509-11111')
    sh(work, 'git', 'switch', '-q', 'main')
    sh(work, 'git', 'fetch', '-q', 'origin')
    return work


def test_donor_files_falls_back_to_cid_branch(repo):
    assert cc.donor_files(repo, '202509-11111') == ('origin/202509-11111', ['manifest.json'])


def test_donor_files_prefers_main_over_cid_branch(repo):
    sh(repo, 'git', 'push', '-q', 'origin', 'main:202510-37994')
    sh(repo, 'git', 'fetch', '-q', 'origin')
    assert cc.donor_files(repo, '202510-37994') == ('origin/main', ['checkbox.conf', 'manifest.json'])


def test_donor_files_falls_back_to_main(repo):
    assert cc.donor_files(repo, '202510-37994') == ('origin/main', ['checkbox.conf', 'manifest.json'])


def test_donor_files_none(repo):
    assert cc.donor_files(repo, '202512-99999') is None


def test_find_donors_skips_new_cid_and_malformed(repo):
    machines = [
        {'canonical_id': '202611-39791', 'holder': {'name': 'dirksu'}},
        {'canonical_id': '202510-37994', 'holder': {'name': 'kevinyeh'}},
        {'canonical_id': 'bogus', 'holder': {'name': 'x'}},
        {'canonical_id': '202512-99999', 'holder': {'name': 'y'}},
    ]
    donors = cc.find_donors(repo, '202611-39791', machines)
    assert donors == [{'cid': '202510-37994', 'holder': 'kevinyeh', 'ref': 'origin/main',
                       'files': ['checkbox.conf', 'manifest.json']}]


def test_pick_single():
    d = {'cid': 'a'}
    assert cc.pick_donor([d], None) is d


def test_pick_multiple_exits_2():
    ds = [{'cid': '202510-37994', 'holder': 'k', 'ref': 'origin/main', 'files': ['manifest.json']},
          {'cid': '202509-11111', 'holder': 'j', 'ref': 'origin/202509-11111', 'files': ['manifest.json']}]
    with pytest.raises(cc.CloneError) as e:
        cc.pick_donor(ds, None)
    assert e.value.code == 2
    assert '202510-37994' in str(e.value) and '202509-11111' in str(e.value)


def test_pick_from_selects():
    ds = [{'cid': 'a'}, {'cid': 'b'}]
    assert cc.pick_donor(ds, 'b') == {'cid': 'b'}


def test_pick_from_not_donor_raises():
    with pytest.raises(cc.CloneError) as e:
        cc.pick_donor([{'cid': 'a'}], 'zzz')
    assert e.value.code == 1


def test_pick_zero_raises():
    with pytest.raises(cc.CloneError) as e:
        cc.pick_donor([], None)
    assert e.value.code == 1


NEW = '202611-39791'


def fake_c3(machines, label='RAURU-MV-SKU1'):
    def call(*args):
        if args[0] == 'show':
            return {'canonical_id': args[1], 'canonical_label': label}
        return machines
    return call


def m(cid, holder='k'):
    return {'canonical_id': cid, 'holder': {'name': holder}}


def run_main(repo, machines, *extra, cid=NEW):
    return cc.main([cid, '--repo', repo, *extra], c3_call=fake_c3(machines))


def test_main_clones_and_commits(repo, capsys):
    rc = run_main(repo, [m(NEW, 'd'), m('202510-37994')])
    assert rc == 0
    assert git_out(repo, 'branch', '--show-current') == NEW
    assert open(f'{repo}/pc/{NEW}/checkbox.conf').read() == '[environment]\nA = 1\n'
    assert open(f'{repo}/pc/{NEW}/manifest.json').read() == '{"a": true}\n'
    assert git_out(repo, 'log', '-1', '--format=%s') == (
        f'Add config for {NEW} (cloned from 202510-37994 origin/main, label RAURU-MV-SKU1)')
    assert 'Created branch' in capsys.readouterr().out


def git_out(repo, *args):
    return subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True).stdout.strip()


def test_main_multiple_exit_2_no_branch(repo, capsys):
    rc = run_main(repo, [m('202510-37994'), m('202509-11111')])
    assert rc == 2
    assert NEW not in git_out(repo, 'branch')
    assert '202509-11111' in capsys.readouterr().err


def test_main_from_selects(repo):
    rc = run_main(repo, [m('202510-37994'), m('202509-11111')], '--from', '202509-11111')
    assert rc == 0
    assert not os.path.exists(f'{repo}/pc/{NEW}/checkbox.conf')
    assert open(f'{repo}/pc/{NEW}/manifest.json').read() == '{"b": true}\n'


def test_main_from_not_donor_exit_1(repo):
    assert run_main(repo, [m('202510-37994')], '--from', '202512-99999') == 1
    assert NEW not in git_out(repo, 'branch')


def test_main_dirty_worktree_refuses(repo):
    write(repo, 'pc/202510-37994/manifest.json', 'dirty\n')
    assert run_main(repo, [m('202510-37994')]) == 1
    assert NEW not in git_out(repo, 'branch')


def test_main_branch_exists_refuses(repo):
    sh(repo, 'git', 'branch', NEW)
    assert run_main(repo, [m('202510-37994')]) == 1


def test_main_remote_branch_exists_refuses(repo):
    sh(repo, 'git', 'push', '-q', 'origin', f'main:{NEW}')
    assert run_main(repo, [m('202510-37994')]) == 1
    assert git_out(repo, 'branch', '--show-current') == 'main'


def test_main_target_dir_exists_on_main_refuses(repo):
    assert run_main(repo, [m('202510-37994'), m('202509-11111')], '--from', '202509-11111',
                    cid='202510-37994') == 1


def test_main_no_donor_exit_1(repo):
    assert run_main(repo, [m(NEW, 'd')]) == 1


def test_main_bad_cid_exit_1(repo):
    assert run_main(repo, [], cid='nope') == 1
