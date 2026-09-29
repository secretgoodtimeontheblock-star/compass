"""Проверка ответа AI на выдуманные числа.

Модель не должна называть цены, уровни и показатели, которых нет в переданных ей данных.
Запретить это промптом можно, гарантировать — нет, поэтому код сверяет числа в ответе с
числами в фактах и возвращает те, которых в фактах нет. Это предупреждение, а не блокировка:
пояснение вроде «риск 1–2% на сделку» — не ошибка, но пользователь должен видеть, что
это не цифра из его данных.
"""

from __future__ import annotations

import re

_NUM = re.compile(r"\d+(?:[  ]\d{3})*(?:[.,]\d+)?")
_DATE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?\b")
_REL_TOL = 0.01  # 1%: «272» в ответе — это округление 272,45 из данных, а не выдумка
# круглые числа, которые встречаются в любом учебном объяснении (шкала RSI 0–100, уровни 50/70, SMA200)
_TEACHING = {50.0, 70.0, 100.0, 200.0}


def _values(text: str) -> list[float]:
    text = _DATE.sub(" ", text)  # даты в любом формате — не «показатели»
    out = []
    for m in _NUM.finditer(text):
        raw = m.group().replace(" ", "").replace(" ", "").replace(",", ".")
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


def unverified_numbers(answer: str, facts: str) -> list[str]:
    """Числа из ответа, которых нет (с точностью до округления) в фактах. Без дублей, в порядке появления."""
    allowed = _values(facts)
    seen: set[float] = set()
    bad: list[str] = []
    for v in _values(answer):
        if v in seen:
            continue
        seen.add(v)
        if v == int(v) and v <= 31:  # счётчики, дни, мелкие целые: «2 стопа», «14 свечей»
            continue
        if 1900 <= v <= 2100 and v == int(v):  # годы
            continue
        if v in _TEACHING:
            continue
        if any(abs(v - a) <= max(abs(a) * _REL_TOL, 0.005) for a in allowed):
            continue
        bad.append(f"{v:g}")
    return bad
