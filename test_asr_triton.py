import io
import numpy as np
import soundfile as sf
from tritonclient.grpc import InferenceServerClient, InferInput, InferRequestedOutput

# 1) Load audio as float32 mono
wav_path = r"C:\Users\thanglq12\Documents\tmp__bz5lrw_right.wav"
with open(wav_path, "rb") as f:
    audio_bytes = f.read()

audio, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=False)
if audio.ndim == 2:  # stereo -> mono
    audio = audio.mean(axis=1)

# **QUICK FIX: Truncate to first 2000 frames**
MAX_FRAMES = 2000
if len(audio) > MAX_FRAMES:
    audio = audio[2000:4000]

# 2) Add explicit batch dimension (B=1)
audio_batched = np.expand_dims(audio, axis=0)          # shape (1, T)
sr_batched    = np.array([[sr]], dtype=np.int32)       # shape (1, 1)

# 3) Build Triton inputs
inp_audio = InferInput("AUDIO_SIGNAL", audio_batched.shape, "FP32")
inp_audio.set_data_from_numpy(audio_batched)

inp_sr = InferInput("SAMPLE_RATE", sr_batched.shape, "INT32")
inp_sr.set_data_from_numpy(sr_batched)

# 4) Request output and infer
client = InferenceServerClient("aic-ezcallbot-stt.aws.coreai.vpbank.dev:443", ssl=True)
out_transcript = InferRequestedOutput("TRANSCRIPT")

result = client.infer(
    model_name="asr_ctc_bpe_ensemble",
    inputs=[inp_audio, inp_sr],
    outputs=[out_transcript],
)

# 5) Parse output
texts = result.as_numpy("TRANSCRIPT")
text0 = texts[0, 0].decode("utf-8") if isinstance(texts[0, 0], (bytes, bytearray)) else str(texts[0, 0])
print(text0)