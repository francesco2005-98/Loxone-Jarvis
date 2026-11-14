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
# Kroko: German streaming Zipformer by Banafo, CC-BY-SA community model, https://huggingface.co/Banafo/Kroko-ASR
ARG WHISPER_MODELS="small base"
ARG KROKO_MODEL=sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06
ARG KROKO_SHA256=9e27b783c20e67b0d0f13a258c1861fce199917c969d9176a438bee38df64962
RUN mkdir -p /models/whisper /models/speaker /models/kroko \
 && python -c "import os; from huggingface_hub import snapshot_download; [snapshot_download(f'Systran/faster-whisper-{m}', local_dir=f'/models/whisper/{m}', allow_patterns=['config.json', 'preprocessor_config.json', 'model.bin', 'tokenizer.json', 'vocabulary.*']) for m in os.environ['WHISPER_MODELS'].split()]" \
 && curl -fsSL --retry 3 -o /tmp/kroko.tar.bz2 "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/${KROKO_MODEL}.tar.bz2" \
 && echo "${KROKO_SHA256}  /tmp/kroko.tar.bz2" | sha256sum -c - \
 && python -c "import tarfile, shutil, os; t = tarfile.open('/tmp/kroko.tar.bz2'); t.extractall('/tmp/kroko', filter='data'); src = '/tmp/kroko/${KROKO_MODEL}'; [shutil.copy(os.path.join(src, f), '/models/kroko/' + f) for f in ('encoder.onnx', 'decoder.onnx', 'joiner.onnx', 'tokens.txt')]" \
 && rm -rf /tmp/kroko /tmp/kroko.tar.bz2 \
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
