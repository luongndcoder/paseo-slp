---
slice_id: s1-export-service
goal: Add CampaignExporter.to_csv() that streams campaigns of one merchant as CSV
files:
  - src/campaign/export/exporter.py
  - tests/campaign/test_export.py
base_ref: develop
tests_must_pass:
  - tests/campaign/test_export.py
out_of_scope:
  - src/campaign/api/
  - any change to merchant_id filtering
parallel_group: export
---
# Files and tasks

- `src/campaign/export/exporter.py`: new class `CampaignExporter`.
- `tests/campaign/test_export.py`: cover empty and multi-page exports.

# Signatures

```python
class CampaignExporter:
    def to_csv(self, merchant_id: str, page_size: int = 500) -> Iterator[str]: ...
```

# Inline context

Trimmed code the Builder needs (repository function that already filters by merchant_id).

# Conventions

Link the rule files the Builder must follow.
