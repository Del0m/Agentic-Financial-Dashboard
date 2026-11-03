"""Ontology, result containers, and public errors for the graph API."""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.db.models import KGEdge, KGNode

NodeKind = Literal["user", "institution", "account", "merchant", "category", "stream", "advice", "concept", "source"]
Relation = Literal["HAS_ACCOUNT", "CONNECTED", "SPENDS_AT", "CATEGORIZED_AS", "BUDGETS", "SUBSCRIBES", "ABOUT", "IS_A", "RELATES_TO", "HAS_LIMIT", "HAS_RULE", "ALTERNATIVE_TO", "MENTIONED_IN"]
EdgeSource = Literal["projection", "user", "kb_extract"]


class KGError(ValueError):
    """Base class for graph validation failures."""


class InvalidIntervalError(KGError):
    """A valid-time interval is empty, reversed, or malformed."""


class OverlapError(KGError):
    """An assertion conflicts with the accepted valid-time timeline."""


class StaleTargetError(KGError):
    """A correction or close target is unavailable or superseded."""


class ScopeError(KGError):
    """A graph identity is unavailable in the caller's ownership scope."""


class ClockOrderError(KGError):
    """A replacement timestamp does not follow its predecessor."""


class InvalidAssertionError(KGError):
    """An assertion does not conform to the graph ontology or API."""


@dataclass
class GraphSnapshot:
    nodes: list["KGNode"]
    edges: list["KGEdge"]


@dataclass
class NeighborhoodResult(GraphSnapshot):
    truncated: bool
