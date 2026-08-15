#!/usr/bin/env python3
"""Speech in/out for the Pi: whisper.cpp transcription + Piper TTS.

Shared by pibot (Telegram voice notes) now and the room-voice phase later.
Everything is subprocess + ffmpeg; no Python audio deps.

    from voicebox import transcribe, speak, available
    text = transcribe("/tmp/note.oga")          # any ffmpeg-readable audio
    ogg  = speak("Good evening, sir.", "/tmp/reply.ogg")   # OGG/Opus out
"""
import os
import subprocess

VOICE_DIR = os.path.expanduser("~/voice")
WHISPER = os.path.join(VOICE_DIR, "whisper.cpp/build/bin/whisper-cli")
WHISPER_MODEL = os.path.join(VOICE_DIR, "models/ggml-base.en.bin")
PIPER = os.path.join(VOICE_DIR, "piper/piper")
PIPER_VOICE = os.path.join(VOICE_DIR, "models/en_GB-alan-medium.onnx")
FFMPEG = "ffmpeg"


class VoiceError(Exception):
    pass


def stt_available():
    return os.path.exists(WHISPER) and os.path.exists(WHISPER_MODEL)


def tts_available():
    return os.path.exists(PIPER) and os.path.exists(PIPER_VOICE)


def available():
    return stt_available() and tts_available()


def transcribe(audio_path, timeout=180):
    """Audio file (ogg/opus, mp3, wav, ...) -> text. Raises VoiceError."""
    if not stt_available():
        raise VoiceError("whisper.cpp or its model is missing under ~/voice")
    wav = audio_path + ".16k.wav"
    try:
        r = subprocess.run(
            [FFMPEG, "-y", "-loglevel", "error", "-i", audio_path,
             "-ar", "16000", "-ac", "1", wav],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise VoiceError(f"ffmpeg failed: {r.stderr.strip()[:200]}")
        r = subprocess.run(
            [WHISPER, "-m", WHISPER_MODEL, "-f", wav, "-nt", "--no-prints"],
            capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise VoiceError(f"whisper failed: {r.stderr.strip()[:200]}")
        return " ".join(r.stdout.split()).strip()
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def speak(text, out_ogg, timeout=120):
    """Text -> OGG/Opus file (Telegram sendVoice format). Raises VoiceError."""
    if not tts_available():
        raise VoiceError("piper or its voice model is missing under ~/voice")
    wav = out_ogg + ".wav"
    try:
        r = subprocess.run(
            [PIPER, "--model", PIPER_VOICE, "--output_file", wav],
            input=text, capture_output=True, text=True, timeout=timeout,
            cwd=os.path.dirname(PIPER))  # so piper finds its espeak-ng-data
        if r.returncode != 0 or not os.path.exists(wav):
            raise VoiceError(f"piper failed: {r.stderr.strip()[:200]}")
        r = subprocess.run(
            [FFMPEG, "-y", "-loglevel", "error", "-i", wav,
             "-c:a", "libopus", "-b:a", "32k", "-application", "voip", out_ogg],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise VoiceError(f"ffmpeg opus encode failed: {r.stderr.strip()[:200]}")
        return out_ogg
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "say":
        print(speak(" ".join(sys.argv[2:]), "/tmp/voicebox_test.ogg"))
    elif len(sys.argv) == 2:
        print(transcribe(sys.argv[1]))
    else:
        print('usage: voicebox.py <audiofile>  |  voicebox.py say <text>')
        print(f"stt: {stt_available()}  tts: {tts_available()}")
