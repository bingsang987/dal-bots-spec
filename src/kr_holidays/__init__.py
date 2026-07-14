"""한국 공휴일·영업일 계산 유틸 (대체공휴일 포함).

dal.wiki 데드라인 캘린더 봇 공용:
  - ② 세금: 신고·납부 기한이 공휴일/주말이면 다음 영업일로 순연(국세기본법 §5).
  - ④ 휴양림·국립공원: 예약 개시일·추첨일이 공휴일이면 다음 평일로 순연(KNPS 규정).

`holidays` 라이브러리(대체공휴일 반영)를 래핑한다. 연도별 객체를 캐시.

    from kr_holidays import is_business_day, next_business_day, shift_forward

주의: `holidays` 패키지는 미래 연도의 음력 공휴일(설·추석·부처님오신날)도
천문 계산으로 산출하나, 정부 관보 확정 전이라 드물게 어긋날 수 있다.
확정 소스(보도자료 등)로 검증하는 봇은 그 값을 우선한다.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import holidays as _holidays


@lru_cache(maxsize=32)
def _kr(year: int):
    return _holidays.SouthKorea(years=year)


def is_public_holiday(d: date) -> bool:
    """법정공휴일(대체공휴일 포함) 여부. 주말은 제외하고 순수 공휴일만."""
    return d in _kr(d.year)


def holiday_name(d: date) -> str | None:
    return _kr(d.year).get(d)


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # 5=토, 6=일


def is_business_day(d: date) -> bool:
    """영업일(=평일이면서 공휴일 아님)."""
    return not is_weekend(d) and not is_public_holiday(d)


def next_business_day(d: date) -> date:
    """d가 영업일이면 그대로, 아니면 다음 영업일."""
    cur = d
    while not is_business_day(cur):
        cur += timedelta(days=1)
    return cur


def prev_business_day(d: date) -> date:
    """d가 영업일이면 그대로, 아니면 이전 영업일."""
    cur = d
    while not is_business_day(cur):
        cur -= timedelta(days=1)
    return cur


def shift_forward(d: date) -> date:
    """마감·기한 순연: 공휴일/주말이면 다음 영업일로. next_business_day 별칭."""
    return next_business_day(d)


def add_business_days(d: date, n: int) -> date:
    """d로부터 영업일 n일 뒤(양수) 또는 앞(음수). d 자신은 세지 않음."""
    if n == 0:
        return d
    step = 1 if n > 0 else -1
    cur, remaining = d, abs(n)
    while remaining > 0:
        cur += timedelta(days=step)
        if is_business_day(cur):
            remaining -= 1
    return cur


__all__ = [
    "is_public_holiday",
    "holiday_name",
    "is_weekend",
    "is_business_day",
    "next_business_day",
    "prev_business_day",
    "shift_forward",
    "add_business_days",
]
