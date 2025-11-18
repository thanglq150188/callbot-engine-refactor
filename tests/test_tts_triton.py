import numpy as np
import soundfile as sf
from tritonclient.grpc import InferenceServerClient, InferInput, InferRequestedOutput

# TTS endpoint
TTS_SERVER_URL = "aic-ezcallbot-tts.aws.coreai.vpbank.dev:443"

# Text to synthesize
text = "Xin chào, tôi là trợ lý ảo của VPBank. Tôi có thể giúp gì cho anh chị hôm nay?"

# 1) Prepare input text with batch dimension
text_batched = np.array([[text]], dtype=object)  # shape (1, 1)

# 2) Build Triton inputs
inp_text = InferInput("TEXT", text_batched.shape, "BYTES")
inp_text.set_data_from_numpy(text_batched)

# Optional: Add TTS parameters if your model supports them
# speaker_id = np.array([[1]], dtype=np.int32)
# inp_speaker = InferInput("SPEAKER_ID", speaker_id.shape, "INT32")
# inp_speaker.set_data_from_numpy(speaker_id)

# 3) Request output
client = InferenceServerClient(TTS_SERVER_URL, ssl=True)
out_audio = InferRequestedOutput("AUDIO")
# out_audio_length = InferRequestedOutput("AUDIO_LENGTH")  # if available

# 4) Infer
try:
    result = client.infer(
        model_name="fastspeech2",  # Adjust model name based on your setup
        inputs=[inp_text],  # Add inp_speaker if needed
        outputs=[out_audio],
    )
    
    # 5) Parse output audio
    audio_data = result.as_numpy("AUDIO")
    
    # audio_data shape is typically (1, num_samples) or (1, num_samples, 1)
    # Squeeze to get 1D array
    if audio_data.ndim == 3:
        audio_data = audio_data.squeeze()  # (num_samples,)
    elif audio_data.ndim == 2:
        audio_data = audio_data[0]  # (num_samples,)
    
    print(f"Generated audio shape: {audio_data.shape}")
    print(f"Audio dtype: {audio_data.dtype}")
    print(f"Audio range: [{audio_data.min()}, {audio_data.max()}]")
    
    # 6) Save audio to file
    output_path = "output_tts.wav"
    
    # Convert to int16 if needed (based on your TTS service code)
    if audio_data.dtype == np.float32:
        # Assuming audio is in range [-1, 1]
        audio_int16 = (audio_data * 32767).astype(np.int16)
    else:
        audio_int16 = audio_data
    
    sf.write(output_path, audio_int16, samplerate=16000)
    print(f"Audio saved to: {output_path}")
    
    # Optional: Get audio duration
    duration_sec = len(audio_int16) / 16000
    print(f"Audio duration: {duration_sec:.2f}s")
    
except Exception as e:
    import traceback
    print(f"Error during TTS inference: {e}")
    print(traceback.format_exc())