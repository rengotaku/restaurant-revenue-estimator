"""spec md の算術・暦の主張を再計算する検証スクリプト。

実行: python3 -I docs/research/verify_spec_arithmetic.py "$TMPDIR/syukujitsu.csv"
"""
import calendar
import csv
import datetime as dt
import sys
from decimal import Decimal


def load_holidays(path):
    with open(path, encoding="shift_jis") as f:
        rows = list(csv.reader(f))[1:]
    out = {}
    for d, name in rows:
        y, m, dd = map(int, d.split("/"))
        if y == 2026:
            out[dt.date(y, m, dd)] = name
    return out


def check(label, got, expected):
    ok = got == expected
    print(f"[{'OK' if ok else 'NG'}] {label}: got={got} spec={expected}")
    return ok


def main(holiday_csv):
    hol = load_holidays(holiday_csv)

    print("== 2.1 卓・席の算術 ==")
    check("物理席 5x4+1x2", 5 * 4 + 1 * 2, 22)
    check("全卓2人組 6x2", 6 * 2, 12)
    check("物理席利用率 12/22 (%)", round(12 / 22 * 100, 1), 54.5)
    check("満卓時平均 5x2.4+1x1.7", round(5 * 2.4 + 1 * 1.7, 1), 13.7)
    check("65-22", 65 - 22, 43)

    print("== 2.2 人件費試算 ==")
    seishain = 5 * 350_000
    baito = 8 * 5 * 30 * 1330
    check("アルバイト月額", baito, 1_596_000)
    total = Decimal(seishain + baito) * Decimal("1.15")
    check("(175+159.6)x1.15 万円", total / 10000, Decimal("384.79"))

    print("== 3.1 v5 月別表 ==")
    days = [19, 18, 21, 21, 18, 22, 22, 20, 19, 21, 19, 20]
    lunch = [156, 156, 202, 200, 162, 205, 208, 159, 178, 200, 181, 190]
    dinner = [348, 349, 522, 523, 376, 462, 517, 365, 419, 504, 460, 540]
    month = [504, 505, 724, 723, 538, 667, 725, 524, 597, 703, 641, 729]
    check("営業日合計", sum(days), 240)
    check("昼合計", sum(lunch), 2197)
    check("夜合計", sum(dinner), 5385)
    check("月商列合計", sum(month), 7580)
    check("昼+夜", sum(lunch) + sum(dinner), 7582)
    for i, (l, d, m) in enumerate(zip(lunch, dinner, month), 1):
        if l + d != m:
            print(f"  [DIFF] {i}月: 昼{l}+夜{d}={l + d} ≠ 月商{m} (差{l + d - m})")

    print("== 3.1 v5 仮定営業日 vs 2026暦（土日祝休業） ==")
    total_wd = 0
    for m in range(1, 13):
        n_days = calendar.monthrange(2026, m)[1]
        wd = [dt.date(2026, m, d) for d in range(1, n_days + 1)]
        wd = [d for d in wd if d.weekday() < 5 and d not in hol]
        total_wd += len(wd)
        mark = "OK" if len(wd) == days[m - 1] else "NG"
        print(f"  [{mark}] {m:2d}月: 暦上の平日(祝除く)={len(wd):2d} v5={days[m - 1]:2d} 差={days[m - 1] - len(wd):+d}")
    print(f"  年合計: 暦={total_wd} v5={sum(days)}")

    print("== 3.3 営業余剰 ==")
    check("7580/1.1 (万円, 小数1桁)", round(7580 / 1.1, 1), 6890.9)
    check("6891-2274-2343-960-758", 6891 - 2274 - 2343 - 960 - 758, 556)
    net = 7580 / 1.1
    print(f"  参考: 原価2274/税抜={2274 / net:.3f} 人件費2343/税抜={2343 / net:.3f} "
          f"家賃960/税抜={960 / net:.3f} その他758/税抜={758 / net:.3f}")
    print(f"  参考: 家賃960万円/年 = {960 / 12:.0f}万円/月")

    print("== 3.4 感度 ==")
    check("10x5000x240 (万円)", 10 * 5000 * 240 / 10000, 1200)
    check("30x95x240 (万円)", 30 * 95 * 240 / 10000, 68.4)
    check("6891x0.05 (万円)", round(6891 * 0.05, 1), 344.6)

    print("== 3.2 昼人数 ==")
    check("90/40", 90 / 40, 2.25)
    print(f"  38x2.25={38 * 2.25} (spec: 85人)")
    print(f"  1F 13.7→14 の丸め: 14+24={14 + 24}")

    print("== 8.1 観測条件数 ==")
    check("3x3x3x3", 3 ** 4, 81)
    check("81x4", 81 * 4, 324)
    check("324x2", 324 * 2, 648)

    print("== v4 ==")
    print(f"  8685/12={8685 / 12:.1f} (spec: 724)")

    print("== 2.2 月30営業日の妥当性（とら八は年末年始休業） ==")
    for m in (1, 2, 12):
        print(f"  {m}月の暦日数={calendar.monthrange(2026, m)[1]}")

    print("== 祝日一覧（2026） ==")
    for d, name in sorted(hol.items()):
        print(f"  {d} {'月火水木金土日'[d.weekday()]} {name}")


if __name__ == "__main__":
    main(sys.argv[1])
