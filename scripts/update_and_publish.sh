#!/usr/bin/env bash
# ==============================================================================
#  update_and_publish.sh
#  毎日の更新を1コマンドで実行するスクリプト
#
#  処理の流れ:
#    1. 全店舗のデータ取得・高設定判定
#    2. 30日横持ちデータ生成・Web用JSON更新
#    3. docs/data/ を git add → commit → push
#
#  使い方（プロジェクトルートで実行）:
#    bash scripts/update_and_publish.sh
#
#  オプション:
#    --no-push    データ取得・生成だけ行い、git push はしない
#    --push-only  取得・生成をスキップして git push だけ行う
# ==============================================================================

set -euo pipefail  # エラー発生時に即座に停止

# ===== 設定 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
TODAY="$(date +%Y%m%d)"

# フラグ
DO_SCRAPE=true
DO_PUSH=true

for arg in "$@"; do
  case "$arg" in
    --no-push)   DO_PUSH=false ;;
    --push-only) DO_SCRAPE=false ;;
  esac
done

# ===== ログ出力ヘルパー =====
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo "[$(date '+%H:%M:%S')] ✓ $*"; }
err()  { echo "[$(date '+%H:%M:%S')] ✗ $*" >&2; }
line() { echo "──────────────────────────────────────────────────"; }

# ===== 前提チェック =====
line
log "slot-data-tool 毎日更新スクリプト  ($TODAY)"
line

if [ ! -f "$VENV_PYTHON" ]; then
  err "仮想環境が見つかりません: $VENV_PYTHON"
  err "以下を実行してセットアップしてください:"
  err "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

if [ ! -f "$PROJECT_ROOT/config/storage_state.json" ]; then
  err "セッションファイルがありません: config/storage_state.json"
  err "以下を実行してログインしてください:"
  err "  python scripts/save_session.py"
  exit 1
fi

cd "$PROJECT_ROOT"

# ===== Step 1: データ取得・高設定判定 =====
if [ "$DO_SCRAPE" = true ]; then
  line
  log "【Step 1】全店舗データ取得・高設定判定 を開始します"
  line
  if ! "$VENV_PYTHON" scripts/run_all_stores_pipeline.py; then
    err "データ取得に失敗しました。処理を中止します。"
    err "エラー内容を確認し、必要なら python scripts/save_session.py を再実行してください。"
    exit 1
  fi
  ok "Step 1 完了"
  echo

  # ===== Step 2: 30日横持ちデータ生成・Web用JSON更新 =====
  line
  log "【Step 2】30日横持ちデータ生成・docs/data/ 更新 を開始します"
  line
  if ! "$VENV_PYTHON" scripts/build_30day_store_json.py; then
    err "データ整形に失敗しました。処理を中止します。"
    exit 1
  fi
  ok "Step 2 完了"
  echo
fi

# ===== Step 3: git add / commit / push =====
if [ "$DO_PUSH" = true ]; then
  line
  log "【Step 3】GitHub に公開します"
  line

  # 変更があるか確認
  if git -C "$PROJECT_ROOT" diff --quiet HEAD -- docs/data/ 2>/dev/null && \
     ! git -C "$PROJECT_ROOT" ls-files --others --exclude-standard -- docs/data/ | grep -q .; then
    log "docs/data/ に変更がありません。push をスキップします。"
  else
    git -C "$PROJECT_ROOT" add docs/data/

    COMMIT_MSG="データ更新 ${TODAY}"
    git -C "$PROJECT_ROOT" commit -m "$COMMIT_MSG"
    ok "コミット: $COMMIT_MSG"

    if ! git -C "$PROJECT_ROOT" push; then
      err "git push に失敗しました。"
      err "ネットワーク接続や GitHub の認証設定を確認してください。"
      exit 1
    fi
    ok "GitHub Pages に公開しました"
  fi
  echo
fi

# ===== 完了 =====
line
ok "すべての処理が完了しました ($TODAY)"
if [ "$DO_PUSH" = true ]; then
  REPO_URL="$(git -C "$PROJECT_ROOT" remote get-url origin 2>/dev/null | sed 's/\.git$//' | sed 's|https://github.com/|https://|')"
  OWNER_REPO="$(git -C "$PROJECT_ROOT" remote get-url origin 2>/dev/null | sed 's|.*github.com/||' | sed 's/\.git$//')"
  PAGES_URL="https://$(echo "$OWNER_REPO" | cut -d/ -f1).github.io/$(echo "$OWNER_REPO" | cut -d/ -f2)/"
  log "公開URL（数分後に反映）: $PAGES_URL"
fi
line
