---
adr: 0002
title: 事実は YAML（git）、画面で作る試算は SQLite のシナリオに分けて保存する
status: accepted
superseded_by: null
date: 2026-10-31
issues: []
tags: [storage, sqlite, yaml, scenario, web]
description: 根拠のある事実・観察・基準の仮定は git 管理の YAML、Web 画面で変えた値はシナリオとして SQLite に保存し、画面からは事実を書き換えない
---

# ADR 0002: 事実は YAML（git）、画面で作る試算は SQLite のシナリオに分けて保存する

## 背景

Web 画面で 2 階の卓構成や夜の客数を変えて試算できるようにする（spec 10.1 のシミュレーター・座席編集）。画面で変えた値をどこに残すかで、根拠の監査（spec 0.1・10.1-7）がどこまで成り立つかが変わる。

選択肢は 3 つあった: (a) 事実は YAML、試算は SQLite、(b) 全部 SQLite で画面から編集、(c) 保存しない。岸良さんが (a) を選んだ（2026-10-31）。

## 決定

- 事実・観察・基準の仮定セットは `data/` 配下の YAML に置き、git で版管理する。変更は PR で行い、理由は commit メッセージに書く
- 画面で変えた値は SQLite の `scenarios`（name・base_assumption_set・overrides_json・note）に保存する
- 評価の実行記録は SQLite の `runs`（入力ハッシュ・seed・replications・モデル版・祝日 CSV の sha256・結果 JSON）に保存する
- シナリオで上書きできるのは、元のステータスが ASSUMPTION か UNRESOLVED の値だけ。OBSERVED・PUBLIC_VERIFIED・PUBLIC_REPORTED の上書きは API が 422 で拒否する。上書きした値は ASSUMPTION（出典 `scenario:<id>`）になる
- 雛形に含まれるユーザー・認証・items のテーブルは削除する。待ち受けは 127.0.0.1、ログインなし

## 捨てた案

- **全部 SQLite で画面から編集**: 事実の変更履歴が git に残らず、監査用の変更履歴テーブルを別に作る必要がある
- **保存しない**: 画面で作った「2 階 6 卓案」と「2 階 3 卓案」を比較画面で並べられない
- **シナリオも YAML に書き出す**: 画面からの保存のたびに作業ツリーが汚れる

## 変えてよい前提 / 壊すと危ない前提

- **変えてよい**: テーブルの列の追加、シナリオの命名規則、runs の保持期間
- **壊すと危ない**: 「画面からは事実を書き換えない」「上書きした値は ASSUMPTION になる」。これが崩れると、レポートの根拠一覧に画面で入れた値が公開情報として載ってしまう
