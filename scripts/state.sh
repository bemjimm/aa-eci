#!/usr/bin/env bash
set -euo pipefail
repo=$(git rev-parse --show-toplevel)
cd "$repo"
case "${1:-}" in
  restore)
    ref=$(git ls-remote --heads origin pipeline-state)
    if [[ -n "$ref" ]]; then
      git fetch --depth=1 origin pipeline-state:refs/remotes/origin/pipeline-state
      git show refs/remotes/origin/pipeline-state:state.sqlite > state.sqlite
    fi
    ;;
  save)
    test -s "$repo/state.sqlite"
    state_dir=$(mktemp -d)
    if git show-ref --verify --quiet refs/remotes/origin/pipeline-state; then
      git worktree add --detach "$state_dir" refs/remotes/origin/pipeline-state
    else
      git worktree add --detach "$state_dir" HEAD
      (cd "$state_dir" && git checkout --orphan pipeline-state && git rm -rf --ignore-unmatch .)
    fi
    cp "$repo/state.sqlite" "$state_dir/state.sqlite"
    git -C "$state_dir" config user.name 'github-actions[bot]'
    git -C "$state_dir" config user.email '41898282+github-actions[bot]@users.noreply.github.com'
    git -C "$state_dir" add state.sqlite
    if ! git -C "$state_dir" diff --cached --quiet; then
      git -C "$state_dir" commit -m "Update model registry $(date -u +%F)"
      commit=$(git -C "$state_dir" rev-parse HEAD)
      git push origin "$commit:refs/heads/pipeline-state"
    fi
    git worktree remove "$state_dir"
    ;;
  *) echo 'Usage: bash scripts/state.sh restore|save' >&2; exit 2 ;;
esac
