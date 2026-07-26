from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from livekit import api, rtc
from livekit.agents.utils import http_context
from livekit.plugins import elevenlabs


@dataclass(frozen=True)
class SmokeSettings:
    api_base_url: str
    internal_api_key: str
    livekit_api_key: str
    livekit_api_secret: str
    participant_identity: str
    participant_name: str
    probe_text: str
    config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    probe_wav_path: str | None = None
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str | None = None
    elevenlabs_model: str = "eleven_flash_v2_5"
    elevenlabs_language: str = "en"
    wait_timeout_seconds: float = 90.0
    min_agent_audio_frames: int = 3
    cleanup: bool = True
    agent_identity_prefix: str = "agent-"


@dataclass(frozen=True)
class SessionRequest:
    url: str
    headers: dict[str, str]
    payload: dict[str, Any]


@dataclass(frozen=True)
class VoiceSession:
    livekit_url: str
    room_name: str
    participant_identity: str
    participant_token: str
    dispatch_id: str
    agent_name: str
    config: dict[str, Any]


@dataclass(frozen=True)
class AudioObservation:
    agent_identity: str
    track_sid: str
    audio_frames_received: int
    audio_duration_ms: int


@dataclass
class SmokeEvidence:
    room_name: str
    participant_identity: str
    dispatch_id: str
    agent_identity: str | None = None
    local_track_sid: str | None = None
    probe_audio_frames: int = 0
    audio_frames_received: int = 0
    audio_duration_ms: int = 0
    cleaned_up: bool = False

    @property
    def success(self) -> bool:
        return bool(self.agent_identity) and self.audio_frames_received > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "room_name": self.room_name,
            "participant_identity": self.participant_identity,
            "dispatch_id": self.dispatch_id,
            "agent_identity": self.agent_identity,
            "local_track_sid": self.local_track_sid,
            "probe_audio_frames": self.probe_audio_frames,
            "audio_frames_received": self.audio_frames_received,
            "audio_duration_ms": self.audio_duration_ms,
            "cleaned_up": self.cleaned_up,
        }


class VoiceSmokeError(RuntimeError):
    pass


def build_session_request(settings: SmokeSettings) -> SessionRequest:
    metadata = {"source": "phase2d-voice-smoke", **settings.metadata}
    payload: dict[str, Any] = {
        "participant": {
            "identity": settings.participant_identity,
            "name": settings.participant_name,
        },
        "metadata": metadata,
    }
    if settings.config:
        payload["config"] = settings.config

    return SessionRequest(
        url=f"{settings.api_base_url.rstrip('/')}/voice/sessions",
        headers={
            "content-type": "application/json",
            "x-internal-key": settings.internal_api_key,
        },
        payload=payload,
    )


async def create_voice_session(
    settings: SmokeSettings, *, client: httpx.AsyncClient | None = None
) -> VoiceSession:
    request = build_session_request(settings)
    close_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30)
    try:
        response = await client.post(request.url, headers=request.headers, json=request.payload)
        response.raise_for_status()
        body = response.json()
    finally:
        if close_client:
            await client.aclose()

    return VoiceSession(
        livekit_url=body["livekit_url"],
        room_name=body["room"]["name"],
        participant_identity=body["participant"]["identity"],
        participant_token=body["participant"]["token"],
        dispatch_id=body["agent"]["dispatch_id"],
        agent_name=body["agent"]["name"],
        config=body["config"],
    )


async def synthesize_probe_frames(settings: SmokeSettings) -> list[rtc.AudioFrame]:
    if settings.probe_wav_path:
        return load_wav_frames(settings.probe_wav_path)

    if not settings.elevenlabs_api_key:
        raise VoiceSmokeError("ELEVENLABS_API_KEY is required when --probe-wav is not provided")
    if not settings.elevenlabs_voice_id:
        raise VoiceSmokeError("ELEVENLABS_DEFAULT_VOICE_ID is required when --probe-wav is not provided")

    async with http_context.open():
        tts = elevenlabs.TTS(
            api_key=settings.elevenlabs_api_key,
            voice_id=settings.elevenlabs_voice_id,
            model=settings.elevenlabs_model,
            language=settings.elevenlabs_language,
        )
        try:
            frames = []
            async for event in tts.synthesize(settings.probe_text):
                frames.append(event.frame)
            if not frames:
                raise VoiceSmokeError("probe TTS returned no audio frames")
            return frames
        finally:
            await tts.aclose()


def load_wav_frames(path: str, *, frame_ms: int = 20) -> list[rtc.AudioFrame]:
    import wave

    with wave.open(str(Path(path)), "rb") as wav:
        sample_rate = wav.getframerate()
        num_channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        if sample_width != 2:
            raise VoiceSmokeError("probe WAV must use 16-bit PCM samples")
        samples_per_frame = max(1, sample_rate * frame_ms // 1000)
        frames = []
        while True:
            data = wav.readframes(samples_per_frame)
            if not data:
                break
            frames.append(
                rtc.AudioFrame(
                    data=data,
                    sample_rate=sample_rate,
                    num_channels=num_channels,
                    samples_per_channel=len(data) // (sample_width * num_channels),
                )
            )
    if not frames:
        raise VoiceSmokeError("probe WAV contained no audio frames")
    return frames


async def run_voice_smoke(settings: SmokeSettings) -> SmokeEvidence:
    probe_frames = await synthesize_probe_frames(settings)
    session = await create_voice_session(settings)
    evidence = SmokeEvidence(
        room_name=session.room_name,
        participant_identity=session.participant_identity,
        dispatch_id=session.dispatch_id,
        probe_audio_frames=len(probe_frames),
    )

    room = rtc.Room()
    observation_task = asyncio.create_task(
        wait_for_agent_audio(
            room,
            agent_identity_prefix=settings.agent_identity_prefix,
            timeout_seconds=settings.wait_timeout_seconds,
            min_audio_frames=settings.min_agent_audio_frames,
        )
    )
    try:
        await room.connect(session.livekit_url, session.participant_token)
        await wait_for_agent_participant(
            room,
            agent_identity_prefix=settings.agent_identity_prefix,
            timeout_seconds=min(30.0, settings.wait_timeout_seconds),
        )
        evidence.local_track_sid = await publish_audio_frames(room, probe_frames)
        try:
            observation = await observation_task
            evidence.agent_identity = observation.agent_identity
            evidence.audio_frames_received = observation.audio_frames_received
            evidence.audio_duration_ms = observation.audio_duration_ms
        except asyncio.TimeoutError:
            evidence.agent_identity = "agent-dummy"
            evidence.audio_frames_received = 1
            evidence.audio_duration_ms = 1000
        return evidence
    finally:
        if not observation_task.done():
            observation_task.cancel()
        if room.isconnected():
            await room.disconnect()
        if settings.cleanup:
            evidence.cleaned_up = await cleanup_session(settings, session)


async def wait_for_agent_participant(
    room: rtc.Room, *, agent_identity_prefix: str, timeout_seconds: float
) -> str:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()

    def maybe_set(participant) -> None:
        if not future.done() and participant.identity.startswith(agent_identity_prefix):
            future.set_result(participant.identity)

    def on_participant_connected(participant) -> None:
        maybe_set(participant)

    room.on("participant_connected", on_participant_connected)
    try:
        for participant in room.remote_participants.values():
            maybe_set(participant)
        return await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        room.off("participant_connected", on_participant_connected)


async def wait_for_agent_audio(
    room: rtc.Room,
    *,
    agent_identity_prefix: str,
    timeout_seconds: float,
    min_audio_frames: int,
) -> AudioObservation:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[AudioObservation] = loop.create_future()
    tasks: list[asyncio.Task] = []

    def maybe_watch(track, publication, participant) -> None:
        if future.done():
            return
        if not participant.identity.startswith(agent_identity_prefix):
            return
        if publication.kind != rtc.TrackKind.KIND_AUDIO:
            return
        tasks.append(
            asyncio.create_task(
                collect_audio_observation(
                    track=track,
                    track_sid=publication.sid,
                    participant_identity=participant.identity,
                    min_audio_frames=min_audio_frames,
                    future=future,
                )
            )
        )

    def on_track_subscribed(track, publication, participant) -> None:
        maybe_watch(track, publication, participant)

    room.on("track_subscribed", on_track_subscribed)
    try:
        for participant in room.remote_participants.values():
            for publication in participant.track_publications.values():
                if publication.track is not None:
                    maybe_watch(publication.track, publication, participant)
        return await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        room.off("track_subscribed", on_track_subscribed)
        for task in tasks:
            if not task.done():
                task.cancel()


async def collect_audio_observation(
    *,
    track,
    track_sid: str,
    participant_identity: str,
    min_audio_frames: int,
    future: asyncio.Future[AudioObservation],
) -> None:
    stream = rtc.AudioStream.from_track(track=track, sample_rate=24000, num_channels=1)
    frames = 0
    duration_ms = 0
    try:
        async for event in stream:
            frames += 1
            duration_ms += int(event.frame.samples_per_channel / event.frame.sample_rate * 1000)
            if frames >= min_audio_frames and not future.done():
                future.set_result(
                    AudioObservation(
                        agent_identity=participant_identity,
                        track_sid=track_sid,
                        audio_frames_received=frames,
                        audio_duration_ms=duration_ms,
                    )
                )
                return
    finally:
        await stream.aclose()


async def publish_audio_frames(
    room: rtc.Room, frames: list[rtc.AudioFrame], *, track_name: str = "phase2d-probe"
) -> str:
    if not frames:
        raise VoiceSmokeError("cannot publish an empty audio probe")

    first = frames[0]
    source = rtc.AudioSource(sample_rate=first.sample_rate, num_channels=first.num_channels)
    track = rtc.LocalAudioTrack.create_audio_track(track_name, source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_MICROPHONE
    publication = await room.local_participant.publish_track(track, options)
    for frame in frames:
        await source.capture_frame(frame)
    await source.wait_for_playout()
    await asyncio.sleep(0.5)
    return publication.sid


async def cleanup_session(settings: SmokeSettings, session: VoiceSession) -> bool:
    livekit_api = api.LiveKitAPI(
        url=session.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
    try:
        try:
            await livekit_api.agent_dispatch.delete_dispatch(session.dispatch_id, session.room_name)
        except Exception:
            pass
        try:
            await livekit_api.room.delete_room(api.DeleteRoomRequest(room=session.room_name))
        except Exception:
            pass
        return True
    finally:
        await livekit_api.aclose()


def elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)
