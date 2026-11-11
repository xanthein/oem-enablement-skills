import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import image_lookup as m  # noqa: E402

V = "stella-noble-hwe-proposed"
B = V + "-20261111-1000"

LISTING = (
    '<a href="/share/x/">P</a><a href="?C=N;O=D">N</a>'
    '<a href="stella-noble-hwe-proposed-20261109-1229/">a</a>'
    '<a href="stella-noble-hwe-proposed-20261111-999/">b</a>'
    '<a href="stella-noble-hwe-proposed-20261111-1000/">c</a>'
    '<a href="foo.txt">f</a><a href="http://canonical.com">x</a>'
)


def test_parse_dirs():
    assert m.parse_dirs(LISTING) == [
        "stella-noble-hwe-proposed-20261109-1229",
        "stella-noble-hwe-proposed-20261111-999",
        "stella-noble-hwe-proposed-20261111-1000",
    ]


def test_latest_build_numeric():
    assert m.latest_build(V, m.parse_dirs(LISTING)) == B


def test_latest_build_prefix_variant_not_confused():
    assert m.latest_build("stella-noble-hwe", m.parse_dirs(LISTING)) is None


def test_iso_url():
    assert m.iso_url("v", "v-20261111-1") == m.BASE_URL + "v/v-20261111-1/v-20261111-1.iso"


def test_cache_status():
    assert m.cache_status({"X-Cache-Status": "hit"}) == "HIT"
    assert m.cache_status({}) is None


def make_fakes(heads, base_listing="alloem-noble-hwe/"):
    pages = {m.BASE_URL + V + "/": LISTING, m.BASE_URL: '<a href="%s">x</a>' % base_listing}

    def fetch(url):
        if url not in pages:
            raise m.NotFound(url)
        return pages[url]

    queue = list(heads)

    def head(url):
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    clock = {"t": 0.0}
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock["t"] += s

    return dict(fetch=fetch, head=head, sleep=sleep, now=lambda: clock["t"], log=lambda *a: None), sleeps


def test_waits_through_404_miss_and_error_then_hit(capsys):
    fakes, sleeps = make_fakes([
        m.NotFound("iso"), OSError("boom"),
        {"x-cache-status": "MISS"}, {"x-cache-status": "HIT"},
    ])
    rc = m.lookup(V, timeout_s=3600, interval_s=30, **fakes)
    assert rc == 0
    assert capsys.readouterr().out.strip() == m.iso_url(V, B)
    assert sleeps == [30, 30, 30]


def test_timeout_returns_1(capsys):
    fakes, _ = make_fakes([{"x-cache-status": "MISS"}])
    assert m.lookup(V, timeout_s=90, interval_s=30, **fakes) == 1
    assert capsys.readouterr().out == ""


def test_unknown_variant_returns_2_and_lists_variants(capsys):
    fakes, _ = make_fakes([{}])
    messages = []
    fakes["log"] = messages.append
    assert m.lookup("nope", timeout_s=90, interval_s=30, **fakes) == 2
    assert "alloem-noble-hwe" in " ".join(messages)


def test_no_builds_returns_2():
    fakes, _ = make_fakes([{}])
    base = m.BASE_URL + "other/"
    original = fakes["fetch"]
    fakes["fetch"] = lambda url: "<a href='x.txt'>x</a>" if url == base else original(url)
    assert m.lookup("other", timeout_s=90, interval_s=30, **fakes) == 2


def test_no_wait_prints_url_immediately(capsys):
    fakes, sleeps = make_fakes([{"x-cache-status": "MISS"}])
    assert m.lookup(V, timeout_s=90, interval_s=30, wait=False, **fakes) == 0
    assert capsys.readouterr().out.strip() == m.iso_url(V, B)
    assert sleeps == []


def test_main_parses_minutes(monkeypatch):
    captured = {}
    monkeypatch.setattr(m, "lookup", lambda v, **kw: captured.update(kw, variant=v) or 0)
    assert m.main(["v", "--timeout", "2", "--interval", "5"]) == 0
    assert captured["timeout_s"] == 120 and captured["interval_s"] == 5
