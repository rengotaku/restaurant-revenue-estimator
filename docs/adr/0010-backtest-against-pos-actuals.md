---
adr: 0010
title: 実績（POS）は店舗ディレクトリの actuals に置き、日次売上だけでシナリオと突き合わせる
status: accepted
superseded_by: null
date: 2026-11-03
issues: []
tags: [actuals, backtest, validation, pos, phase-f]
description: 実績は data/stores/<store_id>/actuals/ に CSV とメタ情報で置き、rre backtest が日次売上（税込）だけで MAE・バイアス・相対誤差を出す。3 シナリオは予測区間にしないため被覆率は出さない
---

# ADR 0010: 実績（POS）は店舗ディレクトリの actuals に置き、日次売上だけでシナリオと突き合わせる

## 背景

このシステムは「実績データで誤差を測るまでは精度を名乗らない」と決めているが（[ADR 0001](0001-scenario-calculator-with-manual-inputs.md)）、実績を受け取る口が無かった。`legacy/v5.yaml` は `is_actual: false` が必須で、実績の置き場所にならない。spec の Phase F（V4 実績校正）は未着手のままだった。

手元には 2 種類の POS データがある。

- 北海道オープンデータのレストラン 1 店舗（実データ、71 日、ランチのみ、モバイルオーダーのみ）
- Kaggle の 50 店舗（合成データ）

## 決定

- 実績は `data/stores/<store_id>/actuals/` に置く。`*.csv`（`date,revenue_inc_tax`、任意で `orders` / `guests`）と `actuals.meta.yaml`（`source_id` / `license` / `coverage_note` 必須）
- `rre backtest <store_dir> --run <result.json>` が、実績とシナリオの**日次売上（税込）だけ**を突き合わせる
- 指標は MAE、中央値絶対誤差、バイアス（予測 − 実績）、相対誤差（合計）。重なる日だけで計算し、重なる日が無ければエラー
- 3 シナリオ（悲観・標準・楽観）を予測区間として使わない。**区間被覆率は出さない**（ADR 0008 と同じ理由。帯や誤差棒で見せると予測区間に見える）。replications の分布を日次で保存できるようになってから足す
- 実績は入力 YAML（`ProvenancedValue`）に混ぜない。実績は「値 + 出典」ではなく時系列で、スキーマが違う
- 1 店舗・1 期間の比較では精度を主張しない。レポートの冒頭にその注記を必ず出す

## 捨てた案

- `legacy/` に実績フラグを足す案: legacy は「過去の試算であり実績ではない」という前提で作られており、混ぜると `is_actual: false` の検査が意味を失う
- 客数・客単価まで比較する案: 実績側に組数・人数が無いことが多い（北海道のデータも会計単位しか無い）
- 3 シナリオで区間被覆率を出す案: 予測区間ではないものを区間として扱うことになる
- 実績を `ProvenancedValue` で持つ案: 日次の系列にステータスと出典を 1 行ずつ付ける形になり、`validate` の検査対象が実績にまで広がる

## 守らせ方

- `tests/test_actuals.py`: メタ情報の必須キー、日付の重複、負の売上、未知の列で停止する
- `tests/test_backtest.py`: 指標の計算、重なる日だけの比較、重なる日が無いときのエラー、相対誤差の 0 除算
- `tests/test_cli.py`: `rre backtest` の Markdown / `--json` / 終了コード

## 変えてよい前提 / 壊すと危ない前提

- 変えてよい: 指標の追加（RMSE など）、CSV の任意列、`--out` の出力先
- 壊すと危ない: 実績を入力の解決（UNRESOLVED の候補を 1 つに絞る）に使わないこと。使うと「実績で校正した」と「実績を見て仮定を選んだ」の区別がつかなくなる。校正するなら、fit 期間と test 期間を分けた別の決定として行う
- 壊すと危ない: 合成データ（Kaggle の 50 店舗）を精度の根拠にしないこと
