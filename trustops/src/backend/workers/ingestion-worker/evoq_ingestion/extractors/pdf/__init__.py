"""PDF extraction helpers shared by ``PdfplumberExtractor`` and ``PypdfExtractor``.

Scope & functionality
---------------------
Small, independently testable collaborators kept out of ``pdf_extractor.py``
so each fidelity concern (§1.2 of the plan) is its own unit:

* ``lines`` — visual line extraction, hyphenation repair, ligature/NFC
  normalisation and paragraph assembly (``LineAssembler``).
* ``fonts`` — ``FontStats``, the one-pass body-text font-size mode.
* ``headings`` — ``HeadingInferencer`` (size ratio + bold-short) and
  ``classify_numbered`` (numbered-line heading detection, engine-agnostic).
* ``columns`` — ``ColumnDetector``, the persistent x-gap multi-column split.
* ``repeated`` — ``RepeatedLineDetector``, cross-page header/footer removal.
"""

from __future__ import annotations
