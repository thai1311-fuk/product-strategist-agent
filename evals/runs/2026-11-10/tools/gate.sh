#!/bin/bash
# usage: gate.sh <repo> <slug> <gate> [--value X]
B=<временная-папка>
cd $B/$1 || exit 1
SA_ACTOR=evaluator python3 scripts/sa.py gate $2 $3 ${@:4}
echo "$(date -u +%FT%TZ) repo=$1 slug=$2 gate=$3 ${@:4} actor=evaluator (решение оценщика без чтения, по протоколу бенчмарка)" >> $B/gate_journal.txt
