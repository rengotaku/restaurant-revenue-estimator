---
adr: 0007
title: 保存済みの実行結果を再利用する判定に、計算コードのハッシュを足す
status: accepted
superseded_by: null
date: 2026-11-01
issues: [36, 41]
tags: [runs, cache, reuse, code-hash]
description: runs に計算コードの sha256 を保存し、同じ入力・seed・replications でもコードが違えば再利用せず計算し直す
---

# ADR 0007: 保存済みの実行結果を再利用する判定に、計算コードのハッシュを足す

## 背景

同じ入力ハッシュ・seed・replications の実行結果は `runs` から返して計算し直さない。計算コードを直しても入力が同じなら古い結果が返り、画面に修正前の数字が出続けた。

## 決定

- `runs.code_sha256` に計算コードの sha256 を保存する
- 再利用の判定（`find`）と、画面の既定の実行を選ぶ判定（`latest_default`）に、計算コードのハッシュの一致を足す

## 捨てた案

- `model_version`（パッケージのバージョン）で判定する案: コードを直してもバージョンを上げ忘れると効かない

## 守らせ方

- `tests/test_code_version.py`（ハッシュが一致すれば再利用し、変われば作り直す）

## 変えてよい前提 / 壊すと危ない前提

- 変えてよい: ハッシュを取る対象のファイルの範囲
- 壊すと危ない: `?run=<id>` で明示した過去の run は、コードが違っても保存時の結果をそのまま出す。過去の形の `result_json` を画面が読めるようにしておく必要がある（#32・#33 で互換の読み方を足した）
- `latest_default` が `input_hash` を見ていない件は #38
