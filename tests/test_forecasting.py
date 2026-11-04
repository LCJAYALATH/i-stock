"""Unit tests for the statistical core of the decision engine."""
import math

import pytest

from istock.engine import forecasting as fc


def test_mean_and_stdev():
    assert fc.mean([2, 4, 6]) == 4
    assert fc.stdev([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(2.138, abs=1e-3)
    assert fc.stdev([5]) == 0
    assert fc.mean([]) == 0


def test_sma_series_and_forecast():
    assert fc.sma_series([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]
    assert fc.sma_forecast([1, 2, 3, 4, 5], 3, 2) == [4, 4]
    assert fc.sma_forecast([], 3, 2) == [0, 0]


def test_linear_regression_recovers_exact_line():
    values = [3 + 2 * t for t in range(10)]
    a, b, r2 = fc.linear_regression(values)
    assert a == pytest.approx(3)
    assert b == pytest.approx(2)
    assert r2 == pytest.approx(1)
    assert fc.lr_forecast(values, 2) == pytest.approx([23, 25])


def test_lr_forecast_never_negative():
    assert min(fc.lr_forecast([10, 8, 6, 4, 2], 10)) == 0


def test_error_metrics():
    m = fc.error_metrics([10, 20, 0], [12, 18, 1])
    assert m["mae"] == pytest.approx(5 / 3)
    assert m["rmse"] == pytest.approx(math.sqrt(9 / 3))
    assert m["mape"] == pytest.approx(((2 / 10) + (2 / 20)) / 2 * 100)  # zero actual ignored


def test_backtest_prefers_regression_for_trending_demand():
    trending = [10 + 0.8 * t for t in range(60)]
    assert fc.backtest(trending, holdout=14, window=7)["best"] == "LR"


def test_backtest_prefers_sma_for_level_shift():
    # Long flat history then a recent level shift: regression lags, SMA adapts.
    values = [10] * 50 + [30] * 20
    assert fc.backtest(values, holdout=7, window=7)["best"] == "SMA"


def test_backtest_falls_back_with_short_history():
    r = fc.backtest([1, 2, 3], holdout=14)
    assert r["best"] == "SMA" and r["sma"] is None


def test_z_score():
    assert fc.z_score(0.95) == pytest.approx(1.645, abs=1e-3)
    assert fc.z_score(0.5) == pytest.approx(0, abs=1e-9)


def test_safety_stock_constant_lead_time():
    # Z * sigma * sqrt(L)
    assert fc.safety_stock(4, 9, 0.95) == pytest.approx(1.6449 * 4 * 3, abs=1e-2)


def test_safety_stock_includes_lead_time_variability():
    base = fc.safety_stock(4, 9, 0.95, avg_demand=10, lead_time_std=0)
    with_var = fc.safety_stock(4, 9, 0.95, avg_demand=10, lead_time_std=2)
    expected = 1.6449 * math.sqrt(9 * 16 + 100 * 4)
    assert with_var == pytest.approx(expected, abs=1e-2)
    assert with_var > base


def test_reorder_point_formula_from_proposal():
    # ROP = (Average Daily Unit Sales x Lead Time in Days) + Safety Stock
    assert fc.reorder_point(20, 5, 30) == 130


def test_eoq():
    assert fc.eoq(1000, 50, 4) == pytest.approx(math.sqrt(25000))
    assert fc.eoq(0, 50, 4) == 0


def test_recommended_order_qty():
    # Need 10/day for (5 + 7) days = 120, + SS 20 - on hand 50 - on order 30 = 60
    assert fc.recommended_order_qty([10] * 30, 5, 7, 20, 50, 30) == 60
    assert fc.recommended_order_qty([10] * 30, 5, 7, 20, 500, 0) == 0
    # Fractional cover days are prorated
    assert fc.recommended_order_qty([10] * 30, 2.5, 0, 0, 0, 0) == 25
