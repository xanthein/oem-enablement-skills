---
name: image-lookup
description: Use when you need the URL of the latest OEM PC image ISO for a variant, waiting until it is cached on tel-image-cache.
---

# image-lookup

Resolves the latest build of a PC image variant under
`https://tel-image-cache.canonical.com/oem-share/lyoncore/github-artifacts/oem-pc-image-builder/`,
waits until the ISO is cached (`x-cache-status: HIT`), then prints the ISO URL.

The GitHub image-building action warms the cache; this script only polls (HEAD requests, never downloads the ISO).

## Usage

```bash
python3 <skill-dir>/scripts/image_lookup.py <variant> [--timeout MIN] [--interval SEC] [--no-wait]
# e.g. image_lookup.py stella-noble-hwe-proposed
```

- stdout: only the ISO URL. Progress goes to stderr.
- `--timeout` minutes (default 60), `--interval` seconds (default 30).
- `--no-wait`: print the URL as soon as the ISO exists, whatever the cache status.
- Latest = highest `<YYYYMMDD>-<build#>` directory, compared numerically. It is pinned for the whole wait.
- A build directory can exist before its ISO is uploaded; the script keeps waiting.

Exit codes: 0 cached (or `--no-wait` with ISO present), 1 timeout or ISO missing with `--no-wait`, 2 unknown variant or no builds (available variants are listed on stderr).

The wait can take up to an hour: run it in the background and use the printed URL when it finishes.
