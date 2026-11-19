#!/usr/bin/env python3
import argparse
import re
import sys
import time
import urllib.error
import urllib.request

HOST = "https://tel-image-cache.canonical.com/oem-share/"
BASE_URL = HOST + "lyoncore/github-artifacts/oem-pc-image-builder/"
EXACT_RE = re.compile(r"^(?:canonical-oem-)?(?P<variant>.+?)-(?P<build>\d{8}-\d+)$")


class NotFound(Exception):
    pass


def parse_dirs(html):
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
    return [
        h[:-1]
        for h in hrefs
        if h.endswith("/") and not h.startswith(("/", "?", "http"))
    ]


def latest_build(variant, names):
    pattern = re.compile(r"^%s-(\d{8})-(\d+)$" % re.escape(variant))
    found = []
    for name in names:
        match = pattern.match(name)
        if match:
            found.append(((int(match.group(1)), int(match.group(2))), name))
    return max(found)[1] if found else None


def iso_url(variant, build):
    return "%s%s/%s/%s.iso" % (BASE_URL, variant, build, build)


def parse_target(text):
    """Return (variant, build|None, iso_urls|None) from a variant, DCD, or dir URL."""
    text = text.strip()
    if text.startswith("http"):
        parts = text.rstrip("/").split("/")
        build_dir = parts[-1]
        variant = parts[-2]
        iso = "%s/%s-%s.iso" % (text.rstrip("/"), variant, build_dir)
        return variant, "%s-%s" % (variant, build_dir), [iso]
    match = EXACT_RE.match(text)
    if not match:
        return text, None, None
    variant, num = match.group("variant"), match.group("build")
    build = "%s-%s" % (variant, num)
    urls = []
    tokens = variant.split("-")
    if len(tokens) >= 3:
        urls.append("%s%s/releases/%s/%s/%s/%s.iso" % (HOST, tokens[0], tokens[1], variant, num, build))
    urls.append(iso_url(variant, build))
    return variant, build, urls


def cache_status(headers):
    for key, value in headers.items():
        if key.lower() == "x-cache-status":
            return value.strip().upper()
    return None


def _open(url, method):
    request = urllib.request.Request(url, method=method)
    try:
        return urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotFound(url) from exc
        raise OSError("HTTP %s for %s" % (exc.code, url)) from exc
    except urllib.error.URLError as exc:
        raise OSError(str(exc)) from exc


def fetch(url):
    with _open(url, "GET") as response:
        return response.read().decode("utf-8", "replace")


def head(url):
    with _open(url, "HEAD") as response:
        return dict(response.headers.items())


def _stderr(*args):
    print(*args, file=sys.stderr, flush=True)


def lookup(variant, *, timeout_s, interval_s, wait=True, fetch=fetch, head=head,
           sleep=time.sleep, now=time.monotonic, log=_stderr):
    variant, build, urls = parse_target(variant)
    if urls:
        return _wait(urls, build, timeout_s, interval_s, wait, head, sleep, now, log)
    try:
        listing = fetch(BASE_URL + variant + "/")
    except NotFound:
        variants = ", ".join(parse_dirs(fetch(BASE_URL)))
        log("unknown variant %r; available: %s" % (variant, variants))
        return 2
    build = latest_build(variant, parse_dirs(listing))
    if build is None:
        log("no builds found for %s" % variant)
        return 2
    log("latest build: %s" % build)
    return _wait([iso_url(variant, build)], build, timeout_s, interval_s, wait, head, sleep, now, log)


def _wait(urls, build, timeout_s, interval_s, wait, head, sleep, now, log):
    log("build: %s" % build)
    start = now()
    while True:
        status = None
        url = urls[0]
        for candidate in urls:
            try:
                status = cache_status(head(candidate))
                url = candidate
                log("cache status: %s (%s)" % (status, candidate))
                break
            except NotFound:
                log("ISO not found at %s" % candidate)
            except OSError as exc:
                log("request failed: %s" % exc)
        if status == "HIT" or (not wait and status is not None):
            print(url)
            return 0
        if not wait:
            log("ISO missing")
            return 1
        if now() - start >= timeout_s:
            log("timed out waiting for cache HIT")
            return 1
        sleep(interval_s)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Latest cached ISO URL for a PC image variant")
    parser.add_argument("variant", help="variant (latest build), or exact build: DCD, <variant>-<date>-<build>, or release dir URL")
    parser.add_argument("--timeout", type=float, default=60, help="minutes (default 60)")
    parser.add_argument("--interval", type=float, default=30, help="seconds (default 30)")
    parser.add_argument("--no-wait", action="store_true")
    args = parser.parse_args(argv)
    return lookup(
        args.variant,
        timeout_s=args.timeout * 60,
        interval_s=args.interval,
        wait=not args.no_wait,
    )


if __name__ == "__main__":
    sys.exit(main())
