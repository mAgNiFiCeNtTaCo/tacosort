"""Deciding where each message goes. Start at `pipeline.Classifier`; see SPEC.md "Decision order"."""

from .contacts import contacts_from_sent
from .heuristics import heuristic_decision, looks_automated
from .pipeline import Classifier
from .rules import rule_matches, validate_rules

__all__ = [
    "Classifier",
    "contacts_from_sent",
    "heuristic_decision",
    "looks_automated",
    "rule_matches",
    "validate_rules",
]
