"""The case SLA: the business days FCRM aims to take from a submission to a
decision-ready case. S1 computes every due date from it (the analyst's queue and
the owner's case page), and S3's Rules & Routing page shows it, so the two can
never disagree. Changing it is a policy change: it needs a decision-log row.
"""

from __future__ import annotations

from typing import Final

CASE_SLA_BUSINESS_DAYS: Final = 2
"""Business days from submission to a decision-ready case."""
