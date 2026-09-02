"""The matcher — Dev A's naive model, connected to the real stream.

This package is the end-of-week-2 integration point (Appendix D.2): the gateway stopped
calling the stub engine and now `XADD`s to the inbound stream, and this is the thing that
consumes it.
"""

from services.matcher.adapter import NaiveMatcher
from services.matcher.runner import Matcher

__all__ = ["Matcher", "NaiveMatcher"]
