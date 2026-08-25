"""
Text-to-speech service — cloud (Groq Orpheus, OpenAI) or local (Piper).

Every engine speaks the same way: a stream of raw int16 PCM, played *while* it
is generated. A one-sentence reply takes ~2.6s to come back as a finished WAV
against ~0.5s to its first samples, and in a spoken conversation that
difference is the whole feel of the thing. It is also what makes barge-in
possible — an interruption abandons the stream mid-sentence instead of waiting
for a file that is already obsolete.

``CFG.TTS_ENGINES`` maps a model id to its provider and voices. A local engine
that is not installed degrades to the cloud one rather than going silent, and a
cloud model whose API has no PCM stream falls back to the whole-file path, once.
"""
from __future__ import annotations

import threading
import warnings
from contextlib import contextmanager
from typing import Optional

import sounddevice as sd
from scipy.io import wavfile

from loquivox.api import get_ai_client
from loquivox.config import CFG
from loquivox.state import STATE

#: the cloud engines stream raw 24 kHz mono int16, no header
CLOUD_PCM_RATE: int = 24000
#: the engine that runs on this machine (no client, no key)
LOCAL_PROVIDER: str = "piper"
#: models whose API refused a PCM stream — spoken as a whole file instead
_NO_PCM: set = set()
#: the local engine has been reported missing — one warning, not one per reply
_WARNED: bool = False


class TTSService:
    """Text-to-speech service; the chosen engine decides who speaks."""

    @staticmethod
    def _engine():
        """
        The ``(provider, model, voice)`` to synthesize with.

        No client is built here: the settings dialog and the self-checks ask
        this question without an API key at hand. A local engine that is not
        installed falls back to the configured cloud one — silence is never the
        answer to a missing optional dependency.
        """
        model = STATE.tts_model or CFG.MODEL_TTS
        provider, voices = CFG.TTS_ENGINES.get(model, ("groq", ()))
        # A voice from another engine would be rejected by the API — fall back
        # to that engine's first one, which is always valid.
        voice = STATE.tts_voice if STATE.tts_voice in voices else (
            voices[0] if voices else STATE.tts_voice)
        if provider == LOCAL_PROVIDER and not TTSService._local_ready():
            model = CFG.MODEL_TTS
            provider, voices = CFG.TTS_ENGINES.get(model, ("groq", ()))
            voice = voices[0] if voices else STATE.tts_voice
        return provider, model, voice

    @staticmethod
    def _local_ready() -> bool:
        """True when the local engine can actually speak; warns once if it can't."""
        global _WARNED
        from loquivox.services import tts_piper

        if tts_piper.is_available():
            return True
        if not _WARNED:
            _WARNED = True
            print(f"⚠️  {LOCAL_PROVIDER} is not installed — speaking through the "
                  "cloud engine (pip install piper-tts, or install piper)")
        return False

    @staticmethod
    @contextmanager
    def _pcm(provider: str, model: str, voice: str, text: str):
        """Yield ``(sample_rate, iterator of PCM bytes)`` from the chosen engine."""
        if provider == LOCAL_PROVIDER:
            from loquivox.services import tts_piper
            with tts_piper.stream(voice, text[:CFG.TTS_MAX_CHARS]) as source:
                yield source
            return
        with get_ai_client(provider).audio.speech.with_streaming_response.create(
            model=model, voice=voice, input=text[:CFG.TTS_MAX_CHARS],
            response_format="pcm",
        ) as response:
            yield CLOUD_PCM_RATE, response.iter_bytes(4096)

    @staticmethod
    def speak(text: str, *, wait: bool = False, force: bool = False,
              stop: Optional[threading.Event] = None,
              started: Optional[threading.Event] = None) -> None:
        """
        Convert text to speech and play it (async by default).

        ``force`` speaks even when the TTS toggle is off — talk mode is a spoken
        conversation, so its replies are read aloud regardless. ``wait`` plays
        synchronously and only returns once playback is over: talk mode records
        again right after, and must not capture its own voice. NEVER call it
        with ``wait=True`` from the GTK main thread.

        ``stop`` cuts playback short when it is set — the user talking over the
        reply. ``started`` is set the moment the first sample reaches the sound
        card, which is when the microphone is hearing the speakers and not one
        moment earlier: talk mode arms its barge-in detector on it.
        """
        if not text or not (force or STATE.tts_enabled):
            if started is not None:
                started.set()  # nothing will ever play — never leave a waiter hanging
            return

        if wait:
            TTSService._synthesize_and_play(text, stop, started)
        else:
            threading.Thread(
                target=TTSService._synthesize_and_play, args=(text, stop, started),
                daemon=True,
            ).start()

    @staticmethod
    def preview(text: str, *, model: str = "", voice: str = "") -> None:
        """
        Speak one line with a given engine and voice, saving nothing.

        The settings dialog's Test buttons: judging a voice by reading its name
        is guesswork, and switching to it to find out means switching back.
        """
        engine = None
        if model:
            engine = (CFG.TTS_ENGINES.get(model, ("groq", ()))[0], model, voice)
        threading.Thread(target=TTSService._synthesize_and_play,
                         args=(text, None, None, engine), daemon=True).start()

    @staticmethod
    def _stream_pcm(provider: str, model: str, voice: str, text: str,
                    stop: Optional[threading.Event] = None,
                    started: Optional[threading.Event] = None) -> bool:
        """
        Speak ``text`` as it is synthesized (blocking). Never raises.

        Returns False — once per model — when a cloud API has no PCM stream for
        it, which the caller reads as "play the whole file instead". A local
        engine has no such path, and a failure after the first samples is just a
        cut-short reply, not a reason to say the same thing twice.
        """
        if model in _NO_PCM:
            return False
        playing = False
        try:
            with TTSService._pcm(provider, model, voice, text) as (rate, chunks):
                with sd.RawOutputStream(samplerate=rate, channels=1,
                                        dtype="int16") as out:
                    tail = b""
                    for chunk in chunks:
                        if stop is not None and stop.is_set():
                            out.abort()  # cut NOW: draining would talk over them
                            return True
                        data = tail + chunk
                        cut = len(data) - len(data) % 2  # whole int16 frames only
                        if cut:
                            out.write(data[:cut])
                            playing = True
                            if started is not None:
                                started.set()
                        tail = data[cut:]
                    # stop() drains the buffer; letting the context close it
                    # would abort playback and clip the last word.
                    out.stop()
            return True
        except Exception as e:
            if playing or provider == LOCAL_PROVIDER:
                print(f"❌ TTS Error: {e}")
                return True
            _NO_PCM.add(model)
            print(f"ℹ️  No PCM stream for {model} ({e}) — playing the whole file")
            return False

    @staticmethod
    def _synthesize_and_play(text: str, stop: Optional[threading.Event] = None,
                             started: Optional[threading.Event] = None,
                             engine: Optional[tuple] = None) -> None:
        """
        Synthesize ``text`` and play it to the end (blocking). Never raises.

        ``engine`` overrides the saved one for this line only — see ``preview``.
        """
        try:
            provider, model, voice = engine or TTSService._engine()
            if TTSService._stream_pcm(provider, model, voice, text, stop, started):
                return
            response = get_ai_client(provider).audio.speech.create(
                model=model,
                voice=voice,
                input=text[:CFG.TTS_MAX_CHARS],
                response_format="wav"
            )
            response.write_to_file(CFG.TEMP_TTS_PATH)
            # Play via sounddevice/PortAudio (already a dependency for
            # recording) instead of shelling out to `aplay`, so the app
            # needs no external ALSA binary at runtime.
            # ponytail: OpenAI streams the WAV with a placeholder size in the
            # header, so scipy warns on every reply though it reads the file
            # whole. Silenced here, not globally.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", wavfile.WavFileWarning)
                samplerate, data = wavfile.read(CFG.TEMP_TTS_PATH)
            sd.play(data, samplerate)
            if started is not None:
                started.set()
            if stop is None:
                sd.wait()
            else:
                # Poll instead of sd.wait(): an interruption has to cut this
                # short, and the whole file is already in the sound card.
                while not stop.wait(0.03):
                    if not sd.get_stream().active:
                        return
                sd.stop()
        except Exception as e:
            print(f"❌ TTS Error: {e}")
        finally:
            if started is not None:
                started.set()  # a failed synthesis must not hang the caller

    @staticmethod
    def toggle() -> None:
        """Toggle TTS enabled state."""
        STATE.tts_enabled = not STATE.tts_enabled
        # Late import to avoid circular dependency
        from loquivox.managers.chat import ChatManager
        ChatManager.refresh_overlay()
