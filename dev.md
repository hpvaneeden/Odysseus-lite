# Odysseus development notes

Updated: 2026-10-13


## Goal

Make Odysseus reliable with small local language models. Keep the model focused on the current conversation, use persistent memory as an external source of truth, search the web when facts may be current, and make tool use happen through Odysseus's real tool-calling layer rather than appearing as pretend calls in assistant text.

The intended memory workflow is to search memory for each request, answer using returned memories when relevant, clearly distinguish memory from general knowledge, and save useful user facts and decisions. Automatic factual web search is enabled by the user's preference, subject to incognito and explicit search-off settings.


## Active Architecture Baseline

- **Core LLM engine:** Ollama serves `hf.co/bartowski/google_gemma-4-E2B-it-GGUF:Q8_0`, locally referred to as `gemma4:e2b`.
- **External memory:** Per-user, cross-session memory is managed by the Odysseus harness, with ChromaDB-backed vector retrieval. The harness chooses and injects memory context; the model itself remains stateless between sessions.
- **Search infrastructure:** SearXNG provides live web lookup beyond the model's static knowledge.
- **Integration framework:** Odysseus coordinates agent loops and tool execution. The user's reported runtime also uses secure E2B Python sandboxes.

## What We Did and How

- [x] **Reliable tool calling:** Tool execution improved from frequent plain-text imitations to near-perfect execution in the current test set. Tool events and results are checked, not inferred from the final answer alone.
- [x] **Memory harness integration:** Improved memory writes, cross-session retrieval, preference correction, and context injection through the harness and ChromaDB-backed vector store.
- [x] **Web search improvements:** Refined query generation, search-result formatting, and follow-up search behavior for small-model web lookups.
- [x] **Q8 Gemma optimization:** The local Gemma 4 E2B Q8 setup has demonstrated behavior and reasoning comparable to a substantially larger model for the evaluated workflows.

## Git Conflict Mitigation Strategy

- The Gemma-specific harness changes already present include edits to upstream framework files. Preserve and document that existing work.
- For future features, prefer isolated additions over further core changes. New custom modules and skills (social interaction, audio, UI) should live in a standalone directory such as `/odysseus_extensions/` so upstream updates remain easier to integrate.

## Current Sprint

- [x] **Official-source verification guard implemented:** Recognized SARB/FRED requests use first-party-only search results, avoid an inappropriate one-day freshness filter, and fail closed unless official page content is fetched. Regression coverage is added; the live Gemma rerun is still pending.
- [ ] **News-digest quality:** A live test showed that a "give me a mix" follow-up became the literal query `today's news headlines mix`, contaminating results with irrelevant pages. Add deterministic scope-follow-up expansion, then retest geography, source relevance, fetched-page verification, and labeled links. Temperature remains at 0.5.
- [ ] **Codebase audit:** Map the local input/output prompt-routing hooks and identify extension points for additive work.
- [x] **Social interaction (phase 1):** Added `odysseus_extensions/social_skill.py` to compact only exact, completed greeting/thanks exchanges from the model prompt copy and protect concise information requests from low-signal routing. Current user text, emotional-support content, mixed requests, tool turns, and stored history remain unchanged. Pytest and live Gemma evaluation are pending.
- [ ] **Social interaction follow-up:** Evaluate any standalone-greeting fast path with real conversations; do not rewrite emotional or task content or postprocess streamed persona output.
- [ ] **Audio pipeline:** Research and integrate lightweight, local speech-to-text (STT) and text-to-speech (TTS) pipelines.
- [ ] **UI/UX:** Design a basic graphical element or visual state indicator for the Odysseus desktop experience.

## Future Horizons (Phase 5+)

- [ ] **Dynamic resource-driven engineering:** Build a workflow where Odysseus can find, download, and ingest external documentation or codebases when needed.
- [ ] **C++ code generation:** Enable the assistant to read, modify, and optimize native C++ game code using current external resources, not only pretrained knowledge.


## Current evaluation setup

- Use `hf.co/bartowski/google_gemma-4-E2B-it-GGUF:Q8_0` as the primary baseline. The user reports a 100% success rate across the current tool-call test set after the latest fixes.
- Keep the model, harness configuration, and task prompt fixed while evaluating changes. The next phase is realistic day-to-day use: preference carryover, relevant memory recall, memory correction, current-information web search, and useful multi-step tasks.
- For each scenario, record the prompt, actual tool event, returned tool result, and whether the final answer used the result correctly. A correct answer alone does not prove a tool ran.

## Changes made

- Added a code-owned tool-call protocol to the agent prompt. It tells models to use the actual function-calling interface, not imitate calls in text, and not claim a call ran when it did not.
- Added streaming detection for common textual tool-call markers. When a textual imitation is detected, the fake call is withheld from the stream and a bounded recovery attempt is made; malformed call text is not saved as an assistant answer.
- After the test above exposed an uncovered format, expanded detection to recognize Python-style calls such as `manage_memory(query=...)`, but only when the name matches a tool offered for that turn. This uses the same recovery path.
- A retest exposed a separate bypass: Gemma put its plan and a fake `<|tool_call>call:manage_memory{...}` inside the reasoning stream, which had been forwarded directly. Tool-enabled turns now keep reasoning internal, and fake-call detection also scans that reasoning so the bounded recovery path can run.
- Existing work in this development effort also covers memory retrieval, automatic web search for factual questions, and tool routing. Keep those behaviors in the regression prompts below.


## Historical test confound: published skill

A learned skill named `external-memory-search-and-response-handling` (version 1.0.0, source `learned`, created 2026-10-10) closely matched an earlier leaked narration pattern and may have reinforced it. This was a plausible confound, not a proven sole cause. The user plans to clear saved memory and newly auto-added skills before the real-world evaluation. After cleanup, confirm the skill is gone and start with a fresh chat so the baseline is clean.

## Verification and current caveats

- The user reports that the latest fixes now achieve a 100% success rate with Gemma E2B Q8 in the tests run so far. Earlier failures and fake-call transcripts below are historical and were superseded by the latest fixes; retain them as regression cases, not as the current status.
- Clean-slate real-world memory evaluation: Test 1 did not save a preference from a natural-language request (the user later confirmed that an explicit `manage_memory add` did write it); Test 2 passed with an actual `web_search`; Test 3 failed because a corrected preference did not replace/update the saved brain memory. The key gap was that ordinary memory extraction was scheduled only every fourth message pair and duplicate/correction handling could not replace an existing record.
- Harness-owned memory reliability changes are now in the working tree: every eligible turn injects the full set of that owner's saved memories (newest first), including casual greetings, and the chat stream's `memories_used` event reports what was injected. Extraction is now queued after every completed response, gated by the existing memory/auto-memory settings and excluded for incognito/compare turns. Background extraction batches are serialized and wait until all active chats are idle before calling the model.
- The memory extractor now receives the owner's existing memories as context and accepts explicit owner-validated replacement actions. A deterministic fallback handles “I now prefer …” when there is exactly one saved preference. Replacements retain their memory ID, update timestamp/text/category, and refresh the vector entry. The user verified this behavior in Test 3.
- Real-world memory test results after restart: Test 3 preference correction replaced the prior brain entry; Test 4 applied the corrected preference in a fresh chat; Test 5 auto-saved the Odysseus project and its goal; Test 6 recalled both in a fresh chat. The user later confirmed automatic extraction also saved a Cape Town trip goal and car-free outdoor-attraction preference, and memory use counts increased across turns. Test 7 used a real web search and returned the current SARB rate correctly, though the answer repeated itself and included an unsupported specific observation date.
- Test 8 search-output diagnosis and fix: the original pasted web-search output was exactly 10,117 bytes and ended partway through fetched page 1, confirming the 10,000-character tool-output cap truncated later fetched pages. SearXNG returned 10 results and the harness fetched 5 pages; the old formatter emitted up to 3,000 characters per page plus redundant TL;DR/quotes/statistics. The formatter now budgets compact query-relevant sentence excerpts across fetched pages and trims duplicated summaries, preserving the 10-result recall. Web-agent rules now ask the model to check each material constraint and make a focused follow-up search if evidence is missing. The user reran Test 8 after the change: the result included two fetched-page excerpts within the output limit, recommended Table Mountain with on-foot/cableway/public-transport/tour access, and a follow-up identified MyCiTi plus other public-transport systems. The user considers Test 8 a pass. The official City of Cape Town source supports the statement that MyCiTi is currently the city's only universally accessible public-transport service.
- Startup follow-up: the first web-rule edit left the existing deep-research bullet outside its triple-quoted prompt string, causing a `SyntaxError` in `src/agent_loop.py`. This was corrected. AST syntax parsing now succeeds for all six edited Python modules; the test suite was not run.
- Startup follow-up: the first web-rule edit temporarily left the existing deep-research bullet outside its triple-quoted prompt string, causing a `SyntaxError` in `src/agent_loop.py`. This was corrected. AST syntax parsing succeeded for all six edited Python modules; the full test suite was not run by Codex.
- The current checkout's `.git` file points to `C:/Users/hpvan/odysseus/.git/worktrees/odysseus-main`, which does not exist. Normal Git diff/status/commit operations remain unavailable. Source files and this development note are saved on disk, but no Git commit was made.
- User-provided logs verified real native memory tool execution, approval, execution, and use of the returned memory. A screenshot verified real Python execution for compound interest. Trial division through Python returned `1009 × 1013 × 1019` for `1,041,537,223` and verified the product.
- The sum-of-squares response had the correct value, but its accompanying log showed `tool_calls=0`; do not count that specific run as evidence of Python execution.
- Sequential Thinking MCP calls continued failing with blank error output across Gemma, Qwen2.5 2B, and Gemini, including after restarting the MCP server. This is a separate MCP issue and not a blocker for memory, web search, or the day-to-day assistant goal.
- Earlier code checks were limited: `git diff --check -- src/agent_loop.py` passed at the time; syntax validation and the full test suite were not completed then. The `.git` metadata for `odysseus-main` was reported as invalid, so verify repository status before relying on it for future patch recovery.

## Earlier Evaluation Plan (2026-10-12)

1. The user will clear saved memory and newly auto-added skills before beginning the clean real-world evaluation.
2. Keep Gemma E2B Q8 fixed and run everyday scenarios: remember a preference and apply it later; recall a continuing task after a gap; correct a remembered fact; answer a current question using web search; complete a multi-step task with relevant tools.
3. For each run, verify actual tool events and returned results in Odysseus, then score tool selection, execution, memory/search relevance, and final answer quality separately.
4. Preserve the exact admin-console patch/configuration details before changing or resetting them. Record the UI location, old/new value or patch, reload requirement, and verification.
5. Revisit Sequential Thinking only if it becomes useful for these real-world tasks; its current MCP failure is separate from the successful tool paths.

## Admin-console settings and patch recovery

- The user keeps reasoning display enabled for the admin account under Settings → What to show and what to hide, because it is useful for observing the model's reasoning. Restore this preference if the test reset changes it.
- The admin-console patch enabled native tool support for the Gemma endpoint with ID `a1d395b4`. Reapply it from the admin console's browser console if needed:

  ```js
  fetch('/api/model-endpoints/a1d395b4', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ supports_tools: true })
  }).then(r => r.json()).then(console.log)
  ```

- This sets `supports_tools: true` for that endpoint. Verify the PATCH response reports the updated endpoint and that Odysseus sends native tool definitions on the next Gemma E2B Q8 request. Confirm whether a reload or server restart is needed from the live result; do not assume one.

## Safety while working

Do not switch branches, reset, stash, pull, or overwrite user data without explicit approval. Keep unrelated batch-file and Chroma changes out of commits unless they are intentionally part of the work.
