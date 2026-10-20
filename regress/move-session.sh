#!/bin/sh

set -eu

[ -z "${TEST_TMUX:-}" ] && TEST_TMUX=$(readlink -f ../tmux)
TMUX="$TEST_TMUX -f/dev/null -Lmove-session-$$"

cleanup()
{
	$TMUX kill-server 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

order()
{
	$TMUX list-sessions -F '#{session_name}:#{session_order}'
}

check()
{
	expected=$1
	actual=$(order)
	if [ "$actual" != "$expected" ]; then
		printf 'expected:\n%s\nactual:\n%s\n' "$expected" "$actual" >&2
		exit 1
	fi
}

zeta=$($TMUX new-session -d -s zeta -P -F '#{session_id}')
alpha=$($TMUX new-session -d -s alpha -P -F '#{session_id}')
middle=$($TMUX new-session -d -s middle -P -F '#{session_id}')

check "$(printf 'zeta:0\nalpha:1\nmiddle:2')"

$TMUX move-session -b -s "$middle" -t "$zeta"
check "$(printf 'middle:0\nzeta:1\nalpha:2')"

$TMUX move-session -a -s "$middle" -t "$alpha"
check "$(printf 'zeta:0\nalpha:1\nmiddle:2')"

$TMUX move-session -a -s "$zeta" -t "$middle"
check "$(printf 'alpha:0\nmiddle:1\nzeta:2')"

$TMUX move-session -b -s "$zeta" -t "$alpha"
check "$(printf 'zeta:0\nalpha:1\nmiddle:2')"

$TMUX move-session -b -s "$alpha" -t "$alpha"
check "$(printf 'zeta:0\nalpha:1\nmiddle:2')"

$TMUX rename-session -t "$zeta" omega
check "$(printf 'omega:0\nalpha:1\nmiddle:2')"

$TMUX kill-session -t "$alpha"
check "$(printf 'omega:0\nmiddle:1')"

$TMUX new-session -d -s beta
check "$(printf 'omega:0\nmiddle:1\nbeta:2')"

before=$(order)
if $TMUX move-session -b -s '$999999' -t "$middle" 2>/dev/null; then
	exit 1
fi
[ "$(order)" = "$before" ]
if $TMUX move-session -a -s "$middle" -t '$999999' 2>/dev/null; then
	exit 1
fi
[ "$(order)" = "$before" ]

[ "$($TMUX list-sessions -F '#{session_name}')" = "$(printf 'omega\nmiddle\nbeta')" ]
[ "$($TMUX list-sessions -O name -F '#{session_name}')" = "$(printf 'beta\nmiddle\nomega')" ]
