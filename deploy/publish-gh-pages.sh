#!/usr/bin/env bash
# Talk2Code 宣传主页发布脚本：把 site/ 同步到 gh-pages 分支并推送。
#
# 为什么需要它
# ------------
# GitHub Pages 的来源是 **gh-pages 分支**（Deploy from a branch，非 Actions 构建），
# 所以 main 上改 site/ 不会自动生效，必须同步到 gh-pages 才会发布。
# 推送后 GitHub 会自动触发 `pages build and deployment`，约 1 分钟生效。
#
# 用法
# ----
#   bash deploy/publish-gh-pages.sh              # 同步 + 提交 + 推送（发布）
#   bash deploy/publish-gh-pages.sh --dry-run    # 只同步到临时工作区并显示差异，不提交不推送
#   bash deploy/publish-gh-pages.sh --no-push    # 同步 + 提交到本地 gh-pages，但不推送
#
# 说明：deploy/ 在 .gitignore 里，本脚本不会进入公开仓库。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE_DIR="$REPO_ROOT/site"
BRANCH=gh-pages
REMOTE=origin
PAGES_URL="https://wowcoder.github.io/talk2code/"

MODE=publish
case "${1:-}" in
  --dry-run) MODE=dry-run ;;
  --no-push|-n) MODE=no-push ;;
  --help|-h) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
  "") ;;
  *) echo "✗ 未知参数: $1（可用 --dry-run / --no-push）" >&2; exit 2 ;;
esac

# ---- 前置检查 ----------------------------------------------------------
[ -d "$SITE_DIR" ] || { echo "✗ 找不到 $SITE_DIR" >&2; exit 1; }
[ -f "$SITE_DIR/index.html" ] || { echo "✗ site/index.html 不存在" >&2; exit 1; }

# 发布源必须与 main 一致：避免把未提交的站点改动悄悄发出去
cd "$REPO_ROOT"
if [ "$MODE" = publish ] && [ -n "$(git status --porcelain -- "$SITE_DIR")" ]; then
  echo "⚠️  site/ 有未提交的改动："
  git status --short -- "$SITE_DIR"
  echo "    建议先 commit 到 main，再发布（否则线上内容与仓库对不上）。"
  read -r -p "    仍要继续发布? [y/N] " ans </dev/tty || ans=n
  [ "$ans" = y ] || [ "$ans" = Y ] || { echo "已取消。"; exit 1; }
fi

WORKTREE="$(mktemp -d "${TMPDIR:-/tmp}/t2c-gh-pages.XXXXXX")"
cleanup() { git worktree remove --force "$WORKTREE" >/dev/null 2>&1 || rm -rf "$WORKTREE"; }
trap cleanup EXIT

echo "==> [1/5] 拉取远端 $BRANCH"
git fetch --quiet "$REMOTE" "$BRANCH"

echo "==> [2/5] 在临时工作区检出 $BRANCH"
git worktree add --force -B "$BRANCH" "$WORKTREE" "$REMOTE/$BRANCH" >/dev/null

echo "==> [3/5] 同步 site/ → ${BRANCH}（镜像同步，会删除多余文件）"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete --exclude '.git' "$SITE_DIR/" "$WORKTREE/"
else
  find "$WORKTREE" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  cp -R "$SITE_DIR/." "$WORKTREE/"
fi

cd "$WORKTREE"
git add -A

echo "==> [4/5] 变更对比（远端 $BRANCH → 本次）"
if git diff --cached --quiet; then
  echo "    （无差异：远端 $BRANCH 已与 site/ 一致，无需发布）"
  exit 0
fi
git --no-pager diff --cached --stat
echo "    --- 关键文件校验（本地 vs 待发布）---"
for f in index.html .nojekyll; do
  [ -f "$f" ] || { echo "    ✗ 缺少 $f"; exit 1; }
done
for f in index.html assets/architecture.svg; do
  a=$(md5 -q "$SITE_DIR/$f" 2>/dev/null || md5sum "$SITE_DIR/$f" | cut -d' ' -f1)
  b=$(md5 -q "$WORKTREE/$f" 2>/dev/null || md5sum "$WORKTREE/$f" | cut -d' ' -f1)
  [ "$a" = "$b" ] && echo "    ✓ $f 一致" || { echo "    ✗ $f 不一致，中止"; exit 1; }
done

if [ "$MODE" = dry-run ]; then
  echo "==> [5/5] --dry-run：不提交、不推送。"
  echo "    差异见上；去掉 --dry-run 即执行发布。"
  exit 0
fi

echo "==> [5/5] 提交并推送"
git commit --quiet -m "site: 发布 Talk2Code 宣传主页到 GitHub Pages（$(date '+%Y-%m-%d %H:%M')）"
echo "    提交: $(git log -1 --format=%h) $(git log -1 --format=%s)"

if [ "$MODE" = no-push ]; then
  echo "    --no-push：已在本地 $BRANCH 提交，未推送。"
  echo "    手动推送: git push $REMOTE $BRANCH"
  exit 0
fi

git push --quiet "$REMOTE" "HEAD:$BRANCH"
echo "    ✓ 已推送到 $REMOTE/$BRANCH"

echo
echo "==> 发布完成。"
echo "    线上地址: $PAGES_URL"
echo "    生效时间: GitHub 会自动触发 pages build and deployment，通常 1 分钟内。"
echo "    若仍是旧页面，先强制刷新（Cmd+Shift+R）——CDN 可能有缓存。"
echo "    校验线上版本: curl -s $PAGES_URL | grep -c '<你预期的特征串>'"
