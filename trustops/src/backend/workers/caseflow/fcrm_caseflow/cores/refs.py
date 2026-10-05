"""The keys of ``CaseRun.refs`` the assessment half reads and writes.

``refs`` carries references and small summaries steps leave for later steps (and
for the jobs the engine dispatches), never case data. Naming the keys once keeps
the cores, the engine's dispatch and the tests agreeing on them.
"""

from __future__ import annotations

from typing import Final

#: The last score of record: ``composite``, ``band``, ``route``, ``boundary_flag``,
#: ``boundary_distance``, ``score_record_seq`` and, once an analyst replaced a
#: value, ``overrides``. What a re-score compares itself with, and what the critic
#: and the narrative may read (read-only) through the context pack.
SCORE_SUMMARY_REF: Final = "score_summary"

#: Ids of the research and assessment jobs whose proposals were published, so a
#: re-run publishes only what is new.
PUBLISHED_JOBS_REF: Final = "assessment_published_jobs"

#: The active re-run: ``{"scope", "changed_fields", "domains", "reason"}``,
#: written by the step that dispatches one and read by ``rerun.publish``.
RERUN_REF: Final = "rerun"

#: An owner's answer waiting for the documents it came with to be extracted:
#: ``{"changed_fields", "doc_ids", "round", "submission_version"}``.
RERUN_PENDING_REF: Final = "rerun_pending"

#: The pack of the current lock: ``pack_id``, ``pack_version``, ``pack_hash``.
PACK_REF: Final = "pack"

#: The route R1/R2 chose from the band when the pack was released
#: (``analyst`` or ``committee``).
ROUTE_REF: Final = "route"
