#!/usr/bin/env bash
set -e

source ~/.bashrc

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
win_script="$(cygpath -w "$script_dir/run.ps1")"

runps "& '$win_script'"
