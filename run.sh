#!/usr/bin/env bash
# Development launcher: runs from the source tree using the local venv.
cd "$(dirname "$0")"
exec .venv/bin/python -m pikalicious.app "$@"
