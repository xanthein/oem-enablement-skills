#!/usr/bin/env python3
import argparse
import re
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "https://tel-image-cache.canonical.com/oem-share/lyoncore/github-artifacts/oem-pc-image-builder/"


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
    url = iso_url(variant, build)
    log("latest build: %s" % build)
    start = now()
    while True:
        status = None
        try:
            status = cache_status(head(url))
            log("cache status: %s" % status)
        except NotFound:
            log("ISO not uploaded yet")
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
    parser.add_argument("variant")
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
