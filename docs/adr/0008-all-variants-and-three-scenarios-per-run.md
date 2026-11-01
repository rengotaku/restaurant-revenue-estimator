---
adr: 0008
title: 1 回の実行で未確定の候補をすべて計算し、悲観・標準・楽観を同じ条件で回す
status: accepted
superseded_by: null
date: 2026-11-01
issues: [32, 50]
tags: [variants, scenarios, unresolved, run, result-json]
description: 実行のたびに UNRESOLVED の候補の組合せ（variant）をすべて評価し、悲観・標準・楽観を同じ seed・replications で回して result.json の variants と scenarios に入れる
---

# ADR 0008: 1 回の実行で未確定の候補をすべて計算し、悲観・標準・楽観を同じ条件で回す

## 背景

Web の実行も `rre run` も先頭の候補（2 階 f2_42）の結果しか出しておらず、ほかの 2 階の構成や暦年の土曜定休の候補が画面にもレポートにも無かった（要件レビュー RV-5）。3 シナリオは計算する関数があっても画面とレポートに出ていなかった（RV-6）。

## 決定

- 1 回の実行（`evaluate_run`）で、UNRESOLVED の候補の組合せをすべて評価する。steady は 2 階の候補、historical は 2 階 × 土曜定休の開始日 × 土曜の営業時間。上書きで固定した軸は振らない（要件レビュー後に合意した OPT-5）
- 実行のたびに悲観・標準・楽観を、同じ seed・replications・年・暦・上書きで回す（OPT-6）
- `result.json` の最上位は `variants` と `scenarios`。1 候補ぶんの中身（`result_to_dict`）は各 variant の中に残す。1 回の実行は `runs` の 1 行のまま
- 感度分析の各行は、基準の候補の 3 つの軸をすべて固定して 1 候補だけを計算する

## 捨てた案

- 候補から代表値を 1 つ選んで出す案: spec T17（UNRESOLVED を代表値にまとめない）に反する
- 3 シナリオを帯グラフや誤差棒で見せる案: 予測区間に見える（spec 6.10）

## 守らせ方

- `tests/test_variants.py`（TC-F6-01〜11。候補の数・組合せの重複なし・上書きで固定した軸・3 シナリオの単調性）
- 暦年モードの感度分析が行ごとに 1 候補だけを計算することの追加テスト

## 変えてよい前提 / 壊すと危ない前提

- 変えてよい: variant の名前の付け方、`scenarios` に入れる項目
- 壊すと危ない: 実行 1 回の時間は 3 シナリオぶん延びる（標準・steady・replications 20 で、バードマン 6.3→9.5 秒、とら八 8.4→10.1 秒）。速くするかは #52 で決める
- 感度分析で候補を固定するとき、variant の名前（暦年は組合せ名）を `layout.floor2` に入れない。入れると固定が効かず、行ごとに全候補を計算する（暦年で 1.8→11.5 秒になったことがある）
