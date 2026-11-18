---
slice_id: s1-export-service
goal: "Admins can download one merchant's campaigns as CSV without loading them all in memory (the export times out today)"
constraints:
  - "Tenant isolation: every query keeps its merchant_id filter"
  - "No change to the public API shape (that is slice s2)"
  - "Hard gates: none in this slice"
design_choice: "Stream pages of 500 rows through a generator in CampaignExporter.to_csv (challengeable)"
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
# Goal

Outcome and why (1-3 lines). No implementation steps here.

# Constraints (binding)

Hard gates, invariants and rules that must hold, plus the front-matter `files`, `tests_must_pass` and
`out_of_scope`. Not negotiable: if one cannot be met, stop with status `needs_input`.

# Design choice (proposed, challengeable)

- `src/campaign/export/exporter.py`: new class `CampaignExporter`.
- `tests/campaign/test_export.py`: cover empty and multi-page exports.

```python
class CampaignExporter:
    def to_csv(self, merchant_id: str, page_size: int = 500) -> Iterator[str]: ...
```

This is the Lead's current best guess. If evidence shows it is wrong, answer with `scope_challenge` in
result.json (premise, evidence, proposal) instead of silently following or silently deviating. You still may
not edit files outside `files`.

# Inline context

Trimmed code the Builder needs, with file:line (repository function that already filters by merchant_id).

# Conventions

Link the rule files the Builder must follow.
