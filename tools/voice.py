from dotenv import load_dotenv
import os
from groq import Groq
import speech_recognition as sr
import tempfile
import wave
from piper import PiperVoice
import platform
import subprocess
import select
import sys
import time
from pathlib import Path

base_dir = Path(__file__).parent.parent
model_path = base_dir / "assets" / "voices" / "en_US-lessac-medium.onnx"

piper_voice = PiperVoice.load(str(model_path))

_play_proc = None

load_dotenv()

groq_api_key = os.environ.get("GROQ_API_KEY")

if not groq_api_key:
    print("Groq API key is not set")
    groq_client = None
else:
    groq_client = Groq(api_key = groq_api_key)

def listen_once():

    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = 1.5

    with sr.Microphone() as source:
        print("Calibrating mic for background noise...")
        recognizer.adjust_for_ambient_noise(source,duration=1.0)
        print("Energy threshold set to: ",recognizer.energy_threshold)
        print("Listening...")

        try:
            audio = recognizer.listen(source,timeout=15,phrase_time_limit=30)
        except sr.WaitTimeoutError:
            return ""

    wav_path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name

    with open(wav_path, "wb") as f:
        f.write(audio.get_wav_data())

    try:
        if groq_client is None:
            return "Groq client is not set"
        
        with open(wav_path,"rb") as f:
            result = groq_client.audio.transcriptions.create(
                file = f,
                model = "whisper-large-v3",
                language = "en",
            )
        
        return result.text
    
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

def speak(text):
    if not text:
        return
    
    wav_path = tempfile.NamedTemporaryFile(suffix=".wav",delete = False).name

    try:
        with wave.open(wav_path,"wb") as wav_file:
            piper_voice.synthesize_wav(text,wav_file)

        system = platform.system()

        global _play_proc
        stop_speaking()

        if system == "Darwin":
            _play_proc = subprocess.Popen(["afplay",wav_path])
        elif system == "Windows":
            import winsound
            winsound.PlaySound(wav_path,winsound.SND_FILENAME)
            return
        else:
            _play_proc = subprocess.Popen(["aplay",wav_path])
    
        print("Speaking... (press enter to skip)")
        wait_for_playback()

    finally:
        stop_speaking()

        if os.path.exists(wav_path):
            os.remove(wav_path)

def wait_for_playback():
    global _play_proc

    if _play_proc is None:
        return

    can_watch_keys = sys.stdin.isatty()

    while _play_proc.poll() is None:
        if can_watch_keys and select.select([sys.stdin], [], [], 0.1)[0]:
            sys.stdin.readline()
            stop_speaking()
            return

        if not can_watch_keys:
            time.sleep(0.1)

    _play_proc = None

def stop_speaking():
    global _play_proc
    if _play_proc is not None and _play_proc.poll() is None:
        _play_proc.terminate()
        _play_proc = None

if __name__ == "__main__":
    print(sr.Microphone.list_microphone_names())