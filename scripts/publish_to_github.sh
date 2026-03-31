#!/usr/bin/env bash
# ==============================================================================
#  publish_to_github.sh
#  docs/data/ と requirements.txt を git add → commit → push するだけのスクリプト
#  管理画面の「GitHubへ公開」ボタンから呼び出される
# ==============================================================================

set -uo pipefail   # -e は外す（コマンド失敗を自前でハンドルするため）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TODAY="$(date +%Y%m%d)"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo "[$(date '+%H:%M:%S')] ✓ $*"; }
err()  { echo "[$(date '+%H:%M:%S')] ✗ $*"; }
line() { echo "──────────────────────────────────────────────────"; }

line
log "GitHubへ公開を開始します（${TODAY}）"
line

cd "$PROJECT_ROOT"

# ===== Step 1: git add =====
log "[git add] docs/data/ requirements.txt を追加します..."
git add docs/data/ requirements.txt
ADD_RC=$?
if [ $ADD_RC -ne 0 ]; then
  err "[git add] 失敗しました（終了コード: $ADD_RC）"
  exit 1
fi
ok "[git add] 完了"

# ===== Step 2: 変更があるか確認 =====
log "[git status] 変更内容を確認します..."
git status --short
if git diff --cached --quiet; then
  log "変更なし: コミットするファイルがありません。pushをスキップします。"
  line
  ok "処理完了（変更がなかったため push は行いませんでした）"
  line
  exit 0
fi

# ===== Step 3: git commit =====
COMMIT_MSG="データ更新 ${TODAY}"
log "[git commit] コミットメッセージ: \"${COMMIT_MSG}\""
git commit -m "$COMMIT_MSG"
COMMIT_RC=$?
if [ $COMMIT_RC -ne 0 ]; then
  err "[git commit] 失敗しました（終了コード: $COMMIT_RC）"
  err "コミットに失敗しました。git の設定（user.name / user.email）を確認してください。"
  exit 1
fi
ok "[git commit] コミット成功: ${COMMIT_MSG}"

# ===== Step 4: git push =====
log "[git push] GitHub へ push します..."
git push
PUSH_RC=$?
if [ $PUSH_RC -ne 0 ]; then
  err "[git push] 失敗しました（終了コード: $PUSH_RC）"
  err "ネットワーク接続や GitHub の認証設定を確認してください。"
  exit 1
fi
ok "[git push] push 成功"

# ===== 完了 =====
line
ok "GitHubへの公開が完了しました（${TODAY}）"
OWNER_REPO="$(git remote get-url origin 2>/dev/null | sed 's|.*github.com/||' | sed 's/\.git$//')"
if [ -n "$OWNER_REPO" ]; then
  PAGES_URL="https://$(echo "$OWNER_REPO" | cut -d/ -f1).github.io/$(echo "$OWNER_REPO" | cut -d/ -f2)/"
  log "公開URL（数分後に反映）: $PAGES_URL"
fi
line
