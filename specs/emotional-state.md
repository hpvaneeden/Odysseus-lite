# Slice 3: Functional Emotional Intelligence

## Status

Proposed implementation specification based on the agreed starting decision: functional emotional intelligence without claims of subjective feelings, with state that persists across conversations and has explicit decay and reset rules.

## Goal

Use a small, private, owner-scoped interaction state to help Odysseus respond more appropriately to explicit feedback over time. This is a behavioral adaptation mechanism. It does not represent an inner emotional experience, diagnose the user, or alter safety policy, permissions, or tool access.

## State model

Persist three bounded signals per owner:

- `repair`: recent explicit corrections or negative feedback indicate that the next response should prioritize acknowledgement, accuracy, and avoiding repetition.
- `rapport`: recent explicit positive feedback or social signals support a natural, conversational tone without forced familiarity.
- `success`: recent outcome acknowledgements support continuity on a task that the user said worked.

Each signal is a number in `[0, 1]`; the store also records `updated_at` and a bounded set of processed experience IDs for idempotence. Only existing, persisted Slice 1 experience records are eligible evidence. No raw transcript or inferred mood is stored. Experience and trait records are excluded from the generic saved-memory prompt; Slice 3 emits only the derived advisory guidance below.

## Update and decay

Decay scores exponentially using a 30-day half-life whenever state is loaded or updated. A verified new experience boosts only its mapped signal (repair: correction 0.65, negative_feedback 0.8; rapport: positive_feedback 0.55, social_signal 0.4; success: outcome_ack 0.6). Combine as `min(1, decayed_score + boost)`. Unknown signals are ignored. Duplicate experience IDs do not boost state twice. Evidence older than 90 days is ignored; this matches Slice 2's evidence window. Values below 0.05 are treated as zero.

The state is a lightweight current interaction summary, not a permanent trait. No time-based job is required: decay is applied at read/update time.

## Response behavior

On eligible turns, include a short, untrusted context note only when at least one decayed signal is at or above 0.35:

- `repair`: explicitly acknowledge a relevant correction or boundary, be careful and direct, and do not repeat the same mistake.
- `rapport`: use a natural conversational tone; do not overdo praise, intimacy, or familiarity.
- `success`: preserve relevant task continuity and avoid re-litigating a result the user confirmed worked.

The note must describe these as response preferences inferred from explicit interaction evidence, not as the user's current feelings or the assistant's feelings. State must never override the current user request, system/developer instructions, safety policy, or explicit user preferences. Do not inject state in incognito, compare, `no_memory`, memory-disabled, or otherwise ineligible turns.

## Privacy, ownership, and opt-in

- Store state in a dedicated JSON sidecar next to the canonical memory store, partitioned by effective owner. Do not use the session ID as the identity boundary.
- Enable updates and context use only when `memory_enabled`, `auto_memory`, `auto_experience`, and the new `emotional_state_enabled` per-user preference are all true. Both interaction recording and the new adaptation preference default off and are exposed in the Memories settings panel.
- Honor incognito, compare mode, `no_memory`, and `allow_background_extraction=False` as hard no-read/no-write gates for chat use and state updates.
- Do not use ownerless experience records for a named owner. Auth-disabled single-user operation may use the legacy empty owner key.
- State is advisory context, never policy or authority.

## Reset and failure behavior

- Provide an owner-scoped reset operation that removes only that owner's state; expose it through the memory API.
- The existing admin memory wipe clears all owners' emotional state along with memory and reflection sidecars.
- Setting `emotional_state_enabled` false suppresses reads and writes; it does not silently erase state. Re-enabling resumes from decayed values. The explicit reset operation is the deletion mechanism.
- Serialize state mutations with `memory_write_lock()` and write through a temporary file plus `os.replace`. Never hold the lock across an `await`.
- Invalid, unreadable, or malformed state fails closed: do not inject context or overwrite unknown data. Failures must not fail chat.

## Implementation scope

Add `services/memory/emotional_state.py`; wire guarded update and context retrieval through `routes/chat_helpers.py`; add an owner-scoped reset endpoint to `routes/memory/memory_routes.py`; clear the sidecar in `routes/admin_wipe/admin_wipe_routes.py`; document the contract here and in `specs/chat.md`. Add focused tests for scoring/decay/idempotence, ownership, reset, malformed storage, privacy gates, and response-context gating.
