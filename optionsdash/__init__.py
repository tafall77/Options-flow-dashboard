"""Live SPY / QQQ options quant dashboard on London Strategic Edge data."""

from . import theme  # registers the plotly template
from .feed import DemoFeed, LSEFeed, make_feed
from .analytics import build_snapshot
from .dashboard import LiveDashboard, compute_snapshot

__all__ = ["DemoFeed", "LSEFeed", "make_feed", "build_snapshot", "LiveDashboard", "compute_snapshot"]
