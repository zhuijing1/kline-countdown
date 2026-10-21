"""K 线周期对齐规则。

为什么要单独抽一层
------------------
「周期 5 分钟」并不意味着边界一定是 09:30 / 09:35。不同市场、不同行情软件
对 K 线切分方式并不一致，常见两种：

1. ``session``（交易时段起点对齐，国内多数行情软件的默认做法）
   每段交易时段的开始时刻作为锚点，向后按周期等分。
   A 股 60 分钟线因此是 09:30–10:30、10:30–11:30（而不是 10:00–11:00）。

2. ``clock``（整点时钟对齐）
   以自然日零点为锚点按周期等分，K 线边界落在 00:00、00:15、00:30……
   适合 24 小时连续交易、或图表按整点切分的场景。

两者在周期长度能整除「时段起点到整点偏移」时结果相同（例如 A 股 5/15/30 分钟），
在 60 分钟等周期上则明显不同，因此必须做成可配置而非写死。

新增规则：实现一个 ``Alignment`` 子类并注册到 ``ALIGNMENTS`` 即可，
engine 不需要改动。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import ALIGNMENT_CLOCK, ALIGNMENT_SESSION


@dataclass(frozen=True)
class BarBounds:
    """一根 K 线的时间范围，以及它在本交易时段内的序号。"""

    start: datetime
    end: datetime
    index: int


class Alignment:
    """对齐规则接口。"""

    key: str = ""
    label: str = ""

    def bar_bounds(
        self,
        now: datetime,
        session_start: datetime,
        session_end: datetime,
        period_seconds: int,
    ) -> BarBounds:
        raise NotImplementedError

    # 供子类复用：把起点夹到时段内，终点不得超过时段结束
    @staticmethod
    def _clip(
        bar_start: datetime,
        session_start: datetime,
        session_end: datetime,
        period_seconds: int,
        index: int,
    ) -> BarBounds:
        if bar_start < session_start:
            bar_start = session_start
        bar_end = bar_start + timedelta(seconds=period_seconds)
        if bar_end > session_end:
            # 时段剩余不足一个完整周期：最后一根 K 线在时段结束时被截断
            bar_end = session_end
        return BarBounds(bar_start, bar_end, index)


class SessionStartAlignment(Alignment):
    """以每段交易时段的起点为锚点等分。"""

    key = ALIGNMENT_SESSION
    label = "交易时段起点对齐"

    def bar_bounds(self, now, session_start, session_end, period_seconds):
        offset = (now - session_start).total_seconds()
        if offset < 0:
            offset = 0.0
        index = int(offset // period_seconds)
        bar_start = session_start + timedelta(seconds=index * period_seconds)
        return self._clip(bar_start, session_start, session_end, period_seconds, index)


class ClockAlignment(Alignment):
    """以自然日零点为锚点等分（整点/半点对齐）。"""

    key = ALIGNMENT_CLOCK
    label = "整点时钟对齐"

    def bar_bounds(self, now, session_start, session_end, period_seconds):
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed = (now - midnight).total_seconds()
        index = int(elapsed // period_seconds)
        bar_start = midnight + timedelta(seconds=index * period_seconds)
        # 时段起点可能晚于整点边界（如 09:30 开盘、60 分钟整点对齐），
        # 此时首根 K 线被截断：09:30–10:00。
        index_in_session = int(max(0.0, (bar_start - session_start).total_seconds()) // period_seconds)
        return self._clip(bar_start, session_start, session_end, period_seconds, index_in_session)


ALIGNMENTS: dict[str, Alignment] = {
    SessionStartAlignment.key: SessionStartAlignment(),
    ClockAlignment.key: ClockAlignment(),
}

DEFAULT_ALIGNMENT = ALIGNMENT_SESSION


def get_alignment(key: str | None) -> Alignment:
    return ALIGNMENTS.get(str(key or DEFAULT_ALIGNMENT), ALIGNMENTS[DEFAULT_ALIGNMENT])
