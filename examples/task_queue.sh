#!/bin/sh
cd "$(dirname "$0")"

# NB: assume we're in ~/.local/share/ptaskrunner
export HOME="$(realpath ../../..)"

exec python ollama.py
