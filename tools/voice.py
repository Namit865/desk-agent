from dotenv import load_dotenv
import os
from groq import Groq
import speech_recognition as sr
import tempfile
import wave
from piper import PiperVoice
import platform
import subprocess
from pathlib import Path

base_dir = Path(__file__).parent.parent
model_path = base_dir / "assets" / "voices" / "en_US-lessac-medium.onnx"

load_dotenv()

groq_api_key = os.environ.get("GROQ_API_KEY")

if not groq_api_key:
    print("Groq API key is not set")
    groq_client = None
else:
    groq_client = Groq(api_key = groq_api_key)

def listen_once():

    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = 0.8

    with sr.Microphone(device_index=0,sample_rate=16000) as source:
        print("Calibrating mic for background noise...")
        recognizer.adjust_for_ambient_noise(source,duration=1.0)
        print("Energy threshold set to: ",recognizer.energy_threshold)
        print("Listening...")
        audio = recognizer.listen(source,timeout=5,phrase_time_limit=10)
        recognizer.pause_threshold = 0.8

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
            )
        
        return result.text
    
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

def speak(text):
    if not text:
        return
    
    voice = PiperVoice.load(str(model_path))
    wav_path = tempfile.NamedTemporaryFile(suffix=".wav",delete = False).name

    try:
        with wave.open(wav_path,"wb") as wav_file:
            voice.synthesize_wav(text,wav_file)

        system = platform.system()

        if system == "Darwin":
            subprocess.run(["afplay",wav_path],check=False)
        elif system == "Windows":
            import winsound
            winsound.PlaySound(wav_path,winsound.SND_FILENAME)
        else:
            subprocess.run(["aplay",wav_path],check=False)
    
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

if __name__ == "__main__":
    print(sr.Microphone.list_microphone_names())