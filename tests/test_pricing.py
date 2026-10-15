"""Тесты для модуля расчета раскладки и стоимости (calculate_nesting_and_price из jpeg2eps.py).

Фиксируют текущее поведение калькулятора плоттера Roland VP-540.
Найденные несоответствия (баги) помечены как xfail в соответствии с требованиями Фазы 0.
"""

from __future__ import annotations

import pytest

from jpeg2eps import calculate_nesting_and_price


def test_single_sticker_nesting_and_pricing() -> None:
    """Проверка расчета для одиночного стикера 50x50 мм (qty=1)."""
    res = calculate_nesting_and_price(50.0, 50.0, qty=1, roll_width_mm=1370.0, gap_mm=3.0)

    assert res["qty"] == 1
    assert res["item_w_mm"] == 50.0
    assert res["item_h_mm"] == 50.0
    assert res["roll_width_mm"] == 1370.0
    assert res["cols"] == 25  # (1360 + 3) // (50 + 3) = 25
    assert res["rows"] == 1
    assert res["rotated"] is False
    assert res["used_width_mm"] == 60.0  # 1 * 50 + 0 + 10
    assert res["used_length_mm"] == 60.0  # 1 * 50 + 0 + 10
    assert res["cost_rub"] == 5  # минимальная себестоимость
    assert res["retail_rub"] == 100  # минимальная розничная цена
    assert res["unit_rub"] == 100.0
    # На лист А4 (200x287): cols=3, rows=5 -> fit_qty=15
    assert res["a4_cols"] == 3
    assert res["a4_rows"] == 5
    assert res["a4_fit_qty"] == 15
    assert res["a4_cost_rub"] == 8
    assert res["a4_retail_rub"] == 250


@pytest.mark.parametrize(
    ("qty", "expected_retail", "expected_unit", "expected_rows"),
    [
        (10, 140, 14.0, 1),   # скидка 10% (коэффициент 0.90)
        (25, 300, 12.0, 1),   # скидка 20% (коэффициент 0.80)
        (100, 900, 9.0, 4),   # скидка 40% (коэффициент 0.60), 100 / 25 = 4 ряда
    ],
)
def test_quantities_discount_and_layout(
    qty: int,
    expected_retail: int,
    expected_unit: float,
    expected_rows: int,
) -> None:
    """Проверка прогрессивной скидки за объем тиража (10, 25, 100 шт.) и раскладки по рядам."""
    res = calculate_nesting_and_price(50.0, 50.0, qty=qty, roll_width_mm=1370.0, gap_mm=3.0)

    assert res["qty"] == qty
    assert res["rows"] == expected_rows
    assert res["retail_rub"] == expected_retail
    assert res["unit_rub"] == expected_unit


def test_unit_price_decreases_with_quantity() -> None:
    """Цена за единицу продукции должна строго убывать при росте тиража."""
    res_1 = calculate_nesting_and_price(50.0, 50.0, qty=1)
    res_10 = calculate_nesting_and_price(50.0, 50.0, qty=10)
    res_25 = calculate_nesting_and_price(50.0, 50.0, qty=25)
    res_100 = calculate_nesting_and_price(50.0, 50.0, qty=100)

    assert res_1["unit_rub"] > res_10["unit_rub"] > res_25["unit_rub"] > res_100["unit_rub"]


def test_rotation_90_degrees_optimization() -> None:
    """Проверка авто-поворота на 90 градусов, если это сокращает длину погонного метра рулона."""
    # Изделие 1000x100 мм (ШxВ):
    # Без поворота: 1 шт. в ряд (1000 мм > 1360/2), 10 рядов -> длина 10 * 100 + 9 * 3 + 10 = 1037 мм
    # С поворотом: ширина становится 100 мм -> 13 шт. в ряд -> 10 шт. умещаются в 1 ряд -> длина 1000 + 10 = 1010 мм
    res_rot = calculate_nesting_and_price(1000.0, 100.0, qty=10, roll_width_mm=1370.0)
    assert res_rot["rotated"] is True
    assert res_rot["cols"] == 13
    assert res_rot["rows"] == 1
    assert res_rot["used_length_mm"] == 1010.0

    # Изделие 100x1000 мм: поворот не требуется, так как уже ориентировано оптимально
    res_no_rot = calculate_nesting_and_price(100.0, 1000.0, qty=10, roll_width_mm=1370.0)
    assert res_no_rot["rotated"] is False
    assert res_no_rot["cols"] == 13
    assert res_no_rot["rows"] == 1
    assert res_no_rot["used_length_mm"] == 1010.0


def test_item_wider_than_roll_current_behavior() -> None:
    """Фиксация текущего поведения: изделие шире рулона (1500 мм > 1370 мм) не вызывает ошибку."""
    res = calculate_nesting_and_price(1500.0, 1500.0, qty=1, roll_width_mm=1370.0)
    # Текущий алгоритм принудительно возвращает cols >= 1 через max(1, ...)
    assert res["cols"] == 1
    assert res["used_width_mm"] == 1510.0
    assert res["used_width_mm"] > res["roll_width_mm"]


@pytest.mark.xfail(
    reason="Баг: изделие шире рулона (1500 мм > 1370 мм) не отбраковывается и не помечается как не помещающееся",
    strict=True,
)
def test_item_wider_than_roll_should_fail_or_flag() -> None:
    """Ожидаемое поведение: изделие шире рулона должно давать cols=0 или вызывать ValueError."""
    res = calculate_nesting_and_price(1500.0, 1500.0, qty=1, roll_width_mm=1370.0)
    assert res["cols"] == 0


def test_item_larger_than_a4_current_behavior() -> None:
    """Фиксация текущего поведения: для изделия больше А4 a4_fit_qty возвращает 1 из-за max(1, a4_fit)."""
    res = calculate_nesting_and_price(250.0, 350.0, qty=1)
    # Поле А4: 200x287 мм. Ни 250, ни 350 не помещаются, но max(1, 0) дает 1.
    assert res["a4_fit_qty"] == 1
    assert res["a4_cols"] == 1
    assert res["a4_rows"] == 1


@pytest.mark.xfail(
    reason="Баг: при размерах изделия больше А4 (250x350 мм) a4_fit_qty ошибочно равен 1 вместо 0 из-за max(1, ...)",
    strict=True,
)
def test_item_larger_than_a4_should_be_zero() -> None:
    """Ожидаемое поведение: если изделие физически не помещается на лист А4, вместимость должна быть 0."""
    res = calculate_nesting_and_price(250.0, 350.0, qty=1)
    assert res["a4_fit_qty"] == 0


def test_pricing_module_import_and_config() -> None:
    """Проверка загрузки конфигурации из pricing.toml через модуль pricing."""
    import pricing

    cfg = pricing.load_pricing_config()
    assert "roll" in cfg
    assert "costs" in cfg
    assert "retail" in cfg
    assert cfg["roll"]["default_width_mm"] == 1370.0
    assert cfg["costs"]["zenon"]["material_rub_per_m2"] == 75.18

    # Проверка вызова напрямую из pricing
    res = pricing.calculate_nesting_and_price(50.0, 50.0, qty=1)
    assert res["cost_rub"] == 5
    assert res["retail_rub"] == 100
