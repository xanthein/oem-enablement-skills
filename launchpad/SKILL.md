---
name: launchpad
description: Use lp-api to read, search, and update Launchpad bugs, tasks, comments, people, projects, packages, and PPAs for OEM enablement.
---

# Launchpad via lp-api

Use the installed `lp-api` CLI for Launchpad interactions. It is an OAuth-signed
HTTPie wrapper supplied by oem-scripts. Use `jq` to select useful JSON fields.
Read `references/workflows.md` for bug triage, comments, SRU tasks, and package
queries. Discover other operations in the current API reference.

## Sources

- [Launchpad web service guide](https://ubuntu.com/docs/launchpad/user/explanation/launchpad-api/launchpad-web-service/)
- [Current API reference](https://launchpad.net/+apidoc/devel.html)
- Local CLI implementation: resolve with `command -v lp-api` (currently
  `/usr/bin/lp-api`). Its help only documents a small part of the interface.

The web service guide is a migrated document with historical HTTP, staging, and
1.0 examples. Use HTTPS and the actual installed CLI behavior. Relative resource
paths default to the production `devel` API. For another version, pass its full
API URL; `1.0/bugs/123` would incorrectly become `devel/1.0/bugs/123`.

## Setup and authentication

```sh
command -v lp-api
command -v http
command -v jq
set -o pipefail
lp-api get people/+me --body --pretty=none --timeout=30 \
  | jq '{name, display_name, self_link}'
```

If missing, `lp-api` comes from `oem-scripts`; `http` comes from `httpie`.
Use the configured package sources rather than guessing an installer URL.

The wrapper uses `LAUNCHPAD_TOKEN` if supplied, otherwise the oem-scripts config
at `~/.config/oem-scripts/config.ini`. Reuse existing authentication. Do not read
or print credential values, enable shell tracing, or run `lp-api get_token`
(it prints secrets). Do not use verbose/request-printing HTTPie options that
would expose the OAuth Authorization header.

Without credentials, a normal request starts a browser-authorization flow and
waits. Have the user run `lp-api get people/+me` in their terminal and authorize
it, then retry. Do not delete the shared config automatically on a 401. Check
the response and account first; credential recovery belongs to the user.

## Request syntax

```sh
# An entry; use the global bug ID from any Launchpad bug URL.
lp-api get bugs/2166829 --body --pretty=none

# Named read operation: query parameters use ==.
lp-api get ubuntu/+source/libfprint \
  ws.op==searchTasks search_text==synaptic ws.size==20 --body --pretty=none

# Full API URLs returned by Launchpad are accepted unchanged.
lp-api get 'https://api.launchpad.net/devel/bugs/2166829' --body --pretty=none
```

| Request | CLI shape | Encoding |
| --- | --- | --- |
| Read entry/collection | `lp-api get RESOURCE` | GET |
| Named read | `lp-api get RESOURCE ws.op==OP arg==VALUE` | Query string |
| Update fields | `lp-api patch RESOURCE field=VALUE` | JSON body |
| Named write | `lp-api post RESOURCE ws.op=OP arg=VALUE` | Form body |
| Download hosted file | `lp-api download API_FILE_URL` | GET with redirects |

- `=` is a string body value, `:=` is a typed JSON body value for PATCH,
  `==` is a query parameter, and `Header:Value` is a header.
- Quote arguments containing spaces, `&`, or JSON. For PATCH arrays, booleans,
  and null use, for example, `'tags:=["oem-priority"]'` or `assignee_link:=null`.
  For structured named-operation parameters, follow that operation's documented
  encoding; POST is form-encoded, not a JSON body.
- `--body --pretty=none` gives JSON suitable for `jq`. Use `--print=hb` when
  response headers (ETag or Location) are needed; it prints only the response.
- The wrapper uses `--ignore-stdin`. Pass data as arguments; do not pipe a JSON
  request into it. HTTPie's `content=@/path/comment.txt` reads a text field from
  a file; `field@/path/file` is a file upload with different semantics.
- GET, PATCH, POST, and download are implemented. Do not invent `lp-api put`
  or `lp-api delete`; identify a supported named operation or explain the gap.
- Normalize `https://bugs.launchpad.net/.../+bug/ID` to `bugs/ID` for the core
  bug. The wrapper does not recognize the `bugs.launchpad.net` host as an API
  URL. Obtain task URLs from the bug's task collection.

## Resource discovery and pagination

1. GET the entry and retain its `self_link` as its canonical API identity.
2. Follow `*_link` for entries and `*_collection_link` for collections.
3. Use the fragment in `resource_type_link`, such as `#bug`, to find the type
   in `https://launchpad.net/+apidoc/devel.html#bug`. Check writable fields,
   operation names, required arguments, and allowed enum values there.
4. If needed, request the machine-readable WADL at the API root with
   `lp-api get devel/ Accept:application/vnd.sun.wadl+xml --body`.
   Reuse this large schema rather than repeatedly fetching it.
5. Collections contain `entries` and may have `next_collection_link`. Follow
   that URL unchanged until absent/null, or until the user's requested limit
   is reached. `ws.size` controls page size, not the total number of results.
   `total_size` may be absent; never depend on it to terminate pagination.

Use scoped searches (project, package, person, archive) instead of fetching
large global collections. Report when results are intentionally limited.
For a known bug number, fetch `bugs/ID` directly instead of text-searching it.

## Updating and verifying

Carry out the user's requested changes on the identified resource. Read the
current state first, preserve unrelated fields, and PATCH only the fields that
need changing. A bug's title, description, and tags are on the core bug; status,
importance, assignee, and milestone are on individual bug tasks for a project,
package, or Ubuntu series.

For read-modify-write updates, pass the entry's `http_etag` (or response ETag)
unchanged in `If-Match` to detect concurrent edits. On 412, fetch the new state
and reconcile before retrying. Collection links are not writable fields.
An array PATCH replaces the entire array, so merge existing tags before writing.

Named writes use POST with `ws.op` in the form body. A successful write may
return an empty body or `201 Created` with a `Location` header. Fetch the changed
resource or the returned Location to verify the result. If a POST times out,
check whether it succeeded before retrying to avoid duplicate comments/items.

For errors, inspect both HTTP status and response body: 400 usually means a
field, value, or encoding problem; 401/403 requires checking authentication or
access; 404 may mean a wrong URL or an inaccessible resource. The wrapper's
generic authentication-failure message alone does not establish a token problem.
Use bounded retries for transient errors and honor Retry-After when supplied.

## Hosted files

Follow the attachment entry's file link. `lp-api download` follows redirects
and writes the URL basename in the working directory. Use a dedicated directory
under `/tmp/opencode` and check for filename collisions first. Pass the API
hosted-file URL, not an arbitrary external or Librarian URL: the wrapper rewrites
unrecognized URLs as relative API paths. Download public external URLs with a
normal HTTP client rather than passing Launchpad credentials to them.
