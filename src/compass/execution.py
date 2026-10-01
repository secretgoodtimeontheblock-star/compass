"""Модель исполнения: допущения о ценах заявок, общие для бэктеста, учебного счёта и плана сделки.

Раньше комиссия и проскальзывание были разбросаны по параметрам. Здесь они собраны в одно место и
дополнены тем, что OHLC-свечи не показывают: спредом (разница между ценой покупки и продажи) и
ликвидностью (какую долю объёма свечи занимает заявка). Всё остальное в Compass берёт расходы
отсюда, поэтому проверка, план и тренировка исходят из одних и тех же допущений.

Что здесь НЕ моделируется (честно): очередь заявок, стакан, частичные исполнения внутри свечи,
сессии и клиринг биржи, маржинальные требования. Стакана у бесплатных источников нет, поэтому спред
и ликвидность — оценки, а не измерения: их задаёт профиль рынка, и пользователь может изменить.

Профиль рынка — понятное описание для новичка: «Крипта» и «МосБиржа» отличаются комиссиями, спредом,
шагом количества и временем торгов, но не логикой приложения.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from compass.risk import DEFAULT_FEE_PCT, DEFAULT_SLIPPAGE_PCT, WORSE_SLIPPAGE_MULT

# Доля объёма свечи, выше которой заявку на практике уже нельзя считать «незаметной»
LIQUIDITY_WARN_PCT = 5.0
LIQUIDITY_CAP_SUGGESTED_PCT = 10.0

DEFAULT_SPREAD_PCT = {"moex": 0.05, "crypto": 0.03}


@dataclass(frozen=True, slots=True)
class ExecutionModel:
    """Расходы одной сделки в процентах от цены. Спред делится пополам: вход хуже на половину,
    выход — тоже. max_participation_pct — предельная доля объёма свечи в заявке (None — не ограничивать)."""

    fee_pct: float
    slippage_pct: float
    spread_pct: float = 0.0
    max_participation_pct: float | None = None

    def check(self) -> None:
        for name, v in (("Комиссия", self.fee_pct), ("Проскальзывание", self.slippage_pct), ("Спред", self.spread_pct)):
            if not 0 <= v < 100:
                raise ValueError(f"{name}: от 0 до 100%")
        if self.max_participation_pct is not None and not 0 < self.max_participation_pct <= 100:
            raise ValueError("Доля объёма свечи: больше 0 и не больше 100%")

    @property
    def effective_slippage_pct(self) -> float:
        """Проскальзывание плюс половина спреда: на столько каждая сторона сделки хуже цены свечи."""
        return self.slippage_pct + self.spread_pct / 2

    @property
    def round_trip_cost_pct(self) -> float:
        """Во сколько круг «вход + выход» обходится в процентах: две комиссии, два проскальзывания и спред целиком."""
        return 2 * self.fee_pct + 2 * self.slippage_pct + self.spread_pct

    def worse(self, mult: float = WORSE_SLIPPAGE_MULT) -> ExecutionModel:
        """Ухудшенное исполнение: все расходы во столько раз больше."""
        return ExecutionModel(
            self.fee_pct * mult, self.slippage_pct * mult, self.spread_pct * mult, self.max_participation_pct
        )

    def describe(self) -> str:
        parts = [
            f"комиссия {self.fee_pct:g}% с каждой стороны",
            f"проскальзывание {self.slippage_pct:g}%",
            f"спред {self.spread_pct:g}%",
        ]
        text = ", ".join(parts) + f" — круг покупка+продажа обходится примерно в {self.round_trip_cost_pct:.2f}%."
        if self.max_participation_pct is not None:
            text += f" Заявка не больше {self.max_participation_pct:g}% объёма свечи."
        return text


@dataclass(frozen=True, slots=True)
class TradingSession:
    """Как устроен торговый день рынка. Часы торгов не зашиты: расписания биржи меняются,
    их показывает источник данных (`trading_open`), а здесь — только то, что влияет на расчёт."""

    continuous: bool  # круглосуточно, без перерывов между «днями»
    day_boundary: str  # что считается границей суток для дневных лимитов и «закрытия дня»
    note: str


@dataclass(frozen=True, slots=True)
class MarketProfile:
    market: str
    label: str
    model: ExecutionModel
    session: TradingSession
    why_it_differs: list[str] = field(default_factory=list)

    def dto(self) -> dict:
        m = self.model
        return {
            "market": self.market, "label": self.label,
            "fee_pct": m.fee_pct, "slippage_pct": m.slippage_pct, "spread_pct": m.spread_pct,
            "suggested_participation_pct": LIQUIDITY_CAP_SUGGESTED_PCT,
            "round_trip_cost_pct": round(m.round_trip_cost_pct, 4),
            "session": {"continuous": self.session.continuous, "day_boundary": self.session.day_boundary,
                        "note": self.session.note},
            "summary": m.describe(),
            "why_it_differs": self.why_it_differs,
        }


PROFILES: dict[str, MarketProfile] = {
    "crypto": MarketProfile(
        "crypto", "Крипта (спот)",
        ExecutionModel(DEFAULT_FEE_PCT["crypto"], DEFAULT_SLIPPAGE_PCT, DEFAULT_SPREAD_PCT["crypto"]),
        TradingSession(True, "UTC", "Торги идут круглосуточно, без выходных: «день» для лимитов считается по UTC."),
        [
            "Торги круглосуточные, поэтому цена может уйти далеко, пока вы не смотрите на экран.",
            "Количество делится на малые доли (шаг и минимальная заявка задаёт биржа), лотов нет.",
            "Комиссия биржи обычно выше, чем у брокера на МосБирже, а спред на крупных парах очень узкий.",
        ],
    ),
    "moex": MarketProfile(
        "moex", "МосБиржа (акции)",
        ExecutionModel(DEFAULT_FEE_PCT["moex"], DEFAULT_SLIPPAGE_PCT, DEFAULT_SPREAD_PCT["moex"]),
        TradingSession(
            False, "сутки по Москве",
            "Есть торговые сессии и перерывы; между днями возможны гэпы открытия. Расписание сессий показывает "
            "источник данных, Compass его не угадывает.",
        ),
        [
            "Акции покупаются лотами: минимальная покупка — один лот, а не одна бумага.",
            "Между закрытием и открытием цена может сместиться (гэп), и стоп исполнится хуже уровня.",
            "Бесплатные котировки идут с задержкой около 15 минут: для внутридневной торговли это критично.",
            "Налоги и купоны/дивиденды в расчёт не входят.",
        ],
    ),
}


def profile(market: str) -> MarketProfile:
    """Профиль рынка; для неизвестного рынка — нейтральные допущения без спреда."""
    p = PROFILES.get(market)
    if p is not None:
        return p
    return MarketProfile(
        market, market, ExecutionModel(DEFAULT_FEE_PCT.get(market, 0.1), DEFAULT_SLIPPAGE_PCT, 0.0),
        TradingSession(False, "UTC", ""),
    )


def model_for(
    market: str,
    fee_pct: float | None = None,
    slippage_pct: float | None = None,
    spread_pct: float | None = None,
    max_participation_pct: float | None = None,
) -> ExecutionModel:
    """Модель рынка с переопределениями пользователя: не заданное берётся из профиля."""
    base = profile(market).model
    m = ExecutionModel(
        base.fee_pct if fee_pct is None else fee_pct,
        base.slippage_pct if slippage_pct is None else slippage_pct,
        base.spread_pct if spread_pct is None else spread_pct,
        max_participation_pct,
    )
    m.check()
    return m


def cost_story(base_return_pct: float, worse_return_pct: float, model: ExecutionModel, mult: float = WORSE_SLIPPAGE_MULT) -> str:
    """Исторический результат простым языком: что принято и как меняется при худшем исполнении."""
    worse = model.worse(mult)
    text = (
        f"Историческая оценка исходит из расходов около {model.round_trip_cost_pct:.2f}% на круг «купить и продать» "
        f"({model.describe().split(' — ')[0]}). Если исполнение будет хуже (расходы ≈ {worse.round_trip_cost_pct:.2f}%), "
        f"результат изменится с {base_return_pct:+.1f}% до {worse_return_pct:+.1f}%."
    )
    if base_return_pct > 0 >= worse_return_pct:
        text += " Преимущество исчезает при ухудшении исполнения: оно держалось на дешёвых и точных сделках."
    return text


def participation_pct(qty: float, volume: float) -> float | None:
    """Какую долю объёма свечи занимает заявка, %. None — объём свечи неизвестен (ноль)."""
    return None if volume <= 0 else qty / volume * 100
