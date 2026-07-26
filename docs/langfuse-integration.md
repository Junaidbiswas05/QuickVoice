# Langfuse Integration

This document covers how Langfuse is integrated into the QuickVoice AI worker for tracing and evaluation.

## Setup
Add these to your `apps/ai/.env.dev` to enable tracing:
```
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```
If you don't add them, the app will just skip tracing instead of crashing.

## How it works
The integration logic lives in `apps/ai/handlers/langfuse_handler.py`.

1. **Initialization:** We initialize a singleton Langfuse client in `init_langfuse()`.
2. **OTel Tracing:** LiveKit has native OpenTelemetry support. Instead of wrapping every LLM turn manually (which is messy), we use `LangfuseSpanProcessor` to automatically push the LiveKit LLM, STT, and TTS spans to Langfuse.
3. **Session Tracing:** `open_langfuse_trace()` creates a root trace tagged with the `call_id` and `agent_id` at the start of the session.
4. **Scoring:** When a call ends, `attach_langfuse_evaluation()` runs a couple basic rules to score the call (e.g. if the transcript isn't empty, and if the call lasted longer than 5 seconds). These get attached to the trace as metrics.
