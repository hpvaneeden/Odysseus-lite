# Odysseus Lite development notes

Updated: 2026-10-21


## Goal

Make Odysseus reliable and useful with small local language models: the model is the engine or “soul”, the harness is the body and neural network, and tools are the limbs. The goal is to let compact models complete real work through tool use, web search, and external memory while preserving conversational continuity and a personality that develops from interaction over time.

Treat saved memories and evolving interaction state as separate from the model's own context window. Respect memory, incognito, compare-mode, and per-feature opt-in controls. Casual conversation is also a valid goal; do not turn every chat into a work plan.


## Active Architecture Baseline

- **Current test LLM:** Henk reports testing `Gemma 4 E4B`, not the Q8 build yet. The exact Ollama tag/quantization has not been verified. Earlier logs refer to `gemma4:e2b`; keep those historical runs distinct.
- **External memory:** Per-user, cross-session memory is managed by the Odysseus harness, with ChromaDB-backed vector retrieval. The harness chooses and injects memory context; the model itself remains stateless between sessions.
- **Search infrastructure:** SearXNG provides live web lookup beyond the model's static knowledge.
- **Image-generation checkpoint:** Henk tentatively identified `CyberRealisticCLASSIC_V5_FINAL-FP16.safetensors`. The Hugging Face model listing identifies this as a Stable Diffusion 1.5 text-to-image checkpoint, not a vision-language model. The actual vision model, if configured, still needs confirmation. [Model listing](https://huggingface.co/cyberdelia/CyberRealisticClassic).
- **Runtime boundary:** `C:\Users\hpvan\odysseus-main` on Windows is the source of truth. WSL is the later pull-and-test environment; do not make WSL-only changes or reset it before the Windows slice is ready.

## Implemented layers

- **Tool use and web:** Native tool execution is the source of truth; textual imitation detection and bounded recovery reduce fake tool calls. Web/music lookup has worked well in Henk's live tests. A run is only counted as a tool success when the tool event and returned result confirm it.
- **Conversation policy:** `src/tool_policy.py` includes default handling for casual chat, requested distractions, and ordinary low-mood disclosures: stay with the user's intent, don't infer a web search or work task, and allow the user to choose whether to talk or be distracted. These are harness defaults, separate from learned memory and interaction evidence.
- **External-context gate:** After untrusted web or provider content enters a run, higher-impact follow-up tools can require exact approval. Image generation (`generate_image` and the built-in `mcp__image_gen__generate_image`) was narrowly allowed to continue without that extra gate in this work; code execution, private reads, external actions, admin changes, and destructive actions remain gated.
- **Memory and experience:** Memory is owner-scoped and cross-session. Slice 1 records short, explicit corrections, boundaries, positive feedback, social signals, and confirmed outcomes as evidence entries, without an LLM call or copying transcripts. Slice 2 reflects repeated evidence into inspectable traits. Both are opt-in and use the existing memory privacy boundaries.
- **Slice 3 interaction adaptation:** `services/memory/emotional_state.py` derives advisory `repair`, `rapport`, and `success` signals from persisted experience evidence. They persist per owner, decay with a 30-day half-life, and can be reset. This layer does not infer feelings or claim a subjective inner state.
- **Affective layer:** `services/memory/affective_state.py` is separate from Slice 3. When opted in, it can retain a short-lived simulated Odysseus task affect from completed tool outcomes and a broad label from a short, explicit first-person user emotion report. It stores labels/state, not transcript text; the user and assistant states stay separate and decay on different time scales. Current toggles include `assistant_emotions_enabled` and `user_emotion_recording_enabled`; reset is owner-scoped.
- **Lifecycle and privacy controls:** `auto_experience`, `auto_reflection`, `emotional_state_enabled`, `assistant_emotions_enabled`, and `user_emotion_recording_enabled` are distinct controls. Experience/reflection/adaptation are suppressed in incognito, compare, no-memory, or blocked background-extraction flows as applicable. Affect sidecars are owner-scoped and written under the shared memory lock with temporary-file replacement. The settings UI exposes reset controls.
- **Voice interface:** Browser TTS/STT settings and a full-screen Voice with Odysseus view are present. Voice themes are user-configurable. The dot-matrix face is currently a lightweight canvas drawing; this change adds shallow 3D-like dot projection, mock yaw/tilt/bob, blinking, gaze drift, and a speaking mouth animation. It is not phoneme-accurate lip sync.
- **Voice approvals:** Approval and clarification cards can now be answered inside the voice screen and return to chat when voice mode closes. Hands-free re-listening pauses while a choice is pending.

## Current work and next checks

- Manually check the new voice-face motion and voice-screen approval interaction in the running app; these UI changes have not yet been visually checked in the app.
- Verify the new admin-only Brain interaction-insights panel in the running app. It reports current decayed interaction guidance, recent explicit user/assistant affect, and timestamped feedback-signal counts. The API is admin-gated and omits transcript text, trigger phrases, and message IDs. Treat it as observability of stored state, not an empathy or response-quality score.
- Test mobile STT after pulling the Windows changes into WSL. The recorder now chooses a supported `MediaRecorder` MIME type, passes the actual file name/type through the STT route, and surfaces unsupported/failed browser speech recognition. This has not been checked on a real phone; Local Whisper or an STT endpoint is the server-side fallback when the phone browser cannot transcribe.
- Continue evaluating the personality and emotional layers across real chat logs over time; one day's prompts cannot prove that persistent adaptation is behaving well.
- Retest image generation with the active MCP server after the recent alias, external-context-gate, and gallery-render fixes. Earlier WSL logs showed native calls reported as `diffuser:generate_image`, `diffuser__generate_image`, or even `diffuser`, which Odysseus rejected as unknown. Verify the actual tool event and rendered result.
- Investigate image over-generation from Voice mode: Henk's latest run produced three images after requesting one. It is not yet known whether the model emitted multiple tool calls, the client retried, or the UI rendered one result more than once. Compare native tool-call count, MCP execution count, saved image count, and rendered chat items for a single request.
- Revisit intermittent browser STT transcription failures and the failed “save chat as transcript document” action; copying a chat worked, transcript export did not in Henk's report.
- Investigate the embedding lane: startup logs showed FastEmbed/CUDA CUBLAS failing because the GPU lacks a required architectural feature, leaving ChromaDB vector memory degraded. Henk has a small embedding model but has not found a way to select it in Odysseus.
- Confirm the exact active Ollama LLM tag and whether a separate vision-language model is configured. The named CyberRealistic checkpoint is for text-to-image generation, not vision input.
- After Windows work is ready, pull it into WSL, then perform Henk's planned clean memory/skill wipe and live test. Do not use preloaded profile facts during the clean-slate personality test; that would confound whether Odysseus learns them itself.

## Recent Windows-source changes to verify

These edits were made in the Windows source-of-truth working copy during the 2026-10-21 development session. They have not been tested in the running WSL environment:

- The post-image chat render path no longer references undefined `_isBg`; generated-image results now trigger the normal render path.
- MCP-generated image filenames using the `mcp-<hex-id>.png` form are accepted by the generated-image resolver. MCP image content is saved through the canonical generated-image path so the Gallery can serve it.
- Mobile voice recording selects a browser-supported audio type instead of forcing WebM. The uploaded filename and MIME type are forwarded to local/API STT handling, and browser transcription errors are shown to the user.
- Brain → Settings now contains an admin-only Interaction insights panel. It graphs decayed `repair`, `rapport`, and `success` adaptation values, current explicit affect snapshots, and recent feedback signal activity. It deliberately does not retain or display chat text for this view.

The Git metadata directory is present, but Git status/diff commands from this Codex workspace were denied by filesystem permissions. Treat these working-copy changes as uncommitted until Henk verifies them in VS Code or another authorized Git environment. No commit or push was made here.


## Current evaluation setup

- Current live model: Gemma 4 E4B per Henk; do not describe it as the earlier Gemma E2B Q8 baseline. Exact runtime tag and quantization remain to be checked in Ollama.
- Evaluate from conversation logs over time: tool selection/execution, relevant memory and experience recall, emotion handling, conversational fit, and whether the final answer matches actual tool results.
- Mark subjective judgments as observations, not objective pass/fail evidence. A correct-looking answer alone does not prove a tool ran.
- For the personality/emotion layer, evaluate the timeline and full chat logs together: recorded signals can show what state changed, while the transcript is still needed to judge whether the assistant responded appropriately. Track false emotion attribution, unwanted work/tool pivots, missed boundaries, repeated mistakes, and useful continuity as separate observations.

## Sharing with constrained-hardware users

Henk is considering publishing the work so owners of constrained hardware can benefit. Odysseus already has a public upstream referenced by the README; before publishing this working copy, verify the intended remote/branch and review local changes for credentials, real user memories, transcripts, logs, and machine-specific paths. Useful public-facing follow-up work would be clear CPU-only setup guidance, embedding/STT fallback behavior, and documented VRAM/RAM tradeoffs. No push was made.

## Verification and current caveats

- **Historical Gemma E2B Q8 results:** Henk reported strong tool-use results and successful preference extraction/correction/recall in earlier tests. Those results are not an evaluation of the current Gemma 4 E4B test setup.
- **Historical memory behavior:** Memory extraction was changed to run after each eligible response, correction/replacement behavior was added, and Henk confirmed corrected preferences and cross-chat project goals were saved and recalled. Current memory behavior still needs evaluation after the planned clean wipe.
- **Earlier web evaluation:** Search formatting and follow-up behavior were improved and Henk reported a multi-constraint Cape Town search as a pass. This predates the current Gemma 4 E4B evaluation and is not a current baseline.
- **Repository metadata:** The `.git` directory is present, but this workspace could not run Git status/diff because filesystem permissions denied Git access. Commit state is unverified; treat edits as uncommitted until checked in VS Code or another authorized Git environment.
- **Known peripheral MCP issue:** Sequential Thinking returned blank errors in earlier tests across multiple models. It has not blocked the tested memory, web, or image-generation paths.
- **This turn:** No automated or live-app tests have been run for the voice approval or dot-face changes. Verify them in the running app before relying on them.

## Safety while working

Do not switch branches, reset, stash, pull, or overwrite user data without explicit approval. Keep unrelated batch-file and Chroma changes out of commits unless they are intentionally part of the work.
