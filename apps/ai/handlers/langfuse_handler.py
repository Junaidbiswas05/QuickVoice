import os
from langfuse import Langfuse
from opentelemetry.trace import TracerProvider
from opentelemetry import trace
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
import base64
from utils.logger import logger
from typing import Optional

_langfuse_client: Optional[Langfuse] = None

def init_langfuse():
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client

    pub = os.getenv("LANGFUSE_PUBLIC_KEY")
    sec = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    
    if not (pub and sec):
        logger.info("[LANGFUSE] Missing credentials, tracing is disabled.")
        return None

    try:
        # initialize the singleton client
        _langfuse_client = Langfuse(public_key=pub, secret_key=sec, host=host)
        
        # hook up livekit's spans to langfuse via otel
        auth_header = "Basic " + base64.b64encode(f"{pub}:{sec}".encode()).decode()
        exporter = OTLPSpanExporter(
            endpoint=f"{host}/api/public/otel/v1/traces",
            headers={"Authorization": auth_header}
        )
        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        
        logger.info("[LANGFUSE] initialized successfully.")
    except Exception as e:
        logger.warning(f"[LANGFUSE] init failed: {e}")
        _langfuse_client = None

    return _langfuse_client

def get_langfuse_client() -> Optional[Langfuse]:
    return _langfuse_client

def open_langfuse_trace(call_context: dict, config: dict):
    client = get_langfuse_client()
    if not client:
        return

    try:
        call_id = call_context.get("call_id")
        if not call_id:
            logger.warning("[LANGFUSE] no call_id found.")
            return

        direction = call_context.get("direction", "unknown")
        
        client.trace(
            id=call_id,
            name="voice_session",
            session_id=call_id,
            user_id=call_context.get("agent_id"),
            tags=[direction],
            metadata={
                "agent_number": call_context.get("agent_number"),
                "provider": call_context.get("provider"),
            }
        )
    except Exception as e:
        logger.warning(f"[LANGFUSE] failed to open trace: {e}")

def attach_langfuse_evaluation(call_context: dict, payload: dict):
    client = get_langfuse_client()
    if not client:
        return

    try:
        call_id = call_context.get("call_id")
        if not call_id:
            return

        # check if we actually got any conversation
        transcripts = payload.get("transcripts", [])
        turns_count = len(transcripts)
        client.score(
            trace_id=call_id,
            name="transcript_non_empty",
            value=1.0 if turns_count > 0 else 0.0,
            comment=f"turns: {turns_count}"
        )
        
        # basic success check based on duration
        duration = payload.get("durationSeconds", 0)
        client.score(
            trace_id=call_id,
            name="call_success",
            value=1.0 if duration > 5 else 0.0,
            comment=f"duration: {duration}s"
        )

        client.flush()
    except Exception as e:
        logger.warning(f"[LANGFUSE] failed to attach score: {e}")
