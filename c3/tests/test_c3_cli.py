import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import c3_api  # noqa: E402


class Fake:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def c3_get(self, endpoint, **params):
        self.calls.append((endpoint, params))
        return self.data


def run(argv, data, secrets_sink=None):
    fake = Fake(data)

    def factory(path):
        if secrets_sink is not None:
            secrets_sink.append(path)
        return fake
    return c3_api.main(argv, api_factory=factory), fake


def test_search_uses_icontains(capsys):
    rc, fake = run(['search', 'mrnw', '--holder', 'dirksu'],
                   [{'canonical_id': '202601-1', 'canonical_label': 'MRNW-1', 'holder': {'name': 'dirksu'}}])
    assert fake.calls == [('machines/', {'canonical_label__icontains': 'mrnw', 'holder__name': 'dirksu'})]
    assert capsys.readouterr().out == '202601-1\tMRNW-1\tdirksu\n'
    assert rc == 0


def test_search_no_match_exits_1(capsys):
    rc, _ = run(['search', 'zzz'], [])
    assert rc == 1
    assert capsys.readouterr().out == ''


def test_holder_shapes():
    assert c3_api._holder_name({'holder': {'name': 'a'}}) == 'a'
    assert c3_api._holder_name({'holder': 'b'}) == 'b'
    assert c3_api._holder_name({}) == '-'


def test_show_prints_json(capsys):
    canned = {'canonical_id': '202601-1'}
    rc, fake = run(['show', '202601-1'], canned)
    assert fake.calls == [('machines/202601-1/', {})]
    assert json.loads(capsys.readouterr().out) == canned
    assert rc == 0


def test_submissions(capsys):
    rc, fake = run(['submissions', '202601-1'], [{'id': 1}])
    assert fake.calls == [('submissions/', {'canonical_id': '202601-1'})]
    assert json.loads(capsys.readouterr().out) == [{'id': 1}]
    assert rc == 0


def test_get_raw_params():
    rc, fake = run(['get', 'machines/', 'q=a=b', 'x=1'], [{}])
    assert fake.calls == [('machines/', {'q': 'a=b', 'x': '1'})]
    assert rc == 0


def test_get_none_exits_1():
    rc, _ = run(['get', 'machines/'], None)
    assert rc == 1


def test_default_secrets_path():
    sink = []
    run(['show', '1'], {}, sink)
    assert sink == [os.path.expanduser('~/.config/secrets/c3_secrets.ini')]


def test_log_levels():
    run(['show', '1'], {})
    assert logging.getLogger().level == logging.WARNING
    run(['-v', 'show', '1'], {})
    assert logging.getLogger().level == logging.DEBUG
