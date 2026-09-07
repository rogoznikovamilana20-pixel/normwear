import io
import json
import logging
import subprocess
import wave

log = logging.getLogger("speech")

_model = None


def _model_dir():
    from app.config import BASE_DIR

    d = BASE_DIR / "data" / "vosk" / "vosk-model-small-ru-0.22"
    return d if d.exists() else None


def is_available() -> bool:
    if _model_dir() is None:
        return False
    try:
        import vosk  # noqa: F401
        import imageio_ffmpeg  # noqa: F401

        return True
    except Exception:
        return False


def _get_model():
    global _model
    if _model is None:
        from vosk import Model

        _model = Model(str(_model_dir()))
    return _model


def transcribe_ogg(ogg_bytes: bytes) -> str | None:
    """Голосовое .ogg -> текст (офлайн, Vosk ru-small)."""
    try:
        import imageio_ffmpeg
        from vosk import KaldiRecognizer
    except Exception as e:
        log.warning("vosk unavailable: %s", e)
        return None
    if _model_dir() is None:
        return None
    try:
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        proc = subprocess.run(
            [ffmpeg, "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
            input=ogg_bytes,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        wf = wave.open(io.BytesIO(proc.stdout), "rb")
        rec = KaldiRecognizer(_get_model(), wf.getframerate())
        rec.SetWords(False)
        text = ""
        while True:
            data = wf.readframes(4000)
            if not data:
                break
            if rec.AcceptWaveform(data):
                text += " " + json.loads(rec.Result()).get("text", "")
        text += " " + json.loads(rec.FinalResult()).get("text", "")
        return text.strip() or None
    except Exception as e:
        log.warning("transcribe failed: %s", e)
        return None
