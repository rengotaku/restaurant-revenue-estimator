"""Report sensitivity test stubbing configuration (Issue #53)."""

REAL_REPORT_SENSITIVITY_TESTS: tuple[str, ...] = (
    "test_run_output_is_byte_identical",
    "test_cli_and_api_reports_are_identical",
    "test_tc_53_02_report_has_real_sensitivity_rows",
    # 上書き付きの run でレポートの感度分析が通ることを守る
    # （sensitivity() を直接呼ぶテストは上書きを使わない）
    "test_override_assumption_is_accepted",
    "test_unresolved_floor_can_be_replaced_with_tables",
)
