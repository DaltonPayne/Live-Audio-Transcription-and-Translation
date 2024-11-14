import requests
import base64
import sounddevice as sd
import numpy as np
from scipy.io.wavfile import write
from tempfile import NamedTemporaryFile
from threading import Thread
from queue import Queue
import webrtcvad
import time
import tkinter as tk
from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()


RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY")
RUNPOD_ENDPOINT_ID = os.getenv("RUNPOD_ENDPOINT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


audio_queue = Queue()
transcriptions = []
translations = []

# Configuration for Voice Activity Detection
SAMPLE_RATE = 16000  # Use 16kHz for webrtcvad compatibility
FRAME_DURATION = 30  # Duration of each frame in ms
VAD_MODE = 1         # Less sensitive to background noise
SILENCE_DURATION = 3 # Duration in seconds to consider as silence
MIN_LOUDNESS_THRESHOLD = 500  # Ignore very low-intensity sounds
WORKER_COUNT = 4     # Number of processing worker threads

vad = webrtcvad.Vad(VAD_MODE)

def detect_voice_activity():
    recording = False
    silence_start_time = None
    audio_buffer = []

    while True:
        frames = sd.rec(int(FRAME_DURATION / 1000 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype='int16')
        sd.wait()
        
        frame = frames.flatten().tobytes()
        loudness = np.max(np.abs(frames))
        is_speech = vad.is_speech(frame, SAMPLE_RATE) and loudness > MIN_LOUDNESS_THRESHOLD

        if is_speech:
            if not recording:
                recording = True
                silence_start_time = None
                audio_buffer = []

            audio_buffer.extend(frames)

        elif recording:
            if silence_start_time is None:
                silence_start_time = time.time()
            elif time.time() - silence_start_time >= SILENCE_DURATION:
                save_audio_segment(audio_buffer)
                recording = False
                audio_buffer = []

def save_audio_segment(audio_buffer):
    with NamedTemporaryFile(delete=False, suffix=".wav") as temp_audio_file:
        write(temp_audio_file.name, SAMPLE_RATE, np.array(audio_buffer, dtype='int16'))
        audio_queue.put(temp_audio_file.name)

def process_audio():
    while True:
        audio_path = audio_queue.get()
        
        with open(audio_path, 'rb') as audio_file:
            encoded_audio = base64.b64encode(audio_file.read()).decode('utf-8')
        
        url = f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT_ID}/runsync"
        headers = {
            "Authorization": f"Bearer {RUNPOD_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "input": {
                "audio_base64": encoded_audio,
                "model": "base",
                "transcription": "plain_text",
                "language": None,
                "enable_vad": True,
                "word_timestamps": False
            }
        }

        try:
            response = requests.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                result = response.json()
                transcription = result.get("output", {}).get("transcription", "").strip()
                if transcription:
                    update_transcription_display(transcription)
                    translate_and_display(transcription)
            else:
                print(f"API request failed with status code {response.status_code}")
        except Exception as e:
            print("Error in processing audio:", e)
        
        audio_queue.task_done()

def update_transcription_display(new_transcription):
    if not new_transcription:
        return
    transcriptions.append(new_transcription)
    if len(transcriptions) > 10: #Store the last five transcriptions
        transcriptions.pop(0)

    transcription_text_content = "\n\n".join(transcriptions)
    transcription_label.config(text=transcription_text_content)
    root.update_idletasks()

def translate_and_display(text):
    try:
        chat_completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "user", "content": f"Translate the following text to Spanish: {text}"}
            ]
        )
        translation = chat_completion.choices[0].message.content.strip()
        update_translation_display(translation)
    except Exception as e:
        print("Error in translation:", e)

def update_translation_display(new_translation):
    if not new_translation:
        return

    translations.append(new_translation)
    if len(translations) > 10:
        translations.pop(0)

    translation_text_content = "\n\n".join(translations)
    translation_label.config(text=translation_text_content)
    translation_window.update_idletasks()

def adjust_font_and_wrap(event):
    width, height = event.width, event.height
    font_size = max(10, int(min(width / 30, height / 20)))
    transcription_label.config(font=("Helvetica", font_size), wraplength=width - 40)
    translation_label.config(font=("Helvetica", font_size), wraplength=width - 40)

def setup_gui():
    global transcription_label, translation_label, root, translation_window
    root = tk.Tk()
    root.title("Live Transcription")
    root.configure(bg="black")
    root.geometry("800x400")

    root.bind("<Configure>", adjust_font_and_wrap)
    transcription_frame = tk.Frame(root, bg="white", padx=2, pady=2)
    transcription_frame.pack(expand=True, fill="both", padx=10, pady=10)

    transcription_label = tk.Label(
        transcription_frame, 
        text="", 
        font=("Helvetica", 16), 
        fg="white", 
        bg="black", 
        wraplength=root.winfo_width() - 40,
        justify="center", 
        anchor="s",
        padx=20,
        pady=20
    )
    transcription_label.pack(expand=True, fill="both")

    translation_window = tk.Toplevel(root)
    translation_window.title("Spanish Translation")
    translation_window.configure(bg="black")
    translation_window.geometry("800x400")

    translation_frame = tk.Frame(translation_window, bg="white", padx=2, pady=2)
    translation_frame.pack(expand=True, fill="both", padx=10, pady=10)

    translation_label = tk.Label(
        translation_frame, 
        text="", 
        font=("Helvetica", 16), 
        fg="white", 
        bg="black", 
        wraplength=translation_window.winfo_width() - 40,
        justify="center", 
        anchor="s",
        padx=20,
        pady=20
    )
    translation_label.pack(expand=True, fill="both")

    vad_thread = Thread(target=detect_voice_activity, daemon=True)
    vad_thread.start()
    
    for _ in range(WORKER_COUNT):
        process_thread = Thread(target=process_audio, daemon=True)
        process_thread.start()
    
    root.mainloop()

if __name__ == "__main__":
    setup_gui()
