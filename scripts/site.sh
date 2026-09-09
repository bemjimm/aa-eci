#!/usr/bin/env bash
set -euo pipefail
repo=$(git rev-parse --show-toplevel)
cd "$repo"
branch=site-cache
case "${1:-}" in
  restore)
    ref=$(git ls-remote --heads origin "$branch")
    if [[ -n "$ref" ]]; then
      git fetch --depth=1 origin "$branch:refs/remotes/origin/$branch"
      git show "refs/remotes/origin/$branch:site.tar.gz" | tar -xzf - -C "$repo"
    fi
    ;;
  save)
    test -s "$repo/dist/index.html"
    state_dir=$(mktemp -d)
    if git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
      git worktree add --detach "$state_dir" "refs/remotes/origin/$branch"
    else
      git worktree add --detach "$state_dir" HEAD
      (cd "$state_dir" && git checkout --orphan "$branch" && git rm -rf --ignore-unmatch .)
    fi
    tar -czf "$state_dir/site.tar.gz" -C "$repo" dist
    git -C "$state_dir" config user.name 'github-actions[bot]'
    git -C "$state_dir" config user.email '41898282+github-actions[bot]@users.noreply.github.com'
    git -C "$state_dir" add site.tar.gz
    if ! git -C "$state_dir" diff --cached --quiet; then
      git -C "$state_dir" commit -m "Update published site $(date -u +%FT%TZ)"
      commit=$(git -C "$state_dir" rev-parse HEAD)
      git push origin "$commit:refs/heads/$branch"
    fi
    git worktree remove "$state_dir"
    ;;
  *) echo 'Usage: bash scripts/site.sh restore|save' >&2; exit 2 ;;
esac
