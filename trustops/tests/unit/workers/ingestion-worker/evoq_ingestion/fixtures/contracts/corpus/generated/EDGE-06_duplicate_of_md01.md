# Ingestion Engine Guide

The engine turns uploads into canonical Markdown. SPEC-001 requires every body to carry structure markers.

## Supported formats

| Format | Engine | Markers |
|---|---|---|
| PDF | pdfplumber | `--- Page N ---` |
| XLSX | openpyxl | `--- Sheet: name ---` |
| PPTX | python-pptx | `--- Slide N ---` |

## Configuration

Settings are read from the environment. SPEC-002 requires defaults to live in one module.

```python
from evoq_ingestion import Limits

limits = Limits(max_pdf_pages=2000)
```

### Environment table

| Variable | Default |
|---|---|
| `PDF_ENGINE` | `pdfplumber` |
| `XLSX_MAX_ROWS_PER_SHEET` | `200000` |

## Failure codes

Every refusal is a lower_snake code such as `encrypted_pdf` or `file_too_large`.

```json
{"status": "FAILED", "failure_code": "encrypted_pdf"}
```

## Links

See the [contract](../canonical_document.md) and the [markers grammar](markers.md).
