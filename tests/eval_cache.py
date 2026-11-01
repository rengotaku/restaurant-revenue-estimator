"""Evaluation caching across tests (Issue #42)."""

import copy
import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from restaurant_revenue_estimator.inputs import input_hash, load_store
from restaurant_revenue_estimator.services.evaluation import (
    EvaluationRequest,
    EvaluationResult,
    evaluate,
)

TARGET_FILES: tuple[str, ...] = (
    "test_web.py",
    "test_simulator_default_run.py",
    "test_eval_cache.py",
    "test_variants.py",
    "test_screens.py",
)

EXCLUDED_TESTS: tuple[str, ...] = (
    "test_cli_and_api_reports_are_identical",
    "test_same_parameters_return_cached_run",
    "test_api_does_not_modify_data_directory",
)


class CachedEvaluate:
    """元の evaluate を包んで、同じ条件なら 1 回目の結果を使い回すクラス."""

    def __init__(
        self,
        evaluate_fn: Callable[..., list[EvaluationResult]] = evaluate,
    ) -> None:
        self.evaluate_fn = evaluate_fn
        self.real_calls: int = 0
        self._cache: dict[tuple[Any, ...], list[EvaluationResult]] = {}

    def __call__(
        self,
        data_dir: Path | str,
        request: EvaluationRequest,
    ) -> list[EvaluationResult]:
        resolved_data_dir = Path(data_dir).resolve()
        store_path = resolved_data_dir / "stores" / request.store_id
        if not store_path.exists():
            return self.evaluate_fn(data_dir, request)

        abs_data_dir = str(resolved_data_dir)
        overrides_json = json.dumps(
            request.overrides or {},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

        inputs = load_store(store_path)
        store_hash = input_hash(inputs, request.overrides)

        request_items: list[Any] = []
        for field in dataclasses.fields(request):
            val = getattr(request, field.name)
            if field.name == "overrides":
                request_items.append(overrides_json)
            else:
                request_items.append(val)

        cache_key = (
            abs_data_dir,
            *request_items,
            store_hash,
        )

        if cache_key not in self._cache:
            self.real_calls += 1
            self._cache[cache_key] = copy.deepcopy(self.evaluate_fn(data_dir, request))

        return copy.deepcopy(self._cache[cache_key])
