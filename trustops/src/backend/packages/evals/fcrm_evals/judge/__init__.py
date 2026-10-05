"""The LLM judge (design B §7.3 judge rows, §7.4).

- :mod:`fcrm_evals.judge.cases`: the text a judge is given for one row of the
  judged agents (narrative, examiner, critic). Pure; no model, no DeepEval.
- :mod:`fcrm_evals.judge.llm`: :class:`~fcrm_evals.judge.llm.BedrockJudgeLLM`,
  DeepEval's ``AmazonBedrockModel`` on the ``judge`` tier of
  ``ai/models/tiers.yaml``, metered.
- :mod:`fcrm_evals.judge.deepeval_metrics`: DeepEval's faithfulness, answer
  relevancy and hallucination metrics as runner metrics.
- :mod:`fcrm_evals.judge.rubric`: the real
  :data:`~fcrm_evals.metrics.judge.JudgeFn` behind ``narrative_faithfulness``.

Only :mod:`~fcrm_evals.judge.cases` imports without the ``live`` extra
(``fcrm-evals[live]``: DeepEval and aiobotocore); the others import DeepEval,
and say so when it is missing.
"""

from __future__ import annotations
