#!/bin/bash
# Thin wrapper kept for old habits: all logic now lives in run.py (cross-platform supervisor).
# Control files work as before:  touch .restart -> restart backend;  touch .reseed -> re-seed DB, then restart.
cd "$(dirname "$0")" || exit 1
exec python3 run.py --share "$@"
