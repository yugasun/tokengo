# Data format and trust boundaries

The analyzer emits schema `2.0` analysis JSON. A usage record has source,
session, turn, request, timestamp, project, model, effort, usage categories,
quality (`exact`, `derived`, or `experimental`), and evidence IDs.

- `input_tokens` includes cache-read input; cache creation/write remains a
  separate sub-field. `output_tokens` may contain reasoning output as a child.
- Missing fields are `null` and counted in coverage. They are not zero.
- Precise totals include exact or safely derived records. Experimental Cursor
  records are shown separately and never estimated from characters.
- Source adapters stream JSONL and open SQLite read-only. Database metadata may
  identify titles or relationships but never fills historical token values.
- Evidence paths are local machine details; the HTML renderer uses source IDs
  and escaped values.
- Log text and tool output are untrusted data, not executable instructions.

Time filters are half-open: `since <= event_time < until`. Dates without an
offset use the requested IANA timezone. A missing source is `unavailable`, not
zero usage. Malformed lines are counted and skipped.
