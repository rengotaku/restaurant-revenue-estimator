# ARCHITECTURE — restaurant-revenue-estimator

飲食店 1 店舗を「独立した個人事業」として切り出し、公開情報・現地観察・統計・仮定から売上と損益を**根拠付きのシナリオ**として算出して、Web 画面と評価レポートで見せるシステム。

> これは予測モデルではない。実績データで誤差を測るまでは、出力は「この仮定ならこうなる」というシナリオであり、精度や信頼区間を名乗らない（[元の spec](spec/2026-10-30-original-spec.md) 0 章・11 章・16 章）。

関連ドキュメント:

- [元の spec](spec/2026-10-30-original-spec.md) — 調査記録・要件・数理モデル・検証計画（以下 spec）
- [spec の再検証レポート](research/2026-10-30-spec-verification.md) — spec の数値と公開情報を一次情報で確かめた結果（FND-n で参照）
- [ADR 一覧](adr/README.md)

> 2026-10-31 時点でリポジトリにあるのは my-boilerplate `python-web` の雛形とこの設計だけで、計算エンジンと画面は未実装。下の構成は実装後の姿を書いている。

## 1. 全体像

```text
data/stores/<store_id>/*.yaml   事実・観察・仮定（git で版管理。画面からは書き換えない）
data/calendars/*.csv            内閣府の祝日 CSV（同梱）
data/benchmarks/*.yaml          公庫の経営指標・POS の参考値（照合用）
        │
        v
[inputs]   読み込み・スキーマ検証・店舗同一性チェック・シナリオ上書きの適用
        │   UNRESOLVED は候補に展開する。null を 0 にしない
        v
[engine]   calendar → tables(卓シミュレーション) → lunch / dinner → revenue → cost → pnl → scenario
        │   純粋関数。DB にも HTTP にも触らない
        v
[services/evaluation.py]  入力ハッシュ・seed・モデル版を付けて 1 回の評価を実行する
        │
        ├──> [cli]    rre validate / rre run / rre report / rre backtest
        ├──> [report] Markdown + JSON の評価レポート
        └──> [web]    FastAPI + Jinja2 + Alpine.js 画面 ──> SQLite（シナリオと実行履歴だけ）
```

## 2. 技術構成

| 項目 | 採用 | 理由 |
|---|---|---|
| 雛形 | rengotaku/my-boilerplate `python-web`（FastAPI・Jinja2・Alpine.js・Tailwind CSS・SQLModel/Alembic・uv・ruff・mypy・pytest） | 計算エンジンと画面を Python 1 本にまとめる |
| 追加依存 | pydantic v2（入力スキーマ）、PyYAML、numpy（乱数 Generator）、Typer（CLI）、Chart.js（npm で入れて Alpine と同じく static へコピー） | 入力の型検証、seed 固定の乱数、CLI、グラフ |
| 事実の保存 | `data/` 配下の YAML を git で版管理 | 変更理由が commit に残る（[ADR 0002](adr/0002-facts-in-yaml-scenarios-in-sqlite.md)） |
| 画面で作った試算 | SQLite の `scenarios` と `runs` | 画面からは事実を書き換えない（ADR 0002） |
| 公開範囲 | `127.0.0.1` で待ち受け、ログインなし | 個人の PC で使う。雛形の認証・ユーザー機能は削除する |
| シミュレーション | 1 分刻みの時間進行型（自前実装） | spec 6.4 の擬似コードがそのまま書け、単体テストしやすい（[ADR 0003](adr/0003-time-step-table-simulation.md)） |
| 金額 | 円は `int`、比率は `Decimal` | 丸めによる集計差（v5 の 2 万円差）を構造的に防ぐ |

## 3. ディレクトリ

```text
src/restaurant_revenue_estimator/
  evidence/      根拠付きの値（ProvenancedValue）、SourceStatus、出典台帳
  inputs/        YAML 読み込み、検証、店舗同一性チェック、シナリオ上書きの適用、入力ハッシュ
  engine/
    calendar/    営業日（定常年 / 暦年）
    tables/      卓シミュレーション
    lunch/       昼のモデル
    dinner/      夜の需要・予約・コース構成
    observation/ 観察との整合（候補探索）
    revenue/     売上恒等式と集計
    cost/        人件費シフト・原価・家賃・その他
    pnl/         損益段階・CF・ベンチマーク照合
    scenario/    3 シナリオ・感度・調査優先順位
  report/        Markdown / JSON の評価レポート
  services/      evaluation.py（評価の実行）、scenario_service.py
  repositories/  SQLite（scenarios / runs）
  routes/        画面と API
  cli.py         rre コマンド
templates/       Jinja2 テンプレート（画面ごと）
data/            入力（3.2 節）
runs/            CLI の出力（git 管理外）
```

## 4. データモデル

### 4.1 根拠付きの値

すべての入力値は次の形で持つ（spec 0.1）。

```yaml
lunch_close_at:
  value: "13:30"
  unit: time
  status: PUBLIC_VERIFIED        # OBSERVED / PUBLIC_VERIFIED / PUBLIC_REPORTED / ASSUMPTION / DERIVED / UNRESOLVED
  sources: [S01]                 # sources.yaml の ID
  effective_from: 2026-10-30
  note: "食べログ 営業時間欄"
floor2_seats:
  value: null
  unit: seats
  status: UNRESOLVED
  candidates: [43, 13]           # 65-22 / 35-22。UNRESOLVED は候補が無いと実行できない
  note: "本店 65 席の解釈が未確定（検証レポート FND-3）"
```

**表1: ステータスごとの必須項目（`rre validate` が検査する）**

| ステータス | 必須項目 |
|---|---|
| PUBLIC_VERIFIED / PUBLIC_REPORTED | `sources` が 1 件以上。出典に `accessed_at` |
| OBSERVED | `observation_id`（observations.yaml）。観察の時刻範囲 |
| ASSUMPTION | `justification` と `range`（low / base / high） |
| UNRESOLVED | `value: null` かつ `candidates` が 1 件以上 |
| DERIVED | 入力には書けない（計算結果にだけ付く） |

### 4.2 入力ファイル

```text
data/
  calendars/jp_holidays_cao.csv          # 内閣府 CSV。取得日・URL・sha256 を jp_holidays_cao.meta.yaml に
  benchmarks/jfc_2025_izakaya.yaml       # 公庫 経営指標（定義つき。入力ではなく照合用）
  stores/<store_id>/
    store.yaml        # 店名・住所・電話・外部 ID・関連店舗（姉妹店は relation: sister, included: false）
    sources.yaml      # 出典台帳: id, url, accessed_at, published_at, facts（抜き出した事実）, license_status, body_stored: false
    layout.yaml       # floors / tables（席数・結合可否・相席可否・有効期間）
    menu.yaml         # menu_items / courses（通常価格・割引価格・飲み放題・時間・人数・有効期間）、お通し
    operations.yaml   # 営業ルールの版（曜日・時間・L.O.・最終入店・有効期間）＋ 例外日（年末年始・臨時休業）
    observations.yaml # 現地観察（時刻範囲・階・内容）
    assumptions/{pessimistic,standard,optimistic}.yaml
    legacy/v5.yaml    # 過去の試算（再現・不整合検知用。実績として扱わない）
    actuals/          # 実績（POS）。*.csv + actuals.meta.yaml（出所・ライセンス・欠測）。入力 YAML とは別（ADR 0010）
```

- ページ本文は保存しない。保存するのは抜き出した事実・URL・取得日だけ（spec 4.1、9.4 末尾）
- 店舗同一性: 外部 ID・電話番号・住所のどれかが他店舗と一致したら `validate` がエラーにする（姉妹店の混入防止、spec 2.3）

### 4.3 SQLite（画面で作る試算だけ）

**表2: テーブル**

| テーブル | 列 | 用途 |
|---|---|---|
| `scenarios` | id, store_id, name, base_assumption_set, overrides_json, note, created_at, updated_at | 画面で変えた仮定のまとまり |
| `runs` | id, store_id, scenario_id, assumption_set, calendar_mode, year, seed, replications, input_hash, model_version, code_sha256, calendar_sha256, created_at, result_json | 1 回の評価の記録 |

`result_json` の最上位は `variants`（未確定の候補ごとの結果）と `scenarios`（悲観・標準・楽観 × 候補の年間売上・事業所得相当）。売上・損益・卓シミュレーション・日別の売上・昼の着席人数の時系列・人件費の内訳・費用の内訳に使った値・最低賃金の警告は、各 variant の中にある。#32 より前に保存した run は `variants` を持たず、画面は最上位の中身を 1 件の候補として読む。

シナリオ上書きのルール:

- 上書きできるのは、元のステータスが ASSUMPTION か UNRESOLVED の値だけ。OBSERVED・PUBLIC_VERIFIED・PUBLIC_REPORTED の上書きは API が 422 で拒否する
- 上書きした値は ASSUMPTION になり、出典は `scenario:<id>` になる
- 事実を直したいときは YAML を直して PR にする

## 5. 計算モデル

### 5.1 営業日（engine/calendar）

- `steady`: 現在有効な営業ルールを対象年の 12 か月に当てはめた仮想の定常年
- `historical`: 有効期間つきの営業ルールで、その年に実際に営業した日。開始日が UNRESOLVED のルール（バードマンの土曜定休の開始年。FND-4）は候補ごとに系列を出す
- 祝日は同梱 CSV から。年末年始・お盆・臨時休業は**例外日として 1 日ずつ**ステータス付きで持つ（v5 が記載なしで年末年始を休みにしていた問題への対策。FND-2）

### 5.2 卓シミュレーション（engine/tables）

1 分刻みで、到着 → 待ち行列 → 離脱判定 → 卓割当 → 厨房容量チェック → 着席（滞在時間を抽選）→ 退店 → 片付け を処理する。
昼の滞在時間は、前提に比（`lunch_stay_ratio_by_group_size`）があれば組の人数ごとに平均滞在とばらつきを変えて抽選する（1 人客が早く帰り、平均滞在は維持される）。
昼の到着組数は、前提に倍率（`lunch_arrival_weekday_factor`）があれば平日の平均組数に掛けて曜日ごとの組数にする（月曜が少なく金曜が多い形を再現する）。

**表3: 卓シミュレーションの要素**

| 要素 | 扱い |
|---|---|
| 到着 | 5 分ごとの到着率 λ(t)（グループ/5 分）のポアソン到着 |
| グループ人数 | カテゴリ分布 P(1..6+) |
| 卓割当 | 待ち行列の先頭から、入れる卓のうち席数が最小のもの。卓の結合・相席は設定で切替（既定 off） |
| 階の開放 | 階ごとに「常時」「下の階が満卓のとき」「使わない」 |
| 離脱 | 到着時に行列が閾値を超えていたら入店しない／待ち時間が上限を超えたら帰る |
| 厨房 | 5 分あたりの受注上限。超えたら着席を止める |
| 予約 | 開始時刻・人数・占有時間・必要卓を持つブロック。開始前は卓を空けて待つ（夜で使う） |
| 出力 | 入店客数、失った需要、卓・席の稼働、時刻ごとの行列長、階ごとの入店数 |

同じ seed と入力なら出力は完全一致する。複数回（replications）回して平均と範囲を出す。

### 5.3 観察との整合（engine/observation）

2 階の卓構成候補 × 到着率 × 滞在時間のグリッドを回し、観察（12:00〜12:30 に 1 階 6 卓が全部使用中、行列 5〜10 人、2 階への案内 1 件以上）を再現できた組合せを**候補として全部**出す。単一の正解を宣言しない（spec 6.5、Phase B 完了条件）。

### 5.4 夜（engine/dinner）

- 需要 = 曜日ベース × 月の季節係数 × 特定日の効果（spec 6.6）
- コース客の比率 = ロジスティック（月・曜日・人数）。コース客とアラカルト客は排他
- 団体予約は複数卓を 120／150 分のブロックで確保する（卓シミュレーションの予約）
- 夜の客数も卓シミュレーションに通し、卓が足りずに入れなかった分を失った需要として出す（[ADR 0005](adr/0005-dinner-through-table-simulation.md)）
- 予約画面の「×」を満席や需要に変換しない（spec T16、T24）

### 5.5 売上（engine/revenue）

spec 6.1 の恒等式。守ること:

- お通しは店舗設定（バードマン 450 円・コース客は免除、とら八 0 円。FND-6、FND-7）
- コース価格は通常価格・割引価格・割引の適用率に分ける（FND-5）
- 飲み放題付きコースにドリンク売上を足さない
- 季節係数は客数かコース比率のどちらかにだけ掛ける。売上全体への再適用はエラー（spec T15）
- 税込→税抜は税区分ごと（店内飲食・酒類 10%、持ち帰り食品 8%）
- 集計は円の整数のまま。丸めは表示のときだけ

### 5.6 費用と損益（engine/cost、engine/pnl）

**表4: 損益の段階**

| 段階 | 内容 |
|---|---|
| 税込売上 → 税抜売上 | 税区分ごと |
| 粗利 | 税抜売上 − 食材原価 − 飲料原価（アラカルトとコースで別の原価率を持てる） |
| 店舗営業利益 | 粗利 − 人件費 − 家賃 − その他販管費 − 減価償却 − 独立化代替費（本部機能を自前でまかなう費用。spec 1.2） |
| 事業所得相当 | 店舗営業利益 − 支払利息。店主の給与は含めない |
| 店主の労働対価（別列） | 店主の労働時間 × 仮想時給。事業所得相当と合算して表示しない |
| CF | 事業所得相当 + 減価償却 − 借入元本返済 − 設備投資 |

人件費: 正社員（職種別のユニーク人数 × 月給）＋ アルバイト（仮定 `part_time_shifts` のシフト表 × 時給、22:00〜5:00 は 25% 以上の割増）＋ 賞与・法定福利・通勤費。付帯費率を一括で足す場合は個別積算と二重にしない（spec T09、T10）。

- アルバイトのシフトは、営業日ごとに曜日と営業サービスで絞って数える（昼が休みの日は昼のシフトを数えない）
- 法定福利は（正社員の給与 ＋ 賞与 ＋ アルバイトの賃金）× 率。通勤費には掛けない
- 最低賃金は数えたシフトの日付ごとに比べ、下回るシフトを警告にする（東京都は 2026-10-01 から 1,280 円）
- 原価は「アラカルト（お通しを含む）の税抜 × 原価率 ＋ コースの税抜 × 原価率」
- 損益は月ごとに作る。各月の費用（人件費の 5 つの内訳・原価・固定費）を円に丸めてから月の損益を作り、年の値はその和にする（[ADR 0006](adr/0006-labor-from-shifts-and-monthly-pnl.md)）

公庫の経営指標（酒場・ビヤホール）は入力に使わない。推計結果を公庫の定義（税込売上が分母、人件費に経営者報酬を含む）に換算して比べる照合だけに使う（[ADR 0004](adr/0004-jfc-ratios-as-benchmark-only.md)、FND-9）。照合の計算は `engine/pnl` の `jfc_comparison`・`jfc_benchmark_rows` で行い、公庫の値は `data/benchmarks/jfc_2025_izakaya.yaml` から読む。

税引後の手取り（所得税・住民税・事業税）は MVP の対象外。

### 5.7 シナリオと感度（engine/scenario）

- 1 回の実行（`services/evaluation.py` の `evaluate_run`）で、未確定の候補の組合せをすべて計算し、悲観／標準／楽観の 3 つの仮定セットを同じ seed・replications で回す（[ADR 0008](adr/0008-all-variants-and-three-scenarios-per-run.md)）
  - steady は 2 階の候補（バードマン 3 通り）、historical は 2 階 × 土曜定休の開始日 × 土曜の営業時間（バードマン 12 通り）。上書きで固定した軸は振らない
- 感度: ASSUMPTION と UNRESOLVED の各入力を 1 つずつ low/high（UNRESOLVED は各候補）に振り、年間売上と事業所得相当の振れ幅で並べる。上位から「次に調べるべきもの」として出す（spec 1.1-7、12 章）。行ごとに、基準の候補の 3 つの軸をすべて固定して 1 候補だけを計算する
- 相関付きモンテカルロと P10–P90 表示は、実績で校正できるまで出さない（spec 6.10）

### 5.8 実績との比較（actuals/, `rre backtest`）

- `data/stores/<store_id>/actuals/` の日次売上（税込）と、`rre run` の `result.json` の日次売上を突き合わせる（[ADR 0010](adr/0010-backtest-against-pos-actuals.md)）
- 比較するのは重なる日だけ。指標は MAE・中央値絶対誤差・バイアス（予測 − 実績）・相対誤差（合計）
- 悲観・標準・楽観の 3 シナリオは予測区間ではないので、**区間被覆率は出さない**。replications の分布を日次で保存してから足す
- 実績は入力（事実・仮定）に混ぜない。`ProvenancedValue` の検査対象にもしない

## 6. 出力

### 6.1 評価レポート（report）

`rre run data/stores/<store_id> --assumptions standard --year 2026 --calendar steady --seed 42` で `runs/<run_id>/` に `result.json` と `report.md` を出す。画面の実行結果も同じ形式でダウンロードできる。

章立て:

1. 冒頭の注記（シナリオであって実績でも予測区間でもない／未確定の入力の一覧）
2. 店舗と根拠の時点（外部 URL、姉妹店を含めていないこと）
3. 年間・月別の売上（昼／夜、アラカルト／コース、税込／税抜、3 シナリオ）
4. 卓シミュレーション（入店客数、失った需要、稼働、観察を再現できた候補）
5. 費用・損益段階・CF・店主の労働対価（別列）
6. 公庫ベンチマークとの照合
7. 感度と調査の優先順位
8. 根拠一覧（全入力のステータス・出典 URL・取得日）

表示ルール（テストで検査する）: 「信頼度」「精度」「±%」「信頼区間」の語を出さない。DERIVED の値には、元になった入力のうち最も弱いステータスを併記する。

### 6.2 画面（routes / templates）

spec 10.1 の 9 画面に対応する。

**表5: 画面**

| 画面 | URL | 中身 |
|---|---|---|
| 店舗一覧 | `/` | 登録店舗と最新の評価 |
| 店舗サマリー | `/stores/{id}` | 外部 URL、姉妹店を含めていないこと、営業期間、根拠の時点、ステータス別の入力件数 |
| 売上内訳 | `/stores/{id}/revenue` | 年・月・日、昼／夜、アラカルト／コース、税込／税抜、3 シナリオ |
| 座席・卓 | `/stores/{id}/layout` | 階ごとの卓（事実）とシナリオでの上書き。物理席と有効着席人数を並べる |
| シミュレーター | `/stores/{id}/simulator` | 仮定値を変える → 再計算 → シナリオとして保存 |
| 人件費・費用 | `/stores/{id}/costs` | 職種・人数・時給・シフト・割増・家賃・原価、月次の損益段階 |
| 年カレンダー | `/stores/{id}/calendar` | 営業日（定常年／暦年）、例外日、ルールの切替日 |
| 根拠・監査 | `/stores/{id}/evidence` | 全入力のステータス・出典 URL・取得日、UNRESOLVED の一覧 |
| 改善優先順位 | `/stores/{id}/priorities` | 感度の上位と、それぞれの確かめ方 |
| 比較 | `/compare` | 複数店舗・複数シナリオを同じ期間・同じ費用基準で並べる（順位は付けない） |

API（画面から呼ぶ）:

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/api/stores` | 店舗一覧 |
| GET | `/api/stores/{id}` | 入力とステータス |
| POST | `/api/stores/{id}/runs` | 評価を実行（scenario_id か overrides、assumption_set、calendar_mode、year、seed、replications） |
| GET | `/api/runs/{run_id}` | 実行結果 |
| GET | `/api/runs/{run_id}/report.md` | レポートのダウンロード |
| GET / POST | `/api/stores/{id}/scenarios` | シナリオ一覧・作成 |
| GET / PUT / DELETE | `/api/scenarios/{id}` | シナリオの参照・更新・削除 |

同じ入力ハッシュ・seed・replications・計算コードのハッシュの実行結果は `runs` から返す（再計算しない。[ADR 0007](adr/0007-code-hash-in-run-reuse.md)）。

画面は `?variant=` で表示する候補を切り替える。表を組み立てる処理（税抜の配分・年の行の和・費用の内訳の取り出し）は `services/screens.py` に置き、routes には計算を書かない。

## 7. どこを触れば何が変わるか

| 変えたいこと | 触る場所 |
|---|---|
| 店舗の事実・仮定 | `data/stores/<store_id>/*.yaml` |
| 祝日 | `data/calendars/jp_holidays_cao.csv`（年 1 回差し替え） |
| 卓割当・離脱のルール | `src/restaurant_revenue_estimator/engine/tables/` |
| 夜の需要式・コース比率 | `src/restaurant_revenue_estimator/engine/dinner/` |
| 損益の段階・費目 | `src/restaurant_revenue_estimator/engine/pnl/`、`engine/cost/` |
| レポートの章立て・表示ルール | `src/restaurant_revenue_estimator/report/` |
| 画面 | `src/restaurant_revenue_estimator/routes/`、`templates/` |
| 画面の表の組み立て（税抜の配分・日別の表・費用の内訳） | `src/restaurant_revenue_estimator/services/screens.py` |
| 候補の組合せ・3 シナリオ・1 回の実行の結果の形 | `src/restaurant_revenue_estimator/services/evaluation.py`（`evaluate_run`） |
| 人件費のシフト・最低賃金の警告・月次の損益 | `src/restaurant_revenue_estimator/engine/cost/`、`engine/pnl/` |
| テストで評価結果を使い回す対象のファイル | `tests/eval_cache.py` の `TARGET_FILES`・`EXCLUDED_TESTS`（[ADR 0009](adr/0009-reuse-evaluations-in-tests.md)） |

## 8. MVP の対象外

- 外部サイト・API からの自動収集（spec 4 章の利用条件を満たす確認が先。[ADR 0001](adr/0001-scenario-calculator-with-manual-inputs.md)）
- 実績データによる精度検証の本番運用（受け口と突合は `rre backtest` で実装済み。対象店舗の実績が揃ってから）
- 税引後手取りの計算
- 相関付きモンテカルロ・確率的な区間表示
