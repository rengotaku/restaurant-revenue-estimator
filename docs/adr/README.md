# ADR 索引

<!-- generate-adr-index.zsh による自動生成。手で編集しない -->

| ADR | タイトル | status | date | 要旨 |
|---|---|---|---|---|
| [0001](0001-scenario-calculator-with-manual-inputs.md) | 根拠付きシナリオ算出器として作り、入力は手入力の YAML に限る（自動収集しない） | accepted | 2026-10-31 | MVP は予測モデルではなく根拠付きシナリオ算出器。外部サイト・API の自動収集はせず、出典つきの手入力 YAML から計算する |
| [0002](0002-facts-in-yaml-scenarios-in-sqlite.md) | 事実は YAML（git）、画面で作る試算は SQLite のシナリオに分けて保存する | accepted | 2026-10-31 | 根拠のある事実・観察・基準の仮定は git 管理の YAML、Web 画面で変えた値はシナリオとして SQLite に保存し、画面からは事実を書き換えない |
| [0003](0003-time-step-table-simulation.md) | 昼夜の処理能力は 1 分刻みの卓シミュレーションで求め、観察を再現できた候補を全部出す | accepted | 2026-10-31 | 「営業時間÷滞在時間＝回転数」の近似をやめ、到着・待ち・離脱・卓割当・厨房を 1 分刻みで回す自前の卓シミュレーションを使う。SimPy は使わない |
| [0004](0004-jfc-ratios-as-benchmark-only.md) | 公庫の業種別経営指標（原価率・人件費率）は入力に使わず照合だけに使う | accepted | 2026-10-31 | 日本政策金融公庫「小企業の経営指標調査」の酒場・ビヤホールの比率は分母と人件費の定義が本システムと違うため、売上に掛けず、推計結果を同じ定義に換算して比べるだけにする |
| [0005](0005-dinner-through-table-simulation.md) | 夜の客数も卓シミュレーションに通し、入れなかった分を失った需要として出す | accepted | 2026-11-01 | 夜は需要をそのまま客数にせず、昼と同じ 1 分刻みの卓シミュレーションに通して、卓が足りずに入れなかった分を失った需要として出す |
| [0006](0006-labor-from-shifts-and-monthly-pnl.md) | 人件費はシフト表から積み上げ、原価は税抜売上に掛け、損益は月ごとに円へ丸めて年を月の和にする | accepted | 2026-11-01 | アルバイトはシフト表（曜日・昼夜・時間帯・人数・時給）から数え、原価はお通しを含む税抜売上に掛け、損益は月ごとに費用を円に丸めてから作り年の値をその和にする |
| [0007](0007-code-hash-in-run-reuse.md) | 保存済みの実行結果を再利用する判定に、計算コードのハッシュを足す | accepted | 2026-11-01 | runs に計算コードの sha256 を保存し、同じ入力・seed・replications でもコードが違えば再利用せず計算し直す |
| [0008](0008-all-variants-and-three-scenarios-per-run.md) | 1 回の実行で未確定の候補をすべて計算し、悲観・標準・楽観を同じ条件で回す | accepted | 2026-11-01 | 実行のたびに UNRESOLVED の候補の組合せ（variant）をすべて評価し、悲観・標準・楽観を同じ seed・replications で回して result.json の variants と scenarios に入れる |
| [0009](0009-reuse-evaluations-in-tests.md) | 画面系のテストでは、同じ条件の評価結果を使い回して CI を短くする | accepted | 2026-11-01 | tests/eval_cache.py の CachedEvaluate で、対象のテストファイルでは同じ条件の evaluate の結果を 1 回目のものを deepcopy して返す |
