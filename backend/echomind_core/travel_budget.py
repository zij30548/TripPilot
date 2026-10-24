"""Explicit full-domain budgets; the legacy three-attempt profile is unchanged.

An unbounded number of test cases is not an unbounded Agent task.  These finite
server-selected tiers share the ORIGINAL reservation methods and lock across
classification, embeddings, specialists, retrieval and composition.  They do
not grant external-call authorization or replace durable product quotas.
"""
from __future__ import annotations

from enum import Enum
from types import MappingProxyType

from echomind_core.provider import ProviderError, TaskBudget

VERSION = "travel-task-budget-v1"


class TravelBudgetTier(Enum):
    SINGLE = "single"
    COLLABORATION = "collaboration"
    REFERENCE = "reference"


LIMITS = MappingProxyType({
    TravelBudgetTier.SINGLE: (6, 8, 8192, 60.0),
    TravelBudgetTier.COLLABORATION: (10, 12, 16384, 90.0),
    TravelBudgetTier.REFERENCE: (16, 16, 24576, 90.0),
})


class TravelTaskBudget(TaskBudget):
    """Only trusted composition selects a finite tier, never a model/request.

    The larger envelope is explicit and isolated from TaskBudget's original
    validation/defaults.  No reservation/refund/retry algorithm is replaced.
    Template/corpus preparation and offline Judge are NOT implicit work here.
    """

    def __init__(self, tier: TravelBudgetTier = TravelBudgetTier.SINGLE):
        if type(tier) is not TravelBudgetTier:
            raise ProviderError("configuration")
        self.tier = tier
        models, tools, tokens, seconds = LIMITS[tier]
        super().__init__(model_limit=models, tool_limit=tools,
                         token_limit=tokens, seconds=seconds)

    def __post_init__(self) -> None:
        if (type(self) is not TravelTaskBudget or type(self.tier) is not TravelBudgetTier
                or (self.model_limit, self.tool_limit, self.token_limit, self.seconds) != LIMITS[self.tier]
                or any(type(value) is not int for value in (self.model_limit, self.tool_limit, self.token_limit))
                or type(self.seconds) is not float):
            raise ProviderError("configuration")

    def summary(self) -> dict:
        return {"version": VERSION, "tier": self.tier.value, "model_limit": self.model_limit,
                "tool_limit": self.tool_limit, "output_tokens_reserved_limit": self.token_limit,
                "deadline_seconds": self.seconds, "shared": True,
                "real_call_authorization": False}
