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


if __name__ == "__main__":
    print("=" * 50)
    print("slot-data-tool 管理画面を起動します")
    print("ブラウザで http://localhost:5000 を開いてください")
    print("終了するには Ctrl+C を押してください")
    print("=" * 50)
    app.run(debug=False, host="127.0.0.1", port=5000, threaded=True)
