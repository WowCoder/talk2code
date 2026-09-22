#!/usr/bin/env bash
# 把 site/ 发布为 GitHub Pages（独立 gh-pages 分支，不污染 main）
#
# 用法:
#   bash deploy/publish-gh-pages.sh                 # 发布到 https://wowcoder.github.io/talk2code/
#   WITH_CNAME=1 bash deploy/publish-gh-pages.sh    # 额外绑定自定义域名 wowcoder.cn
#   加 --dry-run 只打包不提交不推送
#
# ── 首次还需要做两件事（只需一次）──────────────────────────────
# 1) GitHub 网页: 仓库 Settings → Pages → Build and deployment
#    Source = "Deploy from a branch"，Branch = gh-pages，Folder = /(root)，Save
# 2) 只有走 WITH_CNAME=1 时才需要配 DNS（腾讯云 DNSPod）：
#      A     @    185.199.108.153 / .109.153 / .110.153 / .111.153（4 条）
#      CNAME www  WowCoder.github.io
#    生效后回到 Settings → Pages → Custom domain 填 wowcoder.cn → Save
#    → 等证书签发（约 1 小时内）→ 勾选 Enforce HTTPS
#    注意: 备案通过后必须把 A 记录改回腾讯云 129.204.47.217，否则会被判「空壳网站」注销备案号。
# ────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SITE_DIR="$REPO_ROOT/site"
BRANCH="gh-pages"
WORKTREE="/tmp/t2c-pages-$$"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

cd "$REPO_ROOT"

# 前置检查
[ -d "$SITE_DIR" ] || { echo "✗ 找不到 site/ 目录"; exit 1; }
[ -f "$SITE_DIR/index.html" ] || { echo "✗ 缺少 site/index.html"; exit 1; }
git remote get-url origin >/dev/null 2>&1 || { echo "✗ 没有 origin 远端"; exit 1; }

# WITH_CNAME=1 时才带上自定义域名（deploy/CNAME.template）
# 不带 CNAME  → 站点跑在 https://wowcoder.github.io/talk2code/ ，立刻可访问
# 带上 CNAME  → github.io 会 301 跳到 wowcoder.cn，必须先配好 DNS，否则两处都打不开
WITH_CNAME="${WITH_CNAME:-0}"
if [ "$WITH_CNAME" = "1" ]; then
  [ -f "$REPO_ROOT/deploy/CNAME.template" ] || { echo "✗ 缺少 deploy/CNAME.template"; exit 1; }
  echo "==> 模式: 自定义域名（会 301 到 $(cat "$REPO_ROOT/deploy/CNAME.template")）"
else
  echo "==> 模式: github.io 子路径（https://wowcoder.github.io/talk2code/）"
fi

echo "==> 远端: $(git remote get-url origin)"
echo "==> 待发布文件:"
(cd "$SITE_DIR" && find . -type f -not -path './.DS_Store' | sort | sed 's/^/    /')
echo "==> 总体积: $(du -sh "$SITE_DIR" | cut -f1)"

# 用独立 worktree 打包，完全不动 main 的工作区（main 上还有未提交改动）
# 先删掉本地同名分支：--orphan 遇到已存在的分支会直接失败
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git branch -D "$BRANCH" >/dev/null
fi
git worktree add --detach "$WORKTREE" >/dev/null
cd "$WORKTREE"
git checkout --orphan "$BRANCH"   # 注意: 别吞掉 stderr，否则失败时脚本静默退出
git rm -rf --quiet . >/dev/null 2>&1 || true

# 拷贝站点文件（含隐藏文件 CNAME / .nojekyll）
(cd "$SITE_DIR" && tar --exclude='.DS_Store' --exclude='*.sh' --exclude='*.md' -cf - .) | tar -xf -

if [ "$WITH_CNAME" = "1" ]; then
  cp "$REPO_ROOT/deploy/CNAME.template" "$WORKTREE/CNAME"
fi

git add -A
echo "==> 将提交 $(git diff --cached --name-only | wc -l | tr -d ' ') 个文件"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "==> --dry-run：到此为止，未提交未推送"
  git status --short | sed 's/^/    /'
else
  git commit -m "site: 发布 Talk2Code 宣传主页到 GitHub Pages" --quiet
  # 孤儿分支每次重建，远端已存在时需强制更新；首次推送不能用 --force-with-lease
  if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    git push --force-with-lease origin "$BRANCH"
  else
    git push origin "$BRANCH"
  fi
  echo "==> 已推送 $BRANCH"
  if [ "$WITH_CNAME" = "1" ]; then
    echo "==> 接下来: Settings → Pages → Custom domain 填 wowcoder.cn → 等证书 → Enforce HTTPS"
  else
    echo "==> 站点地址: https://wowcoder.github.io/talk2code/ （首次构建约 1-2 分钟）"
  fi
fi

# 清理 worktree
cd "$REPO_ROOT"
git worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
