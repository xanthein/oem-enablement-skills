#!/bin/bash
# Tests for ssh/scripts/dut using fake sshpass/scp resolver.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
DUT="$HERE/../scripts/dut"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir "$T/bin"
cat > "$T/bin/sshpass" <<'F'
#!/bin/bash
printf '%s\n' "$*" >> "$FAKE_LOG"
exit "${FAKE_RC:-0}"
F
cat > "$T/resolver" <<'F'
#!/bin/bash
echo 10.9.9.9
F
chmod +x "$T/bin/sshpass" "$T/resolver"
export PATH="$T/bin:$PATH" FAKE_LOG="$T/log" DUT_C3="$T/resolver"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected [$2] got [$3]"; fail=1; fi; }
has() { if grep -q -- "$2" "$FAKE_LOG"; then echo "ok   $1"; else echo "FAIL $1: [$2] not in log:"; cat "$FAKE_LOG"; fail=1; fi; }

: > "$FAKE_LOG"; FAKE_RC=3 "$DUT" run 10.0.0.1 'echo "a|b"'; rc=$?
check "run propagates rc" 3 "$rc"
has "run target+cmd" 'ubuntu@10.0.0.1 echo "a|b"$'
has "run no known_hosts" 'UserKnownHostsFile=/dev/null'

: > "$FAKE_LOG"; "$DUT" run 202611-39773 true
has "run resolves CID" 'ubuntu@10.9.9.9 true$'

: > "$FAKE_LOG"; "$DUT" cp ./f 202611-39773:/tmp/
has "cp resolves remote" './f ubuntu@10.9.9.9:/tmp/$'

: > "$FAKE_LOG"; err=$(DUT_POLL=0 FAKE_RC=255 "$DUT" wait 10.0.0.1 0 2>&1 >/dev/null); rc=$?
check "wait timeout rc" 1 "$rc"
case "$err" in *timeout*) echo "ok   wait timeout msg";; *) echo "FAIL wait msg: $err"; fail=1;; esac

err=$("$DUT" 2>&1 >/dev/null); rc=$?
check "no args rc" 2 "$rc"
case "$err" in *sage*) echo "ok   usage";; *) echo "FAIL usage: $err"; fail=1;; esac
exit $fail
