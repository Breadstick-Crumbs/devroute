# 0004: Local JSON Lines files for records

**Status:** accepted (v0.1)

## Context

Evaluation needs durable records of routes, outcomes, and verification evidence. They may contain task descriptions that should not leave the machine.

## Options

1. Append-only JSONL files in `.susanoo/records/`.
2. SQLite.
3. A hosted service.

## Decision

Option 1. JSONL is append-only, easy to inspect and diff, tolerant of partial corruption (bad lines are skipped and counted), and needs no dependencies. Records are written only after `susanoo init`, are git-ignored by default, and can store a hash instead of the task text.

## Consequences

- Aggregation reads whole files, which is fine at expected volumes (thousands of records).
- Team-level aggregation needs an export or merge step later. A move to SQLite remains possible behind the `RecordStore` interface.
