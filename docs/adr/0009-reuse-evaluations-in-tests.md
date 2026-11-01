---
adr: 0009
title: 画面系のテストでは、同じ条件の評価結果を使い回して CI を短くする
status: accepted
superseded_by: null
date: 2026-11-01
issues: [42, 44, 46, 47]
tags: [tests, ci, cache, evaluation]
description: tests/eval_cache.py の CachedEvaluate で、対象のテストファイルでは同じ条件の evaluate の結果を 1 回目のものを deepcopy して返す
---

# ADR 0009: 画面系のテストでは、同じ条件の評価結果を使い回して CI を短くする

## 背景

GitHub の CI が 80 分を超えていた。画面系のテストがそれぞれ replications 20 の評価とレポートの感度分析をやり直していたのが主な原因だった。

## 決定

- `tests/eval_cache.py` の `CachedEvaluate` で `evaluate` を包み、同じ条件（データの場所・リクエスト・入力ハッシュ）なら 1 回目の結果を deepcopy して返す
- `tests/conftest.py` の autouse fixture が、`TARGET_FILES` のテストファイルで `PATCH_TARGETS` の各モジュールの `evaluate` を差し替える。計算そのものを確かめるテストは `EXCLUDED_TESTS` に入れて外す
- あわせて、計算結果を変えない高速化（#46）と、カバレッジを行単位にして sys.monitoring で測る変更（#47）で、CI は 3 分台になった

## 捨てた案

- pytest-xdist で 2 並列にする案: 2 コアでは並列なしより遅かった（評価の使い回しがワーカーごとに分かれる）

## 守らせ方

- `tests/test_eval_cache.py`

## 変えてよい前提 / 壊すと危ない前提

- 変えてよい: `TARGET_FILES` に入れるファイル
- 壊すと危ない: 新しいモジュールから `evaluate` を呼ぶときは、そのモジュールの `evaluate` を `PATCH_TARGETS` に足す。足さないと使い回しが効かない（#32 の `evaluate_run` で足した）
- 評価結果が大きくなると deepcopy が重くなる。#33 で 1 回 0 ms から 13 ms になり、CI のテスト工程が 4 分半から 8 分に延びた（#53）
