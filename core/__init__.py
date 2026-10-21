"""核心层：倒计时计算、周期对齐、提醒调度、配置持久化。"""

from .engine import (
    REASON_LABELS,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_TRADING,
    CountdownState,
    compute_state,
    describe_state,
    validate_profile,
)
from .models import (
    ALIGNMENT_CLOCK,
    ALIGNMENT_SESSION,
    AppConfig,
    Appearance,
    Profile,
    ReminderRule,
    TradingSession,
    default_profiles,
)

__all__ = [
    "ALIGNMENT_CLOCK",
    "ALIGNMENT_SESSION",
    "AppConfig",
    "Appearance",
    "CountdownState",
    "Profile",
    "REASON_LABELS",
    "ReminderRule",
    "STATUS_ERROR",
    "STATUS_IDLE",
    "STATUS_TRADING",
    "TradingSession",
    "compute_state",
    "default_profiles",
    "describe_state",
    "validate_profile",
]
