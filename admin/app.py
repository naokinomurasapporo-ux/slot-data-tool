"""
slot-data-tool ローカル管理画面
使い方: python admin/app.py
ブラウザで http://localhost:5000 を開く
"""

import json
import os
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, Response, jsonify, request, stream_with_context

app = Flask(__name__)

# プロジェクトのルートディレクトリ（admin/ の1つ上）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PYTHON = os.path.join(BASE_DIR, ".venv", "bin", "python")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")

# 判定ルールファイル
RULES_PATH = Path(BASE_DIR) / "config" / "rules.json"
RULES_BACKUP_DIR = Path(BASE_DIR) / "config" / "backups"
RULES_BACKUP_KEEP = 10  # 保持するバックアップ件数
REJUDGE_SCRIPT = os.path.join(SCRIPTS_DIR, "rejudge_existing.py")

# 公約カレンダーファイル
EVENTS_PATH           = Path(BASE_DIR) / "config" / "events.json"
RECURRING_RULES_PATH  = Path(BASE_DIR) / "config" / "recurring_rules.json"
STORES_CONFIG_PATH    = Path(BASE_DIR) / "config" / "stores.json"

# 公約タグ定義（tag_key → 表示名）
EVENT_TAGS = {
    "is_tokuteibi":        "特定日",
    "is_old_event_day":    "旧イベント日",
    "is_syuzai_day":       "取材日",
    "is_raiten_day":       "来店日",
    "is_anniversary":      "周年日",
    "is_zorome_day":       "ゾロ目日",
    "is_juggler_boost_day":"ジャグラー強化日",
    "has_suffix_koyaku":   "末尾公約",
    "has_narabi_koyaku":   "並び公約",
}

# 編集可能な数値フィールド（表示順）
RULE_NUMERIC_FIELDS = [
    "min_games_blank", "min_games_circle", "min_games_double",
    "reg_good", "reg_better", "reg_best",
    "comb_good", "comb_better", "comb_best",
]

# 実行中・完了したジョブの記録
# { job_id: { status, lines, returncode, start_time, end_time } }
jobs: dict = {}

# ボタン定義
ACTIONS = {
    "update_all": {
        "label": "全店舗更新",
        "description": "全店舗の本日分データを取得します（既存ファイルはスキップ）",
        "cmd": [VENV_PYTHON, "-u", os.path.join(SCRIPTS_DIR, "run_all_stores_pipeline.py"), "--skip-existing"],
    },
    "backfill_1": {
        "label": "昨日分バックフィル",
        "description": "昨日分のデータが欠けている店舗を補完します",
        "cmd": [VENV_PYTHON, "-u", os.path.join(SCRIPTS_DIR, "run_all_stores_pipeline.py"), "--backfill", "1", "--yes"],
    },
    "backfill_3": {
        "label": "3日分バックフィル",
        "description": "直近3日間で欠けているデータを補完します",
        "cmd": [VENV_PYTHON, "-u", os.path.join(SCRIPTS_DIR, "run_all_stores_pipeline.py"), "--backfill", "3", "--yes"],
    },
    "rebuild_30d": {
        "label": "30日データ再生成",
        "description": "過去30日分の集計JSONを再構築します",
        "cmd": [VENV_PYTHON, "-u", os.path.join(SCRIPTS_DIR, "build_30day_store_json.py")],
    },
    "publish": {
        "label": "GitHubへ公開",
        "description": "docs/data/ と requirements.txt を git add → commit → push します",
        "cmd": ["bash", os.path.join(SCRIPTS_DIR, "publish_to_github.sh")],
    },
}


def _run_job(job_id: str, cmd: list):
    """バックグラウンドスレッドでコマンドを実行し、出力を jobs に蓄積する"""
    jobs[job_id]["status"] = "running"
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=BASE_DIR,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            env=env,
        )
        # readline() で1行ずつ読む（forループはバッファリングされることがある）
        while True:
            line = process.stdout.readline()
            if line == "" and process.poll() is not None:
                break
            if line:
                jobs[job_id]["lines"].append(line.rstrip())
        process.wait()
        rc = process.returncode
        jobs[job_id]["returncode"] = rc
        jobs[job_id]["status"] = "done" if rc == 0 else "error"
    except Exception as e:
        jobs[job_id]["lines"].append(f"[ERROR] {e}")
        jobs[job_id]["status"] = "error"
    finally:
        jobs[job_id]["end_time"] = datetime.now().strftime("%H:%M:%S")


@app.route("/")
def index():
    return render_template("index.html", actions=ACTIONS, max_backups=RULES_BACKUP_KEEP)


@app.route("/run/<action_id>", methods=["POST"])
def run_action(action_id):
    if action_id not in ACTIONS:
        return jsonify({"error": "不明なアクションです"}), 400

    # 同じアクションが実行中なら弾く
    for job in jobs.values():
        if job.get("action_id") == action_id and job.get("status") == "running":
            return jsonify({"error": "すでに実行中です。完了をお待ちください。"}), 409

    job_id = str(uuid.uuid4())[:8]
    action = ACTIONS[action_id]
    jobs[job_id] = {
        "action_id": action_id,
        "label": action["label"],
        "description": action.get("description", ""),
        "cmd_display": " ".join(os.path.basename(str(c)) if i == 0 else str(c) for i, c in enumerate(action["cmd"])),
        "status": "starting",
        "lines": [],
        "returncode": None,
        "start_time": datetime.now().strftime("%H:%M:%S"),
        "end_time": None,
    }

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, ACTIONS[action_id]["cmd"]),
        daemon=True,
    )
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def get_status(job_id):
    if job_id not in jobs:
        return jsonify({"error": "ジョブが見つかりません"}), 404
    return jsonify(jobs[job_id])


@app.route("/stream/<job_id>")
def stream_log(job_id):
    """Server-Sent Events でログをリアルタイム配信"""
    if job_id not in jobs:
        return jsonify({"error": "ジョブが見つかりません"}), 404

    def generate():
        import time
        job = jobs.get(job_id, {})
        # ジョブ開始情報を最初に送信
        start = job.get("start_time", "")
        label = job.get("label", "")
        desc = job.get("description", "")
        cmd = job.get("cmd_display", "")
        yield f"data: [INFO] ▶ {label} を開始しました（{start}）\n\n"
        if desc:
            yield f"data: [INFO] {desc}\n\n"
        yield f"data: [CMD] $ {cmd}\n\n"
        yield f"data: \n\n"

        sent = 0
        while True:
            job = jobs.get(job_id, {})
            lines = job.get("lines", [])
            # 新しい行を送信
            for line in lines[sent:]:
                yield f"data: {line}\n\n"
            sent = len(lines)

            status = job.get("status", "")
            if status in ("done", "error"):
                rc = job.get("returncode", -1)
                end = job.get("end_time", "")
                yield f"data: \n\n"
                if status == "done":
                    yield f"data: ✅ 完了しました（終了コード: {rc}、終了時刻: {end}）\n\n"
                else:
                    yield f"data: ❌ エラーで終了しました（終了コード: {rc}、終了時刻: {end}）\n\n"
                yield "event: close\ndata: done\n\n"
                break
            time.sleep(0.3)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# 判定ルール API
# ---------------------------------------------------------------------------

def _create_backup() -> str:
    """
    rules.json のバックアップを config/backups/ に作成する。
    ファイル名: rules_YYYYMMDD_HHMMSS.json
    古いバックアップが RULES_BACKUP_KEEP 件を超えたら削除する。
    作成したファイル名（ベース名のみ）を返す。
    """
    RULES_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"rules_{ts}.json"
    shutil.copy2(RULES_PATH, RULES_BACKUP_DIR / backup_name)

    # 古いバックアップを削除
    backups = sorted(RULES_BACKUP_DIR.glob("rules_*.json"), key=lambda p: p.name)
    for old in backups[:-RULES_BACKUP_KEEP]:
        old.unlink()

    return backup_name


def _validate_rules(rules: dict) -> list[str]:
    """
    rules dict を検証してエラーメッセージのリストを返す。空リストなら OK。
    """
    errors = []
    meta_keys = {"_comment", "_threshold_note"}

    for key, rule in rules.items():
        if key in meta_keys or not isinstance(rule, dict):
            continue
        label = f"【{key}】"

        # 数値フィールドの存在・型チェック
        for field in RULE_NUMERIC_FIELDS:
            val = rule.get(field)
            if val is None:
                errors.append(f"{label} {field} がありません")
                continue
            if not isinstance(val, int) or val < 1 or val > 99999:
                errors.append(f"{label} {field} = {val} は 1〜99999 の整数である必要があります")

        if errors:
            continue  # 数値が壊れているなら順序チェックは意味がない

        blank  = rule["min_games_blank"]
        circle = rule["min_games_circle"]
        double = rule["min_games_double"]
        if not (blank < circle < double):
            errors.append(
                f"{label} min_games の順序が不正です "
                f"(blank={blank} < circle={circle} < double={double} である必要があります)"
            )

        for prefix, labels in [("reg", "REG"), ("comb", "合算")]:
            good   = rule[f"{prefix}_good"]
            better = rule[f"{prefix}_better"]
            best   = rule[f"{prefix}_best"]
            if not (good > better > best):
                errors.append(
                    f"{label} {labels}の順序が不正です "
                    f"(設定4={good} > 設定5={better} > 設定6={best} である必要があります)"
                )

    return errors


@app.route("/api/rules", methods=["GET"])
def api_get_rules():
    """現在の rules.json を返す"""
    try:
        with open(RULES_PATH, encoding="utf-8") as f:
            rules = json.load(f)
        return jsonify(rules)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rules", methods=["POST"])
def api_save_rules():
    """
    rules.json を検証・保存する。
    保存前に自動バックアップを作成する。
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "リクエストボディが空です"}), 400

    errors = _validate_rules(data)
    if errors:
        return jsonify({"error": "バリデーションエラー", "details": errors}), 422

    try:
        backup_name = _create_backup()
        with open(RULES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True, "backup": backup_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rules/backup", methods=["POST"])
def api_backup_rules():
    """手動バックアップを作成する"""
    try:
        backup_name = _create_backup()
        return jsonify({"ok": True, "backup": backup_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rules/backups", methods=["GET"])
def api_list_backups():
    """バックアップ一覧を新しい順で返す"""
    RULES_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = sorted(RULES_BACKUP_DIR.glob("rules_*.json"), key=lambda p: p.name, reverse=True)
    result = []
    for p in backups[:RULES_BACKUP_KEEP]:
        stat = p.stat()
        result.append({
            "name": p.name,
            "size_kb": round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return jsonify(result)


@app.route("/run_rejudge", methods=["POST"])
def run_rejudge():
    """
    任意日付（または全件）の judged.json を再判定するジョブを起動する。
    body: { "date": "YYYYMMDD" }  ← 省略で全件
    """
    import re as _re
    data = request.get_json(silent=True) or {}
    date_str = data.get("date", "").strip()

    if date_str and not _re.match(r"^\d{8}$", date_str):
        return jsonify({"error": "日付は YYYYMMDD 形式で指定してください"}), 400

    # 同一アクション（日付問わず）が実行中なら弾く
    for job in jobs.values():
        if job.get("action_id") == "rejudge" and job.get("status") == "running":
            return jsonify({"error": "再判定がすでに実行中です。完了をお待ちください。"}), 409

    cmd = [VENV_PYTHON, "-u", REJUDGE_SCRIPT]
    if date_str:
        cmd += ["--date", date_str]
        label = f"再判定 ({date_str})"
        desc  = f"{date_str} の judged.json を新ルールで再判定します"
    else:
        label = "全件再判定"
        desc  = "全 judged.json を新ルールで再判定します"

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "action_id":   "rejudge",
        "label":       label,
        "description": desc,
        "cmd_display": " ".join(
            os.path.basename(str(c)) if i == 0 else str(c)
            for i, c in enumerate(cmd)
        ),
        "status":     "starting",
        "lines":      [],
        "returncode": None,
        "start_time": datetime.now().strftime("%H:%M:%S"),
        "end_time":   None,
    }
    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return jsonify({"job_id": job_id})


# ---------------------------------------------------------------------------
# 公約カレンダー API
# ---------------------------------------------------------------------------

def _load_events() -> list:
    """events.json を読み込む。ファイルがなければ空リストを返す。"""
    if not EVENTS_PATH.exists():
        return []
    with open(EVENTS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_events(events: list) -> None:
    """events.json に書き込む。"""
    with open(EVENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)


def _validate_event(data: dict) -> list[str]:
    """イベントデータのバリデーション。エラーメッセージのリストを返す。"""
    errors = []
    store = data.get("store_name", "").strip()
    date  = data.get("date", "").strip()
    tags  = data.get("tags", [])

    if not store:
        errors.append("店舗名を選択してください")
    if not date or not date.isdigit() or len(date) != 8:
        errors.append("日付は YYYYMMDD 形式で入力してください")
    if not isinstance(tags, list):
        errors.append("タグの形式が不正です")
    else:
        for t in tags:
            if t not in EVENT_TAGS:
                errors.append(f"不明なタグ: {t}")

    narabi = data.get("narabi_size", 0)
    if not isinstance(narabi, int) or narabi < 0 or narabi > 100:
        errors.append("並び台数は 0〜100 の整数で入力してください")

    return errors


@app.route("/api/events", methods=["GET"])
def api_get_events():
    """公約一覧を日付の昇順で返す。"""
    events = _load_events()
    events_sorted = sorted(events, key=lambda e: (e.get("date", ""), e.get("store_name", "")))
    return jsonify(events_sorted)


@app.route("/api/events", methods=["POST"])
def api_add_event():
    """公約を1件追加する。"""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "リクエストボディが空です"}), 400

    errors = _validate_event(data)
    if errors:
        return jsonify({"error": "入力エラー", "details": errors}), 422

    event = {
        "store_name":  data["store_name"].strip(),
        "date":        data["date"].strip(),
        "tags":        data.get("tags", []),
        "narabi_size": int(data.get("narabi_size", 0)),
        "memo":        data.get("memo", "").strip(),
    }

    events = _load_events()

    # 同じ店舗・同じ日付の重複チェック
    for e in events:
        if e["store_name"] == event["store_name"] and e["date"] == event["date"]:
            return jsonify({"error": f"{event['store_name']} の {event['date']} はすでに登録されています"}), 409

    events.append(event)
    _save_events(events)
    return jsonify({"ok": True, "event": event})


@app.route("/api/events/<int:index>", methods=["DELETE"])
def api_delete_event(index):
    """公約を1件削除する（インデックス指定）。"""
    events = _load_events()
    events_sorted = sorted(events, key=lambda e: (e.get("date", ""), e.get("store_name", "")))

    if index < 0 or index >= len(events_sorted):
        return jsonify({"error": "指定されたインデックスが存在しません"}), 404

    removed = events_sorted.pop(index)
    _save_events(events_sorted)
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/stores", methods=["GET"])
def api_get_stores():
    """有効な店舗名一覧を返す（公約フォームのプルダウン用）。"""
    if not STORES_CONFIG_PATH.exists():
        return jsonify([])
    with open(STORES_CONFIG_PATH, encoding="utf-8") as f:
        stores = json.load(f)
    enabled = sorted(
        [s["store_name"] for s in stores if s.get("enabled", False)],
        key=lambda n: next((s.get("sort_order", 999) for s in stores if s["store_name"] == n), 999)
    )
    return jsonify(enabled)


@app.route("/api/event_tags", methods=["GET"])
def api_get_event_tags():
    """タグ定義（key → 表示名）を返す。"""
    return jsonify(EVENT_TAGS)


# ---------------------------------------------------------------------------
# 繰り返しルール API
# ---------------------------------------------------------------------------

def _load_recurring_rules() -> list:
    """recurring_rules.json を読み込む。ファイルがなければ空リストを返す。"""
    if not RECURRING_RULES_PATH.exists():
        return []
    with open(RECURRING_RULES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_recurring_rules(rules: list) -> None:
    """recurring_rules.json に書き込む。"""
    with open(RECURRING_RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def _validate_recurring_rule(data: dict) -> list:
    """繰り返しルールのバリデーション。エラーメッセージのリストを返す。"""
    errors = []
    store = data.get("store_name", "").strip()
    recurrence = data.get("recurrence", {})
    tags = data.get("tags", [])

    if not store:
        errors.append("店舗名を選択してください")

    rtype = recurrence.get("type", "")
    if rtype == "monthly_day":
        days = recurrence.get("days", [])
        if not days or not isinstance(days, list):
            errors.append("繰り返し日（days）を1つ以上指定してください")
        else:
            for d in days:
                if not isinstance(d, int) or d < 1 or d > 31:
                    errors.append(f"日付 {d} は 1〜31 の整数である必要があります")
    elif rtype == "weekly_day":
        weekday = recurrence.get("weekday")
        if weekday is None or not isinstance(weekday, int) or weekday < 0 or weekday > 6:
            errors.append("weekday は 0〜6 の整数である必要があります（0=月〜6=日）")
    elif rtype == "monthly_nth_weekday":
        n = recurrence.get("n")
        weekday = recurrence.get("weekday")
        if n is None or not isinstance(n, int) or n < 1 or n > 5:
            errors.append("n は 1〜5 の整数である必要があります")
        if weekday is None or not isinstance(weekday, int) or weekday < 0 or weekday > 6:
            errors.append("weekday は 0〜6 の整数である必要があります（0=月〜6=日）")
    elif rtype == "yearly":
        month = recurrence.get("month")
        day = recurrence.get("day")
        if month is None or not isinstance(month, int) or month < 1 or month > 12:
            errors.append("month は 1〜12 の整数である必要があります")
        if day is None or not isinstance(day, int) or day < 1 or day > 31:
            errors.append("day は 1〜31 の整数である必要があります")
    else:
        errors.append(f"不明な recurrence type: '{rtype}'")

    if not isinstance(tags, list):
        errors.append("タグの形式が不正です")
    else:
        for t in tags:
            if t not in EVENT_TAGS:
                errors.append(f"不明なタグ: {t}")

    narabi = data.get("narabi_size", 0)
    if not isinstance(narabi, int) or narabi < 0 or narabi > 100:
        errors.append("並び台数は 0〜100 の整数で入力してください")

    return errors


@app.route("/api/recurring_rules", methods=["GET"])
def api_get_recurring_rules():
    """繰り返しルール一覧を返す。"""
    rules = _load_recurring_rules()
    return jsonify(rules)


@app.route("/api/recurring_rules", methods=["POST"])
def api_add_recurring_rule():
    """繰り返しルールを1件追加する。"""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "リクエストボディが空です"}), 400

    errors = _validate_recurring_rule(data)
    if errors:
        return jsonify({"error": "入力エラー", "details": errors}), 422

    rule = {
        "id": str(uuid.uuid4())[:8],
        "store_name":  data["store_name"].strip(),
        "recurrence":  data["recurrence"],
        "tags":        data.get("tags", []),
        "narabi_size": int(data.get("narabi_size", 0)),
        "memo":        data.get("memo", "").strip(),
        "active":      True,
    }

    rules = _load_recurring_rules()
    rules.append(rule)
    _save_recurring_rules(rules)
    return jsonify({"ok": True, "rule": rule})


@app.route("/api/recurring_rules/<rule_id>", methods=["DELETE"])
def api_delete_recurring_rule(rule_id):
    """繰り返しルールを1件削除する（ID指定）。"""
    rules = _load_recurring_rules()
    new_rules = [r for r in rules if r.get("id") != rule_id]
    if len(new_rules) == len(rules):
        return jsonify({"error": "指定されたIDのルールが存在しません"}), 404
    _save_recurring_rules(new_rules)
    return jsonify({"ok": True})


@app.route("/api/recurring_rules/<rule_id>/toggle", methods=["PATCH"])
def api_toggle_recurring_rule(rule_id):
    """繰り返しルールの有効/無効を切り替える。"""
    rules = _load_recurring_rules()
    for rule in rules:
        if rule.get("id") == rule_id:
            rule["active"] = not rule.get("active", True)
            _save_recurring_rules(rules)
            return jsonify({"ok": True, "active": rule["active"]})
    return jsonify({"error": "指定されたIDのルールが存在しません"}), 404


# ---------------------------------------------------------------------------
# 台番号別強さ分析 API
# ---------------------------------------------------------------------------

PROCESSED_DIR = Path(BASE_DIR) / "data" / "processed"

_JUDGE_SCORE = {"◎": 3, "○": 2, "△": 1, "×": 0}
_HIGH_JUDGES = {"◎", "○"}


def _aggregate_units(store_json: dict) -> list:
    from collections import defaultdict
    unit_stats: dict = defaultdict(lambda: {
        "score": 0, "double": 0, "circle": 0, "triangle": 0, "cross": 0,
        "judged_days": 0, "machines": set(),
    })
    for machine in store_json.get("machines", []):
        mname = machine["name"]
        for unit in machine.get("units", []):
            uid = unit["unit"]
            s = unit_stats[uid]
            s["machines"].add(mname)
            for day_data in unit.get("days", {}).values():
                judge = day_data.get("judge", "blank")
                if judge not in _JUDGE_SCORE:
                    continue
                s["judged_days"] += 1
                s["score"] += _JUDGE_SCORE[judge]
                if judge == "◎":   s["double"]   += 1
                elif judge == "○": s["circle"]   += 1
                elif judge == "△": s["triangle"] += 1
                elif judge == "×": s["cross"]    += 1

    results = []
    for uid, s in unit_stats.items():
        judged = s["judged_days"]
        high = s["double"] + s["circle"]
        results.append({
            "unit": uid,
            "judged_days": judged,
            "score": s["score"],
            "avg_score": round(s["score"] / judged, 3) if judged > 0 else 0.0,
            "double": s["double"],
            "circle": s["circle"],
            "triangle": s["triangle"],
            "cross": s["cross"],
            "high_rate": round(high / judged, 4) if judged > 0 else 0.0,
            "machines": sorted(s["machines"]),
        })
    return results


def _aggregate_suffix(unit_rows: list, suffix_len: int) -> list:
    from collections import defaultdict
    suffix_stats: dict = defaultdict(lambda: {
        "score": 0, "judged_days": 0, "high_days": 0, "unit_count": 0,
    })
    for row in unit_rows:
        uid = row["unit"]
        suffix = uid[-suffix_len:].zfill(suffix_len) if uid.isdigit() else uid[-suffix_len:]
        s = suffix_stats[suffix]
        s["judged_days"] += row["judged_days"]
        s["score"] += row["score"]
        s["high_days"] += row["double"] + row["circle"]
        s["unit_count"] += 1

    results = []
    for suffix, s in suffix_stats.items():
        judged = s["judged_days"]
        results.append({
            "suffix": suffix,
            "unit_count": s["unit_count"],
            "judged_days": judged,
            "score": s["score"],
            "avg_score": round(s["score"] / judged, 3) if judged > 0 else 0.0,
            "high_days": s["high_days"],
            "high_rate": round(s["high_days"] / judged, 4) if judged > 0 else 0.0,
        })
    return results


@app.route("/api/analysis/unit_strength", methods=["GET"])
def api_unit_strength():
    """台番号別強さ分析を実行してJSONで返す。"""
    store_name = request.args.get("store", "").strip()
    try:
        top       = int(request.args.get("top", 15))
        min_days  = int(request.args.get("min_days", 3))
        suffix_len = int(request.args.get("suffix_len", 1))
    except ValueError:
        return jsonify({"error": "パラメータが不正です"}), 400

    if not store_name:
        return jsonify({"error": "店舗名を指定してください"}), 400

    # 30d JSON を探す
    safe_name = store_name.replace("/", "_").replace(" ", "_").replace("　", "_")
    path = PROCESSED_DIR / f"30d_{safe_name}.json"
    if not path.exists():
        return jsonify({"error": f"30日データが見つかりません: 30d_{safe_name}.json"}), 404

    with open(path, encoding="utf-8") as f:
        store_json = json.load(f)

    unit_rows = _aggregate_units(store_json)
    if not unit_rows:
        return jsonify({"error": "データがありません"}), 404

    # フィルタ・ランキング
    filtered = [r for r in unit_rows if r["judged_days"] >= min_days]
    score_ranking  = sorted(filtered, key=lambda r: (-r["avg_score"], -r["score"], r["unit"]))[:top]
    high_ranking   = sorted(filtered, key=lambda r: (-r["high_rate"], -(r["double"] + r["circle"]), r["unit"]))[:top]
    suffix_rows    = _aggregate_suffix(unit_rows, suffix_len)
    suffix_ranking = sorted(suffix_rows, key=lambda r: (-r["high_rate"], -r["avg_score"]))

    return jsonify({
        "store_name":     store_name,
        "total_units":    len(unit_rows),
        "filtered_units": len(filtered),
        "min_days":       min_days,
        "score_ranking":  score_ranking,
        "high_ranking":   high_ranking,
        "suffix_ranking": suffix_ranking,
        "suffix_len":     suffix_len,
    })


if __name__ == "__main__":
    print("=" * 50)
    print("slot-data-tool 管理画面を起動します")
    print("ブラウザで http://localhost:5000 を開いてください")
    print("終了するには Ctrl+C を押してください")
    print("=" * 50)
    app.run(debug=False, host="127.0.0.1", port=5000, threaded=True)
