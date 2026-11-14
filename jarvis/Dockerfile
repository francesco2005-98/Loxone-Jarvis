FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Models are baked in so the running container never downloads anything.
# german: primeline/whisper-large-v3-turbo-german via TheTobyB's CTranslate2 conversion, Apache-2.0.
# The tokenizer is the unchanged Whisper tokenizer, stored beside the weights for offline use.
ARG WHISPER_MODELS="small base"
ARG WHISPER_GERMAN_REV=6c16f621da45f94b2b4787c2585a682940beef8f
ARG WHISPER_TOKENIZER_REV=41f01f3fe87f28c78e2fbf8b568835947dd65ed9
RUN mkdir -p /models/whisper/german /models/speaker \
 && python -c "import os; from huggingface_hub import snapshot_download; [snapshot_download(f'Systran/faster-whisper-{m}', local_dir=f'/models/whisper/{m}', allow_patterns=['config.json', 'preprocessor_config.json', 'model.bin', 'tokenizer.json', 'vocabulary.*']) for m in os.environ['WHISPER_MODELS'].split()]" \
 && python -c "from huggingface_hub import snapshot_download; snapshot_download('TheTobyB/whisper-large-v3-turbo-german-ct2', local_dir='/models/whisper/german', revision='${WHISPER_GERMAN_REV}', allow_patterns=['config.json', 'preprocessor_config.json', 'model.bin', 'vocabulary.json'])" \
 && curl -fsSL -A "Mozilla/5.0" --retry 3 -o /models/whisper/german/tokenizer.json \
    "https://huggingface.co/openai/whisper-large-v3-turbo/resolve/${WHISPER_TOKENIZER_REV}/tokenizer.json" \
 && curl -fsSL --retry 3 -o /models/speaker/wespeaker_en_voxceleb_resnet34_LM.onnx \
    https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx \
 && chmod -R a+rX /models

COPY jarvis ./jarvis

RUN useradd --uid 10001 --system --no-create-home --shell /usr/sbin/nologin jarvis \
 && mkdir -p /data && chown jarvis /data

ENV JARVIS_DATA_DIR=/data \
    JARVIS_MODELS_DIR=/models \
    HF_HUB_OFFLINE=1 \
    HOME=/tmp

USER jarvis
EXPOSE 8500 8501 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
  CMD python -c "import os,urllib.request,sys; p=os.environ.get('JARVIS_HTTP_PORT','8500'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/health',timeout=4).status==200 else 1)"
CMD ["python", "-m", "jarvis"]
