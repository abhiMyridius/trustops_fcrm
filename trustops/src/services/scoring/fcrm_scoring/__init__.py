"""S2 scoring-service.

``fcrm_scoring.engine`` is pure arithmetic with no I/O: it is the *only*
place a composite score, band or boundary flag is ever computed anywhere
in this system (rule 1, "no agent writes a score"; design A §5). Every
other module in this package is the HTTP shell around it.
"""

from __future__ import annotations
