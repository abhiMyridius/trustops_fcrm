"""fcrm_evals (X6): per-agent evaluation datasets, metrics, runner, gate and
report (design B §7, BUILD-PLAN §7 "Evals" row).

- :mod:`fcrm_evals.datasets`: nine seed-derived ``.jsonl`` datasets, built
  by :mod:`fcrm_evals.datasets.build` (``--check`` verifies them).
- :mod:`fcrm_evals.metrics`: deterministic metrics over the real
  ``fcrm_contracts.proposals`` schemas, reusing Guard 4's verbatim matcher,
  the restricted-terms scanner, the audit hash chain and the R4 predicate;
  plus a judge-metric seam with an offline :class:`FakeJudge`.
- :mod:`fcrm_evals.runner`: ``run`` (dataset x outputs x metrics ->
  :class:`EvalResult`) and ``gate`` (design B §6.4 promotion rule).
- :mod:`fcrm_evals.report`: the Markdown report ``make evals`` writes.

Measured today: the machinery, against reference ("echo") and deliberately
degraded outputs. Not measured yet: any real agent; the per-agent weekly
figures in ``seed.json["evals"]`` remain the generator's seeded targets.
"""

from __future__ import annotations

import os

# DeepEval loads the nearest ``.env`` (the developer's secrets and stale
# settings) into the process the moment it is imported, from any module that
# imports it. Setting this here, before any ``fcrm_evals`` submodule can import
# DeepEval, covers every entry point (D-31 was the same leak through litellm).
os.environ.setdefault("DEEPEVAL_DISABLE_DOTENV", "1")
