# slot-data-tool

サイトセブン スマホサイトからジャグラーデータを自動取得し、
**GitHub Pages** で30日間の高設定判定一覧を表示するツールです。

---

## できること

- 複数店舗のジャグラーデータを一括取得（自動スクレイピング）
- 機種ごと・台番号ごとに高設定判定（◎/○/△/×）を付与
- 過去30日分を横持ち表で表示する Web 画面を生成
- GitHub Pages で公開 → URL を共有するだけで誰でも閲覧可能

---

## フォルダ構成

```
slot-data-tool/
├── config/
│   ├── stores.json            # 取得対象の店舗リスト
│   ├── rules.json             # 高設定判定ルール（機種別しきい値）
│   ├── test_store.json        # 動作確認用の1店舗設定
│   └── storage_state.json     # ログイン済みセッション ※Gitに含めない
├── data/
│   ├── raw/                   # 取得した生データ・スクリーンショット ※Gitに含めない
│   └── processed/             # 日次の判定JSONと集計済みJSON ※Gitに含めない
├── docs/                      # GitHub Pages 公開用（Gitで管理）
│   ├── index.html             # 30日判定 Web 画面
│   └── data/                  # Web 画面が読み込むJSONデータ
│       ├── stores.json        # 店舗一覧マニフェスト
│       └── 30d_店舗名.json    # 店舗ごとの30日横持ちデータ
├── scripts/
│   ├── save_session.py        # 初回のみ: 手動ログイン → セッション保存
│   ├── run_all_stores_pipeline.py  # 毎日Step1: 全店舗データ取得 + 判定
│   ├── build_30day_store_json.py   # 毎日Step2: 30日横持ちデータ生成
│   ├── run_one_store_pipeline.py   # 1店舗だけ処理したいとき
│   ├── judge_jugler.py             # 高設定判定ロジック
│   └── poc_scrape_one_store.py     # スクレイピング処理（共通関数）
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 初回セットアップ

### 1. Python の確認

```bash
python3 --version
```

Python 3.9 以上が表示されれば OK。

### 2. 仮想環境の作成と有効化

```bash
cd ~/slot-data-tool
python3 -m venv .venv
source .venv/bin/activate
```

プロンプトの先頭に `(.venv)` が表示されたら成功。

### 3. ライブラリのインストール

```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. 取得対象の店舗を設定する

`config/stores.json` を編集し、取得したい店舗名を記入します。

```json
[
  { "store_name": "○○パチンコ店", "enabled": true, "sort_order": 1 },
  { "store_name": "△△スロット店", "enabled": true, "sort_order": 2 }
]
```

> 店舗名はサイトセブンの「マイホール」に登録されている名称と一致させてください。

### 5. セッション保存（初回のみ）

```bash
python scripts/save_session.py
```

1. ブラウザが自動的に開く
2. サイトセブンにログインする
3. ログイン完了後、ターミナルに戻って **Enter** を押す
4. `config/storage_state.json` に保存されて終了

> セッションが切れた（ログアウトされた）場合はこのコマンドを再実行してください。

---

## 毎日の使い方（データ更新）

### ワンコマンドで完結する方法（推奨）

```bash
bash scripts/update_and_publish.sh
```

このコマンド1つで以下をすべて自動で行います。

| ステップ | 内容 |
|---|---|
| Step 1 | 全店舗のデータ取得 + 高設定判定 |
| Step 2 | 30日横持ちデータ生成・Web用JSON更新 |
| Step 3 | `docs/data/` を git add → commit → push |

数分後に GitHub Pages が最新データに更新されます。

#### オプション

```bash
# データ取得・生成だけ行い、git push はしない（確認したいとき）
bash scripts/update_and_publish.sh --no-push

# 取得・生成をスキップして git push だけ行う（手動で修正した後など）
bash scripts/update_and_publish.sh --push-only
```

---

### 手動で1ステップずつ実行したい場合

```bash
# Step 1: データ取得 + 高設定判定
python scripts/run_all_stores_pipeline.py

# Step 2: 30日横持ちデータ生成・Web用JSON更新
python scripts/build_30day_store_json.py

# Step 3: GitHub に push
git add docs/data/
git commit -m "データ更新 $(date +%Y%m%d)"
git push
```

---

### 取り逃した日を後から埋める（バックフィル）

Mac がスリープしていて自動実行が走らなかった日がある場合に使います。

サイトセブンの大当り一覧ページには**日付タブ**（3/17  3/16  3/15 … や `>>` ボタン）があり、
最大8日分程度の過去データを見ることができます。バックフィルはこのタブを実際に押して
その日の本物のデータを取得します。タブが存在しない日（古すぎる日など）は自動スキップされます。

#### 欠けている日を確認する（実行なし）

```bash
python scripts/run_all_stores_pipeline.py --backfill 7 --dry-run
```

直近7日間で欠けている「日付 × 店舗」の一覧を表示するだけで、実際の取得は行いません。

#### 欠けている日をまとめて埋める

```bash
python scripts/run_all_stores_pipeline.py --backfill 7
```

直近7日間で欠けているすべての「日付 × 店舗」の組み合わせを埋めます。
各 (日付, 店舗) ごとに大当り一覧の日付タブを押して実際のデータを取得します。
日付タブが存在しない場合はスキップされ、サマリーに記録されます。
確認プロンプトが出るので `y` を入力して続行してください。

埋め終わったら、30日データを再生成して公開します：

```bash
python scripts/build_30day_store_json.py
git add docs/data/
git commit -m "バックフィル $(date +%Y%m%d)"
git push
```

#### 特定の日付を指定して取得する

```bash
# 昨日のデータとしてラベルを付けて保存する
python scripts/run_all_stores_pipeline.py --date 20260315
```

`--date` を使うと、大当り一覧の指定日付タブをクリックしてその日のデータを取得します。
日付タブが見つからない場合は取得できません（サイトの保持期間外）。

#### 既存ファイルをスキップして再実行する

```bash
# 今日のデータがある店舗はスキップして、失敗した店舗だけ再実行
python scripts/run_all_stores_pipeline.py --skip-existing
```

---

### セッションが切れた場合

```bash
python scripts/save_session.py
```

ブラウザが開くのでサイトセブンにログインし、ターミナルに戻って Enter を押してください。

---

## GitHub Pages 公開手順

### 1. GitHub にリポジトリを作成する

- GitHub にログイン → **New repository**
- リポジトリ名: `slot-data-tool`（任意）
- **Private** を推奨（データを非公開にしたい場合）
- 「Initialize this repository」はチェックしない

### 2. ローカルと GitHub を接続する（初回のみ）

```bash
cd ~/slot-data-tool
git init
git add .
git commit -m "初回コミット"
git remote add origin https://github.com/あなたのユーザー名/slot-data-tool.git
git push -u origin main
```

### 3. GitHub Pages を有効にする

1. GitHub のリポジトリページを開く
2. **Settings** タブ → 左メニューの **Pages**
3. **Source** を「Deploy from a branch」に設定
4. **Branch** を `main`、フォルダを `/docs` に設定
5. **Save** を押す

数分後に以下のような URL で公開されます：

```
https://あなたのユーザー名.github.io/slot-data-tool/
```

### 4. ローカルで表示確認する方法

GitHub に push する前にローカルで確認したい場合：

```bash
python -m http.server 8080
```

ブラウザで `http://localhost:8080/docs/` を開いてください。

> ⚠️ `docs/index.html` をダブルクリックして直接開いても動きません。
> 必ず上記のコマンドを使ってください（セキュリティ制限のため）。

---

## 店舗の ON/OFF を切り替える

`config/stores.json` を直接編集しなくても、スクリプトで切り替えられます。

### 対話モード（推奨）

```bash
python scripts/toggle_stores.py
```

起動すると店舗一覧が番号付きで表示されます。
番号を入力するたびに ON/OFF が切り替わり、`s` で保存、`q` で破棄して終了です。

```
  番号  状態   店舗名
  ──────────────────────────────────────────────────
  [ 1]  ○ OFF  有料会員登録はこちら >
  [ 3]  ● ON   マルハン新宿東宝ビル店
  ...

>>> 3        ← 3番を切り替え
>>> 5 6      ← 5番と6番をまとめて切り替え
>>> s        ← 保存して終了
```

### コマンドラインオプション

```bash
# 全店舗まとめて ON
python scripts/toggle_stores.py --all-on

# 全店舗まとめて OFF
python scripts/toggle_stores.py --all-off

# 指定した番号だけ ON（複数指定可）
python scripts/toggle_stores.py --on 3 4 5

# 指定した番号だけ OFF
python scripts/toggle_stores.py --off 1 2
```

---

## 毎日23:30に自動実行する（Mac launchd）

Mac の「スケジュール機能（launchd）」を使って、毎日23:30に自動でデータ取得・公開を行えます。
一度登録すれば、以後は Mac が起きている限り自動で動き続けます。

### 登録手順（初回のみ）

```bash
cd ~/slot-data-tool

# 1. plist ファイルを launchd の所定フォルダにコピー
cp launchd/com.naoki.slotdatatool.plist ~/Library/LaunchAgents/

# 2. launchd に登録して有効化
launchctl load ~/Library/LaunchAgents/com.naoki.slotdatatool.plist
```

これで毎日23:30に自動実行されます。

> **注意**: Mac がスリープ中は実行されません。
> 23:30 に Mac がスリープしていた場合、次の起動時には実行されません（その日の実行はスキップ）。

### 今すぐ手動でテスト実行する

```bash
launchctl start com.naoki.slotdatatool
```

### 登録を確認する

```bash
launchctl list | grep slotdatatool
```

`-` や `0` が表示されれば正常に登録済みです。

### ログを確認する

```bash
# ログをリアルタイムで確認（Ctrl+C で終了）
tail -f logs/update.log

# 最後の50行だけ表示
tail -50 logs/update.log
```

### 自動実行を停止・削除する

```bash
# 一時停止（ファイルは残る）
launchctl unload ~/Library/LaunchAgents/com.naoki.slotdatatool.plist

# 再開
launchctl load ~/Library/LaunchAgents/com.naoki.slotdatatool.plist
```

### 実行時刻を変更したい場合

`launchd/com.naoki.slotdatatool.plist` をテキストエディタで開き、
`Hour` と `Minute` の数字を変更してから再登録します。

```bash
# 再登録（変更を反映）
launchctl unload ~/Library/LaunchAgents/com.naoki.slotdatatool.plist
cp launchd/com.naoki.slotdatatool.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.naoki.slotdatatool.plist
```

---

## スクリプト一覧

| スクリプト | 用途 |
|---|---|
| `scripts/toggle_stores.py` | **店舗の ON/OFF を対話式で切り替え** |
| `scripts/update_and_publish.sh` | **毎日の更新を1コマンドで実行（推奨）** |
| `scripts/save_session.py` | 初回ログイン → セッション保存 |
| `scripts/run_all_stores_pipeline.py` | 全店舗のデータ取得・判定・保存 |
| `scripts/run_one_store_pipeline.py` | 1店舗だけ処理（テスト用） |
| `scripts/build_30day_store_json.py` | 30日横持ちデータ生成・Web用JSON出力 |
| `scripts/judge_jugler.py` | 高設定判定ロジック（単体実行も可） |
| `scripts/poc_scrape_one_store.py` | スクレイピング共通関数（直接実行で動作確認も可） |
| `scripts/export_myhole_stores.py` | マイホール登録店舗一覧の書き出し |

---

## 判定基準（◎/○/△/×）

判定は `config/rules.json` のしきい値に基づきます。

| 判定 | 意味 |
|---|---|
| ◎ | 高設定濃厚（G数・合算・RBがすべて条件を満たす） |
| ○ | 高設定有望 |
| △ | 要注目（合算またはRBどちらかが基準超え） |
| × | 低設定寄り |
| blank | G数不足（判定不可） |

---

## 今後の予定

- [x] Mac launchd による毎日の自動実行
- [ ] GitHub Actions による毎日の自動実行（クラウド化）
- [ ] 表示画面のデザイン改善（グラフ表示など）
- [ ] 差枚数・合成確率の推移グラフ
