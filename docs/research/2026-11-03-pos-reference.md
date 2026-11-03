# POS 実績データの参照値（北海道オープンデータ）

## 目的

`restaurant-revenue-estimator` の仮定（`assumptions`）に置くレンジの参考値を、実在店舗の POS から測っておく。実績校正（spec Phase F）の第 1 号データ候補でもある。

## 出典

- データ: [レストラン売上データ（令和4年度北海道デジタルチャレンジ推進事業）](https://www.harp.lg.jp/opendata/dataset/1938.html)
- 事業名: スキー場まるごとDX化事業 in ニセコ東急グラン・ヒラフ
- 実施主体: 東急不動産株式会社 / 東急リゾーツ＆ステイ株式会社 / 倶知安町
- ライセンス: CC BY
- 取得日: 2026-11-03

## 測った値

**表1: 参照値**

| 指標 | 値 | 定義 |
|---|---:|---|
| 期間 | 2022-12-20〜2023-02-28（71 日） | データの範囲 |
| 行数 | 40,310 行 | 元 CSV。完全重複 900 行を除去して 39,410 行 |
| 売上合計 | 41,797,100 円 | `決済ステイタス == 実売上` の `金額` 合計 |
| 会計数 | 9,553 | `決済時のアクションID` のユニーク数 |
| 客単価 | 4,375 円 | 売上合計 ÷ 会計数 |
| 日商の中央値 | 597,000 円 | 71 日の中央値。平均 588,692 円、最小 374,860 円、最大 828,100 円 |
| 11〜14 時の構成比 | 94.9% | 時間帯別売上（11〜15 時では 98.9%） |
| 営業時間帯 | 9〜15 時 | 売上が発生した時刻の範囲 |

## データの限界

- **モバイルオーダー経由のみ**。店舗の全売上ではない
- **ランチのみ**。夜のデータが無い
- 1 会計あたりの人数（組数）が無い。客単価は「会計あたり」であって「人あたり」ではない
- 滞在時間・フロア構成・席数が無い。卓シミュレーションの入力には足りない
- 金額が税込か税抜かは明記が無い。**未確認**
- スキー場のレストランという立地で、都市部の居酒屋とは客層も季節性も違う

## 使い方

- 仮定のレンジ（客単価、日商、時間帯の集中）を置くときの**参考値**として使う。ステータスは `ASSUMPTION` のまま
- `rre backtest` の実績として使うなら、店舗の YAML を別途起こし、fit 期間と test 期間を分ける
- そのまま `data/benchmarks/pos_reference_niseko_2023.yaml` に置いてある（入力ではない）

## 使わないこと

- バードマン田町店・虎八秋葉原店の根拠にしない。**別店舗・別業態**
- 予測精度の主張に使わない。1 店舗・1 期間では母数が足りない
- Kaggle の 50 店舗データ（合成）と混ぜない

## 再現手順

```bash
curl -sL -o restaurant_sales.csv \
  "https://www.harp.lg.jp/opendata/dataset/1938/resource/5692/%E3%83%AC%E3%82%B9%E3%83%88%E3%83%A9%E3%83%B3%E5%A3%B2%E4%B8%8A%E3%83%87%E3%83%BC%E3%82%BF.csv"

python3 -c "
import csv, collections, statistics
from datetime import datetime
rows = list(csv.DictReader(open('restaurant_sales.csv', encoding='cp932')))
seen = set(); ded = []
for r in rows:
    k = tuple(r.values())
    if k in seen: continue
    seen.add(k); ded.append(r)
sales = [r for r in ded if r['決済ステイタス'] == '実売上']
print('rows', len(ded), 'sales rows', len(sales))
print('total', sum(int(r['金額']) for r in sales))
print('orders', len({r['決済時のアクションID'] for r in sales}))
daily = collections.defaultdict(int)
for r in sales:
    daily[datetime.strptime(r['決済日時'].strip(), '%Y/%m/%d %H:%M').date()] += int(r['金額'])
vals = sorted(daily.values())
print('days', len(vals), 'median', statistics.median(vals), 'mean', round(sum(vals)/len(vals)))
print('min', min(vals), 'max', max(vals))
"
```

実行結果:

```text
rows 39410 sales rows 38630
total 41797100
orders 9553
days 71 median 597000 mean 588692
min 374860 max 828100
```

## 未確認事項

- 金額の税込・税抜
- 「現金支払」725 行（重複除去後 710 行）が売上に含まれるか。本ドキュメントでは `実売上` のみを集計している
- 1 会計あたりの人数
