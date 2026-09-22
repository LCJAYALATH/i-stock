"""Pure statistical functions used by the i-STOCK decision engine.

Everything here is framework-independent so it can be unit tested in isolation
and reasoned about in the project report.

Formulas
--------
Simple Moving Average (SMA):   F(t+1) = (1/n) * sum(D(t-n+1) .. D(t))
Linear regression (OLS):       D(t)   = a + b*t
                               b = sum((t - t_bar)(D - D_bar)) / sum((t - t_bar)^2)
                               a = D_bar - b * t_bar
Safety stock:                  SS  = Z * sqrt(L * sigma_d^2 + d^2 * sigma_L^2)
                               (reduces to Z * sigma_d * sqrt(L) when lead time is constant)
Reorder point:                 ROP = d * L + SS
Economic order quantity:       EOQ = sqrt(2 * D * S / H)
Recommended order quantity:    Q   = forecast demand over (L + R) + SS - on hand - on order
"""
import math
from statistics import NormalDist

# --------------------------------------------------------------------------- #
# Descriptive statistics
# --------------------------------------------------------------------------- #


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def stdev(values):
    """Sample standard deviation (n - 1). Returns 0 for fewer than two points."""
    values = list(values)
    n = len(values)
    if n < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))


# --------------------------------------------------------------------------- #
# Moving average
# --------------------------------------------------------------------------- #


def sma_series(values, window):
    """Trailing moving average aligned with `values` (None until window is full)."""
    out = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= window:
            running -= values[i - window]
        out.append(running / window if i >= window - 1 else None)
    return out


def sma_forecast(values, window, horizon):
    """Flat forecast equal to the mean of the last `window` observations."""
    if not values:
        return [0.0] * horizon
    tail = values[-window:]
    level = mean(tail)
    return [level] * horizon


# --------------------------------------------------------------------------- #
# Linear regression
# --------------------------------------------------------------------------- #


def linear_regression(values):
    """Ordinary least squares fit of value against time index 0..n-1.

    Returns (intercept, slope, r_squared).
    """
    n = len(values)
    if n == 0:
        return 0.0, 0.0, 0.0
    if n == 1:
        return float(values[0]), 0.0, 0.0
    t_bar = (n - 1) / 2.0
    y_bar = mean(values)
    sxx = sum((t - t_bar) ** 2 for t in range(n))
    sxy = sum((t - t_bar) * (y - y_bar) for t, y in enumerate(values))
    slope = sxy / sxx if sxx else 0.0
    intercept = y_bar - slope * t_bar
    ss_tot = sum((y - y_bar) ** 2 for y in values)
    ss_res = sum((y - (intercept + slope * t)) ** 2 for t, y in enumerate(values))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return intercept, slope, r2


def lr_fitted(values):
    a, b, _ = linear_regression(values)
    return [a + b * t for t in range(len(values))]


def lr_forecast(values, horizon):
    """Extrapolate the regression line `horizon` periods ahead (never negative)."""
    n = len(values)
    if n == 0:
        return [0.0] * horizon
    a, b, _ = linear_regression(values)
    return [max(0.0, a + b * (n + i)) for i in range(horizon)]


# --------------------------------------------------------------------------- #
# Forecast accuracy and automatic model selection
# --------------------------------------------------------------------------- #


def error_metrics(actual, predicted):
    """MAE, RMSE and MAPE (MAPE ignores periods with zero actual demand)."""
    pairs = list(zip(actual, predicted))
    if not pairs:
        return {"mae": None, "rmse": None, "mape": None}
    errors = [a - p for a, p in pairs]
    mae = mean(abs(e) for e in errors)
    rmse = math.sqrt(mean(e * e for e in errors))
    pct = [abs(a - p) / a for a, p in pairs if a]
    mape = mean(pct) * 100 if pct else None
    return {"mae": mae, "rmse": rmse, "mape": mape}


def backtest(values, holdout=14, window=7):
    """Hold out the last `holdout` days, forecast them from the rest and
    compare SMA vs linear regression. The model with the lower MAE wins.

    Returns a dict with metrics for both models and the selected model name.
    Falls back to SMA when there is not enough history to judge.
    """
    result = {"sma": None, "lr": None, "best": "SMA", "holdout": holdout}
    if len(values) < holdout + max(window, 14):
        return result
    train, test = values[:-holdout], values[-holdout:]
    result["sma"] = error_metrics(test, sma_forecast(train, window, holdout))
    result["lr"] = error_metrics(test, lr_forecast(train, holdout))
    if result["lr"]["mae"] is not None and result["lr"]["mae"] < result["sma"]["mae"]:
        result["best"] = "LR"
    return result


def forecast(values, horizon, model="SMA", window=7):
    if model == "LR":
        return lr_forecast(values, horizon)
    return sma_forecast(values, window, horizon)


# --------------------------------------------------------------------------- #
# Inventory policy
# --------------------------------------------------------------------------- #


def z_score(service_level):
    """Z value for a cycle service level (e.g. 0.95 -> 1.645)."""
    service_level = min(max(service_level, 0.5), 0.9999)
    return NormalDist().inv_cdf(service_level)


def safety_stock(demand_std, lead_time, service_level, avg_demand=0.0, lead_time_std=0.0):
    """Safety stock covering both demand and lead-time variability."""
    if lead_time <= 0:
        return 0.0
    variance = lead_time * demand_std ** 2 + (avg_demand ** 2) * (lead_time_std ** 2)
    return z_score(service_level) * math.sqrt(variance)


def reorder_point(avg_daily_sales, lead_time, safety):
    """ROP = (Average Daily Unit Sales x Lead Time in Days) + Safety Stock."""
    return avg_daily_sales * lead_time + safety


def eoq(annual_demand, order_cost, holding_cost_per_unit):
    if annual_demand <= 0 or order_cost <= 0 or holding_cost_per_unit <= 0:
        return 0.0
    return math.sqrt(2 * annual_demand * order_cost / holding_cost_per_unit)


def cover_demand(forecast_daily, cover_days):
    """Forecast demand over a (possibly fractional) number of days."""
    whole = int(cover_days)
    demand = sum(forecast_daily[:whole])
    if cover_days > whole and len(forecast_daily) > whole:
        demand += forecast_daily[whole] * (cover_days - whole)
    elif cover_days > len(forecast_daily) and forecast_daily:
        demand += forecast_daily[-1] * (cover_days - len(forecast_daily))
    return demand


def recommended_order_qty(forecast_daily, lead_time, review_period, safety, on_hand, on_order):
    """Order-up-to quantity: cover demand until the next order can arrive."""
    demand = cover_demand(forecast_daily, lead_time + review_period)
    need = demand + safety - on_hand - on_order
    return max(0, int(math.ceil(need)))
