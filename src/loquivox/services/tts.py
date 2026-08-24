"""
Text-to-speech service using Groq Orpheus.
"""
from __future__ import annotations

import threading

import sounddevice as sd
from scipy.io import wavfile

from loquivox.api import get_client
from loquivox.config import CFG
from loquivox.state import STATE


class TTSService:
    """Text-to-speech service using Groq Orpheus."""

    @staticmethod
    def speak(text: str, *, wait: bool = False, force: bool = False) -> None:
        """
        Convert text to speech and play it (async by default).

        ``force`` speaks even when the TTS toggle is off — talk mode is a spoken
        conversation, so its replies are read aloud regardless. ``wait`` plays
        synchronously and only returns once playback is over: talk mode records
        again right after, and must not capture its own voice. NEVER call it
        with ``wait=True`` from the GTK main thread.
        """
        if not text or not (force or STATE.tts_enabled):
            return

        if wait:
            TTSService._synthesize_and_play(text)
        else:
            threading.Thread(
                target=TTSService._synthesize_and_play, args=(text,), daemon=True
            ).start()

    @staticmethod
    def _synthesize_and_play(text: str) -> None:
        """Synthesize ``text`` and play it to the end (blocking). Never raises."""
        try:
            response = get_client().audio.speech.create(
                model=CFG.MODEL_TTS,
                voice=STATE.tts_voice,
                input=text[:CFG.TTS_MAX_CHARS],
                response_format="wav"
            )
            response.write_to_file(CFG.TEMP_TTS_PATH)
            # Play via sounddevice/PortAudio (already a dependency for
            # recording) instead of shelling out to `aplay`, so the app
            # needs no external ALSA binary at runtime.
            samplerate, data = wavfile.read(CFG.TEMP_TTS_PATH)
            sd.play(data, samplerate)
            sd.wait()
        except Exception as e:
            print(f"❌ TTS Error: {e}")

    @staticmethod
    def toggle() -> None:
        """Toggle TTS enabled state."""
        STATE.tts_enabled = not STATE.tts_enabled
        # Late import to avoid circular dependency
        from loquivox.managers.chat import ChatManager
        ChatManager.refresh_overlay()
