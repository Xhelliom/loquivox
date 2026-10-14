"""Two dictations in a row on a simulated streaming backend: the second must
reach the live session even when the key is released before any microphone
callback ran after the session opened (the "buffer too small" bug)."""
import _hermetic  # noqa: F401
import numpy as np
import sounddevice as sd

from loquivox.services import media
from loquivox.services.audio import AudioService
from loquivox.state import STATE
from loquivox.transcription import get_dispatcher


class Session:
    def __init__(self): self.samples = 0
    def feed(self, a): self.samples += len(a)


class Backend:
    stream_sample_rate = 24000


class Stream:
    """Microphone that never calls back: the release beats the first block."""
    def __init__(self, **kw): self.cb = kw["callback"]
    def start(self): pass
    def stop(self): pass
    def close(self): pass


def main() -> None:
    media.mic_opened = media.mic_closed = lambda *a, **k: None
    sd.InputStream = Stream
    sessions = []

    def start_stream(*a, **k):
        sessions.append(Session())
        return sessions[-1]
    d = get_dispatcher()
    d.streaming_backend = lambda: Backend()
    d.start_stream = start_stream

    for i in range(2):
        AudioService.start_recording()
        # blocks captured while the session was opening, before any catch-up
        STATE.audio_buffer.extend(np.zeros((2400, 1), "float32") for _ in range(5))
        AudioService.stop_recording()
        assert sessions[i].samples == 12000, f"dictée {i + 1}: {sessions[i].samples} échantillons reçus"
        STATE.stream_session = None
    print("ok")


if __name__ == "__main__":
    main()
