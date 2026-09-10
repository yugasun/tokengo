# Diagnosis rules

The CLI supplies facts and signals; the invoking Agent supplies semantic
judgment. Read coverage first, then inspect only evidence that can change the
recommendation.

## Evidence to judgment

| Signal | Required interpretation | Safe action |
|---|---|---|
| High model or effort | Check task uncertainty, scope, error cost, quality, and rework | Trial a lighter setting only for clear, low-risk work |
| Long/growing input | Check compaction, repeated context, and oversized tool output | Narrow reads, summarize, or split a changed topic |
| Repeated tool call | Confirm identical arguments and unchanged failure output | Remove only no-information retries; keep necessary polling |
| Errors or corrections | Check whether the next attempt changes the cause or approach | Diagnose environment/tool failure before escalating model |
| High consumption | Check visible result and validation evidence | Record justified cost when it bought a correct outcome |

Text length is a context clue, not a token conversion. Completion events are not
success evidence. A candidate rank means “review,” never “waste.”

## Finding format

Each finding names source, session, turn, actual model/effort, metric, evidence
IDs, confidence, and next action. Label it `fact`, `hypothesis`, or `experiment`.
Say “this request involved N tokens,” not “N tokens can be saved.”

## Operating heuristics

- Clear local edits and format conversions can start with the lightest verified
  model/effort.
- Mature multi-file work usually benefits from a middle setting.
- Unknown-root-cause or high-risk work keeps enough capability to investigate.
- A high-effort planning pass followed by lower-effort execution is a testable
  strategy, not a universal rule.
- If a lighter run causes repeated unchanged failures, fixing the cause is more
  valuable than blindly raising effort.
