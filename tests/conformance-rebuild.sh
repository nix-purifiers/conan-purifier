#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
system=$(nix eval --impure --raw --expr builtins.currentSystem)
for check in environment project debug release; do
  nix build ".#checks.$system.$check" --no-link
  nix build ".#checks.$system.$check" --no-link --rebuild
 done
