#!/bin/bash
# One calibration run (10_CALIBRATION.md): NAME SEED_SUFFIX SEED ARGS...   e.g. calib_run.sh DF s6 20261026 --deviation 0.05 --pull-ahead 0.35
# Run from this folder with the app root's virtualenv active; outputs go to common.SCRATCH/out (edit common.py for your machine).
cd "$(dirname "$0")"
name=$1; sfx=$2; seed=$3; shift 3
out=$(python -c "from common import SCRATCH; print(SCRATCH / 'out')")
mkdir -p "$out"
python offplan_paired.py 10 5,10 12 "C_${name}_${sfx}" "$@" --seed "$seed" --late-share 0.02 --late-max 3 --lead 20 > "$out/C_${name}_${sfx}.log" 2>&1
echo "C_${name}_${sfx} exit=$? $(date -u +%H:%M:%S)" >> "$out/CALIB.log"
