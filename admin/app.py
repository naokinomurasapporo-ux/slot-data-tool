"""
slot-data-tool ローカル管理画面
使い方: python admin/app.py
ブラウザで http://localhost:5000 を開く
"""

import os
import subprocess
import threading
import uuid
from datetime import datetime
from flask import Flask, render_template, Response, jsonify, stream_with_context

app = Flask(__name__)

# プロジェクトのルートディレクトリ（admin/ の1つ上）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PYTHON = os.path.join(BASE_DIR, ".venv", "bin", "python")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")

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
        "description": "データをコミットしてGitHubへpushします",
        "cmd": ["bash", os.path.join(SCRIPTS_DIR, "update_and_publish.sh"), "--push-only"],
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
            text=True,
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
    return render_template("index.html", actions=ACTIONS)


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


if __name__ == "__main__":
    print("=" * 50)
    print("slot-data-tool 管理画面を起動します")
    print("ブラウザで http://localhost:5000 を開いてください")
    print("終了するには Ctrl+C を押してください")
    print("=" * 50)
    app.run(debug=False, host="127.0.0.1", port=5000, threaded=True)
