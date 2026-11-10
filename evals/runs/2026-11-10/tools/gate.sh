#!/bin/bash
# usage: gate.sh <repo> <slug> <gate> [--value X]
B=/private/tmp/claude-501/-Users-thai/fa2237a0-5433-4537-b2e8-397673a95c10/scratchpad/bench-iso
cd $B/$1 || exit 1
SA_ACTOR=evaluator python3 scripts/sa.py gate $2 $3 ${@:4}
echo "$(date -u +%FT%TZ) repo=$1 slug=$2 gate=$3 ${@:4} actor=evaluator (решение оценщика без чтения, по протоколу бенчмарка)" >> $B/gate_journal.txt
