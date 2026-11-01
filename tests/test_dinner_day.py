"""夜の 1 日シミュレーション・団体予約テスト (TC-F4-xx)."""

import shutil
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from restaurant_revenue_estimator.engine.calendar import (
    calendars_for_store,
    load_holidays,
)
from restaurant_revenue_estimator.engine.dinner import (
    CourseParty,
    CourseSpec,
    DinnerDayResult,
    allocate_reservations,
    build_course_parties,
    can_extend,
    clear_dinner_day_cache,
    simulate_dinner_day,
)
from restaurant_revenue_estimator.engine.dinner import dinner as dinner_module
from restaurant_revenue_estimator.engine.tables import (
    Arrival,
    Floor,
    ReservationBlock,
    Table,
)
from restaurant_revenue_estimator.inputs import load_store
from restaurant_revenue_estimator.services.evaluation import (
    EvaluationError,
    EvaluationRequest,
    evaluate,
    group_dinner_days,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def test_tc_f4_01_table_capacity_constrains_walk_in() -> None:
    """TC-F4-01: 卓容量が効いて失った需要が出る."""
    tables = [Table("t1", 4, "1f")]
    floors = [Floor("1f", "always")]
    result: DinnerDayResult = simulate_dinner_day(
        tables=tables,
        floors=floors,
        desired_guests=100,
        course_ratio=0.0,
        courses={},
        dinner_stay_minutes=120,
        cleanup_minutes=5,
        open_minute=0,
        last_entry_minute=240,
        close_minute=300,
        replications=1,
        seed=42,
    )
    assert isinstance(result, DinnerDayResult)
    assert result.entered_guests < 100
    assert result.lost_guests > 0


def test_tc_f4_02_conservation_of_guests() -> None:
    """TC-F4-02: 各 replication で 入店＋失った需要＝希望人数."""
    tables = [Table("t1", 4, "1f")]
    floors = [Floor("1f", "always")]
    courses = {
        "c1": CourseSpec("c1", "標準コース", 5000, 120, min_guests=2, max_guests=10),
    }
    result = simulate_dinner_day(
        tables=tables,
        floors=floors,
        desired_guests=40,
        course_ratio=0.5,
        courses=courses,
        dinner_stay_minutes=120,
        cleanup_minutes=5,
        open_minute=0,
        last_entry_minute=240,
        close_minute=300,
        replications=3,
        seed=42,
    )
    assert len(result.replications) == 3
    for rep in result.replications:
        assert rep.walk_in_entered + rep.walk_in_lost == rep.walk_in_desired
        assert rep.course_entered + rep.course_lost == rep.course_desired


def test_tc_f4_05_reservations_block_conflicting_walk_ins() -> None:
    """TC-F4-05: 19:00 のコース予約と重なるウォークインは入店しない."""
    tables = [Table(f"t{i}", 4, "1f") for i in range(1, 4)]
    floors = [Floor("1f", "always")]
    courses = {
        "c1": CourseSpec("c1", "宴会コース", 5000, 120, min_guests=2, max_guests=20),
    }
    result = simulate_dinner_day(
        tables=tables,
        floors=floors,
        desired_guests=12,
        course_ratio=1.0,
        courses=courses,
        course_party_size_distribution={12: 1.0},
        course_start_window={"start": "19:00", "end": "19:00"},
        open_minute=0,
        last_entry_minute=300,
        close_minute=360,
        replications=1,
        seed=42,
        walk_in_arrivals=[Arrival(minute=60, size=2, stay_minutes=90)],
    )
    assert result.course_entered == 12
    assert result.walk_in_entered == 0


def test_tc_f4_06_extension_only_allowed_on_extendable_days() -> None:
    """TC-F4-06: 延長利用率 1.0 のとき、金曜は 120 分、火曜は 150 分."""
    tables = [Table("t1", 4, "1f")]
    floors = [Floor("1f", "always")]
    courses = {
        "c1": CourseSpec("c1", "コース1", 5000, 120, min_guests=2, max_guests=10),
    }
    friday = date(2026, 1, 9)
    tuesday = date(2026, 1, 6)
    holidays: set[date] = set()

    res_fri = simulate_dinner_day(
        tables=tables,
        floors=floors,
        desired_guests=4,
        course_ratio=1.0,
        courses=courses,
        course_party_size_distribution={4: 1.0},
        course_extension_rate=1.0,
        can_extend_today=can_extend(friday, holidays),
        open_minute=0,
        last_entry_minute=240,
        close_minute=300,
        replications=1,
        seed=42,
    )
    res_tue = simulate_dinner_day(
        tables=tables,
        floors=floors,
        desired_guests=4,
        course_ratio=1.0,
        courses=courses,
        course_party_size_distribution={4: 1.0},
        course_extension_rate=1.0,
        can_extend_today=can_extend(tuesday, holidays),
        open_minute=0,
        last_entry_minute=240,
        close_minute=300,
        replications=1,
        seed=42,
    )
    block_fri: ReservationBlock = res_fri.reservations[0]
    block_tue: ReservationBlock = res_tue.reservations[0]
    assert block_fri.end_minute - block_fri.start_minute == 120
    assert block_tue.end_minute - block_tue.start_minute == 150


def test_tc_f4_07_course_capacity_filter() -> None:
    """TC-F4-07: 12 人の組に対して肉三昧（3〜10名）が 100 回中 1 度も選ばれない."""
    courses = {
        "sumibi": CourseSpec(
            "sumibi", "炭火焼鳥会席", 5700, 120, min_guests=2, max_guests=54
        ),
        "oyama": CourseSpec(
            "oyama", "大山どり満喫", 6200, 120, min_guests=2, max_guests=54
        ),
        "nikuzanmai": CourseSpec(
            "nikuzanmai", "肉三昧会席", 7000, 120, min_guests=3, max_guests=10
        ),
    }
    tables = [Table(f"t{i}", 4, "1f") for i in range(1, 4)]
    floors = [Floor("1f", "always")]
    for seed in range(100):
        res = simulate_dinner_day(
            tables=tables,
            floors=floors,
            desired_guests=12,
            course_ratio=1.0,
            courses=courses,
            course_party_size_distribution={12: 1.0},
            open_minute=0,
            last_entry_minute=240,
            close_minute=300,
            replications=1,
            seed=seed,
        )
        assert res.course_entered == 12
        assert res.course_entered_by_course.get("nikuzanmai", 0) == 0


def test_tc_f4_08_party_size_under_three_merged_into_previous() -> None:
    """TC-F4-08: コース希望 14 人、残り 2 人が直前の組に足されて [6, 8] になる."""
    rng = np.random.default_rng(42)
    parties = build_course_parties(
        course_guests=14,
        size_distribution={6: 1.0},
        rng=rng,
    )
    assert parties == [6, 8]


def test_allocate_reservations_assigns_tables_and_reports_unallocated() -> None:
    """公開ヘルパー allocate_reservations と CourseParty の結合・容量チェック."""
    tables = [Table("t1", 4, "1f"), Table("t2", 4, "1f")]
    parties = [
        CourseParty(size=4, course_id="c1", start_minute=60, duration_minutes=120),
        CourseParty(size=6, course_id="c1", start_minute=60, duration_minutes=120),
    ]
    blocks, unallocated = allocate_reservations(parties, tables, cleanup_minutes=5)
    assert len(blocks) == 1
    assert len(unallocated) == 1
    assert unallocated[0].size == 6


def test_tc_f4_03_floor2_capacity_affects_dinner_guests() -> None:
    """TC-F4-03: 2 階席数 f2_12 < f2_42 で夜の入店人数（年）が増える."""
    demand_150 = dict.fromkeys(
        ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], 150
    )
    req_12 = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        year=2026,
        replications=2,
        seed=42,
        overrides={
            "dinner_demand_by_weekday": demand_150,
            "layout.floor2": "f2_12",
        },
    )
    req_42 = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        year=2026,
        replications=2,
        seed=42,
        overrides={
            "dinner_demand_by_weekday": demand_150,
            "layout.floor2": "f2_42",
        },
    )
    res_12 = evaluate(DATA_DIR, req_12)[0]
    res_42 = evaluate(DATA_DIR, req_42)[0]
    assert res_12.dinner_guests < res_42.dinner_guests


def test_tc_f4_04_zero_demand_yields_zero_dinner() -> None:
    """TC-F4-04: 需要 0 で夜の入店 0、失った需要 0、売上 0."""
    demand_0 = dict.fromkeys(
        ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], 0
    )
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        year=2026,
        replications=2,
        seed=42,
        overrides={"dinner_demand_by_weekday": demand_0, "layout.floor2": "f2_42"},
    )
    res = evaluate(DATA_DIR, req)[0]
    assert res.dinner_guests == 0
    assert res.dinner_lost_guests == 0
    assert res.revenue.dinner_inc_tax == 0


def test_tc_f4_09_seasonality_applied_to_guests() -> None:
    """TC-F4-09: 季節係数が guests に適用されている."""
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        year=2026,
        replications=2,
        seed=42,
        overrides={"layout.floor2": "f2_42"},
    )
    res = evaluate(DATA_DIR, req)[0]
    assert res.seasonality_targets == {"dinner_seasonality": "guests"}


def test_tc_f4_10_reservation_site_observation_does_not_affect_evaluation(
    tmp_path: Path,
) -> None:
    """TC-F4-10: 予約サイトの × が入っても客数・売上・損益が変わらない."""
    copy_data = tmp_path / "data"
    shutil.copytree(DATA_DIR, copy_data)
    obs_file = copy_data / "stores" / "birdman_tamachi" / "observations.yaml"
    raw_obs = yaml.safe_load(obs_file.read_text(encoding="utf-8")) or {}
    raw_obs.setdefault("observations", []).append(
        {
            "id": "OBS-900",
            "date": "2026-01-05",
            "source": "S01",
            "facts": ["予約サイトで 2026-01-05 19:00 の枠が ×"],
        }
    )
    obs_file.write_text(yaml.dump(raw_obs, allow_unicode=True), encoding="utf-8")

    req = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        year=2026,
        replications=2,
        seed=42,
        overrides={"layout.floor2": "f2_42"},
    )
    res_orig = evaluate(DATA_DIR, req)[0]
    res_copy = evaluate(copy_data, req)[0]
    assert res_orig.revenue == res_copy.revenue
    assert res_orig.pnl == res_copy.pnl


def test_tc_f4_11_conservation_of_guests_annual() -> None:
    """TC-F4-11: 来店希望人数（年）≥ 入店人数（年）かつ 差分＝失った需要."""
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        year=2026,
        replications=2,
        seed=42,
        overrides={"layout.floor2": "f2_42"},
    )
    res = evaluate(DATA_DIR, req)[0]
    assert res.dinner_desired_guests >= res.dinner_guests
    diff = res.dinner_desired_guests - res.dinner_guests
    assert abs(diff - res.dinner_lost_guests) <= 168


def test_tc_f4_12_deterministic_with_same_seed() -> None:
    """TC-F4-12: 同じ seed で 2 回評価すると完全一致."""
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        year=2026,
        replications=2,
        seed=42,
        overrides={"layout.floor2": "f2_42"},
    )
    res1 = evaluate(DATA_DIR, req)[0]
    res2 = evaluate(DATA_DIR, req)[0]
    assert res1 == res2


def test_regression_identical_parties_allocation() -> None:
    """(a) 4 人卓 1 つ、4 人組 2 つが同じ開始時刻・同じコースのとき、
    入店 4・失った需要 4・予約ブロック 1.
    """
    tables = [Table("t1", 4, "1f")]
    floors = [Floor("1f", "always")]
    courses = {
        "c1": CourseSpec(
            id="c1",
            name="C1",
            price=5000,
            duration_minutes=120,
            min_guests=2,
            max_guests=10,
        )
    }
    res = simulate_dinner_day(
        tables=tables,
        floors=floors,
        desired_guests=8,
        course_ratio=1.0,
        courses=courses,
        course_party_size_distribution={4: 1.0},
        course_start_window={"start": "18:00", "end": "18:00"},
        open_clock="17:00",
        replications=1,
        seed=42,
    )
    assert res.entered_guests == 4
    assert res.lost_guests == 4
    assert len(res.reservations) == 1
    assert res.course_entered == 4
    assert res.course_lost == 4


def test_regression_size_distribution_zero_raises_value_error() -> None:
    """(b) 人数分布 {0: 1.0} で ValueError（タイムアウトしない）."""
    rng = np.random.default_rng(42)
    with pytest.raises(ValueError):
        build_course_parties(10, {0: 1.0}, rng)

    tables = [Table("t1", 4, "1f")]
    floors = [Floor("1f", "always")]
    with pytest.raises(ValueError):
        simulate_dinner_day(
            tables=tables,
            floors=floors,
            desired_guests=10,
            course_ratio=1.0,
            courses={"c1": CourseSpec("c1", "C1", 5000, 120)},
            course_party_size_distribution={0: 1.0},
        )


def test_regression_floor_never_policy_excludes_reservations() -> None:
    """(c) 4 人卓だけの階を never にするとコース客は入店 0 で失った需要に数える."""
    tables = [Table("t1", 4, "2f")]
    floors = [Floor("2f", "never")]
    courses = {
        "c1": CourseSpec(
            id="c1",
            name="C1",
            price=5000,
            duration_minutes=120,
            min_guests=2,
            max_guests=10,
        )
    }
    res = simulate_dinner_day(
        tables=tables,
        floors=floors,
        desired_guests=4,
        course_ratio=1.0,
        courses=courses,
        course_party_size_distribution={4: 1.0},
        course_start_window={"start": "18:00", "end": "18:00"},
        open_clock="17:00",
        replications=1,
        seed=42,
    )
    assert res.course_entered == 0
    assert res.course_lost == 4
    assert res.lost_by_reason.get("no_table_for_reservation") == 4


def test_regression_torahachi_holiday_distinct_combo() -> None:
    """(d) とら八の 2026-01-05（月）と 2026-01-12（月・祝日）が
    別の組合せで計算される.
    """
    inputs = load_store(DATA_DIR / "stores" / "torahachi_akihabara")
    holidays_raw = load_holidays(
        DATA_DIR / "calendars" / "jp_holidays_cao.csv",
        DATA_DIR / "calendars" / "jp_holidays_cao.meta.yaml",
        2026,
    )
    cal = calendars_for_store(
        inputs, year=2026, mode="steady", holidays=holidays_raw
    )[0]
    holidays = set(holidays_raw.keys())
    grouped = group_dinner_days(cal.open_days, holidays)

    key_mon = next(
        k
        for k, days in grouped.items()
        if any(d.day == date(2026, 1, 5) for d in days)
    )
    key_hol = next(
        k
        for k, days in grouped.items()
        if any(d.day == date(2026, 1, 12) for d in days)
    )

    assert key_mon != key_hol
    assert key_hol[4] == "21:30"
    assert key_hol[5] == "23:00"


def test_regression_course_start_window_validation() -> None:
    """(e) course_start_window が営業時間外・逆転なら EvaluationError."""
    # 逆転
    req_reversed = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        year=2026,
        replications=1,
        seed=42,
        overrides={"course_start_window": {"start": "20:00", "end": "18:00"}},
    )
    with pytest.raises(EvaluationError):
        evaluate(DATA_DIR, req_reversed)

    # 営業開始前 (バードマンの営業開始は 17:30)
    req_early = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        year=2026,
        replications=1,
        seed=42,
        overrides={"course_start_window": {"start": "10:00", "end": "19:00"}},
    )
    with pytest.raises(EvaluationError):
        evaluate(DATA_DIR, req_early)

    # 最終入店後 (バードマン標準の最終入店は 21:30)
    req_late = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        year=2026,
        replications=1,
        seed=42,
        overrides={"course_start_window": {"start": "18:00", "end": "22:00"}},
    )
    with pytest.raises(EvaluationError):
        evaluate(DATA_DIR, req_late)


def test_tc_42_11_simulate_dinner_day_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_dinner_day_cache()

    call_count = 0
    orig_sim_rep = dinner_module._simulate_one_replication

    def counting_sim_rep(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return orig_sim_rep(*args, **kwargs)

    monkeypatch.setattr(dinner_module, "_simulate_one_replication", counting_sim_rep)

    tables = [Table("t1", 4, "1f")]
    floors = [Floor("1f", "always")]
    courses = {
        "c1": CourseSpec("c1", "標準コース", 5000, 120, min_guests=2, max_guests=10),
    }
    kwargs: dict[str, Any] = {
        "tables": tables,
        "floors": floors,
        "desired_guests": 20,
        "course_ratio": 0.5,
        "courses": courses,
        "dinner_stay_minutes": 120,
        "cleanup_minutes": 5,
        "open_minute": 0,
        "last_entry_minute": 240,
        "close_minute": 300,
        "replications": 1,
        "seed": 42,
    }

    # 1 回目
    res1 = simulate_dinner_day(**kwargs)
    assert call_count == 1

    # 同じ引数で 2 回呼ぶと、2 回目は 1 日分の試行を呼ばない
    res2 = simulate_dinner_day(**kwargs)
    assert call_count == 1
    # 2 つの戻り値は等しく、同じオブジェクトではない
    assert res1 == res2
    assert res1 is not res2

    # desired_guests だけ 1 増やすと計算し直す
    kwargs_diff = dict(kwargs)
    kwargs_diff["desired_guests"] = 21
    res3 = simulate_dinner_day(**kwargs_diff)
    assert call_count == 2
    assert res3 != res1

    # 1 回目の戻り値の lost_by_reason を書き換えても、
    # 同じ引数の 2 回目の戻り値は変わらない
    res1.lost_by_reason["mutated_key"] = 9999
    res4 = simulate_dinner_day(**kwargs)
    assert "mutated_key" not in res4.lost_by_reason
    assert res4 == res2


