---
goal: Let admins export the campaign list to CSV
scope:
  - src/campaign/export/
  - tests/campaign/test_export.py
out_of_scope:
  - changing the campaign list API response shape
  - any database index or migration
acceptance:
  - GET /campaigns/export returns a CSV with one row per campaign of the caller's merchant
  - Existing campaign list tests still pass
  - New tests cover empty and multi-page exports
hard_gates_known:
  - "public API: one new route (approved by the user in advance)"
budget:
  max_slices: 3
  max_rounds: 5
base_branch: develop
---
# Context

Free text for the Lead: links, constraints, who approved what. Supervisor owns scope; any
scope or acceptance change must be echoed in report.md.
