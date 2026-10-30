# Launchpad workflows

These are Bash/HTTPie examples. Replace the sample bug and variables with the
user's target. Keep `set -o pipefail` when piping requests through `jq` so HTTP
failures are not masked. Write examples are for requested changes, not probes.

## Read a bug and its release-specific tasks

```sh
set -o pipefail
bug_id=2166829
bug=$(lp-api get "bugs/$bug_id" --body --pretty=none --timeout=30) || exit
printf '%s\n' "$bug" | jq '{id, title, description, tags, web_link}'
tasks_url=$(printf '%s\n' "$bug" | jq -er '.bug_tasks_collection_link') || exit
lp-api get "$tasks_url" --body --pretty=none --timeout=30 \
  | jq '{next_collection_link, entries: [.entries[] |
      {self_link, bug_target_name, target_link, status, importance,
       assignee_link, milestone_link}]}'
```

Read comments through `messages_collection_link` and attachments through
`attachments_collection_link`. Follow pagination for each collection. Comments
and attachments can contain additional reproduction details and patch links.

For SRUs, distinguish the general Ubuntu source-package task from the tasks for
individual series. Derive target releases from those tasks and the user's
request; do not infer them solely from the current Git branch or description.

## Search and follow every page

```sh
set -o pipefail
page=$(lp-api get ubuntu/+source/libfprint \
  ws.op==searchTasks search_text==synaptic ws.size==50 \
  --body --pretty=none --timeout=30) || exit
while :; do
  printf '%s\n' "$page" | jq -c '.entries[] |
    {self_link, bug_link, status, importance, target_link}' || exit
  next=$(printf '%s\n' "$page" | jq -r '.next_collection_link // empty') || exit
  [ -n "$next" ] || break
  page=$(lp-api get "$next" --body --pretty=none --timeout=30) || exit
done
```

`searchTasks` returns bug tasks, not unique bugs. Deduplicate by `bug_link` if
the user wants a bug count. Check the API reference for status, assignee, tags,
and other filters, including defaults; a text search is not an exhaustive list
of every status. Use `bugs/ID` for a known bug number.

## Update one task

Set `task_url` to the exact `self_link` selected from the task collection.
Choose the requested project/package/series; ask if multiple targets fit and
the user's intended target cannot be inferred.

```sh
task=$(lp-api get "$task_url" --body --pretty=none --timeout=30) || exit
etag=$(printf '%s\n' "$task" | jq -er '.http_etag') || exit
lp-api patch "$task_url" status='In Progress' "If-Match:$etag" \
  --print=hb --pretty=none --timeout=30 || exit
lp-api get "$task_url" --body --pretty=none --timeout=30 \
  | jq '{self_link, status, importance, assignee_link}'
```

For assignment, GET `~USERNAME` and use its `self_link` as `assignee_link`.
Use `assignee_link:=null` only when clearing the assignment is intended.
Check valid statuses and importance values in the `#bug_task` API reference.

## Add a tag without removing existing tags

```sh
bug=$(lp-api get "bugs/$bug_id" --body --pretty=none --timeout=30) || exit
etag=$(printf '%s\n' "$bug" | jq -er '.http_etag') || exit
tags=$(printf '%s\n' "$bug" | jq -c '.tags + ["oem-priority"] | unique') || exit
lp-api patch "bugs/$bug_id" "tags:=$tags" "If-Match:$etag" \
  --print=hb --pretty=none --timeout=30 || exit
lp-api get "bugs/$bug_id" --body --pretty=none --timeout=30 | jq '{id, tags}'
```

If the tag is already present, skip the write. Use the same preservation rule
when adding release-specific verification tags.

## Add a comment

The current `#bug-newMessage` operation takes required `content` and optional
`subject` and `send_notifications` parameters. Example:

```sh
lp-api post "bugs/$bug_id" ws.op=newMessage \
  subject='Verification results' content=@/tmp/opencode/launchpad-comment.txt \
  --print=hb --pretty=none --timeout=30
```

Populate the file with the user's intended comment first. Read the response's
Location URL to verify the created message. If the result is uncertain, inspect
the bug's messages before retrying the POST.

## Packages, archives, and other resources

```sh
lp-api get ubuntu/+source/libfprint --body --pretty=none --timeout=30
lp-api get ubuntu/+archive/primary --body --pretty=none --timeout=30
lp-api get '~OWNER/+archive/ubuntu/PPA' --body --pretty=none --timeout=30
```

Use the resource's type reference to discover publication/build searches and
their arguments. Follow returned archive, source publication, build, and file
links. Do not assume a source publication means every architecture has finished
building. The same discovery process applies to people, teams, Git repositories,
and merge proposals; verify each resource's operations before invoking them.
