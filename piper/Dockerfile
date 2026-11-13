FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install "piper-tts==1.8.0"

# Voices are baked into the image so the container never downloads at runtime.
# Format: <lang>/<lang_CC>/<name>/<quality>. Extend in Coolify as build argument.
# Multi-speaker models (thorsten_emotional, mls, semaine, vctk, aru) show up once per speaker.
ARG PIPER_VOICES="de/de_DE/thorsten/high de/de_DE/thorsten/medium de/de_DE/thorsten_emotional/medium de/de_DE/kerstin/low de/de_DE/ramona/low de/de_DE/karlsson/low de/de_DE/pavoque/low de/de_DE/eva_k/x_low de/de_DE/mls/medium en/en_GB/alan/medium en/en_GB/northern_english_male/medium en/en_GB/southern_english_female/low en/en_GB/jenny_dioco/medium en/en_GB/cori/high en/en_GB/semaine/medium en/en_GB/alba/medium en/en_GB/vctk/medium en/en_GB/aru/medium en/en_US/ryan/high en/en_US/lessac/high en/en_US/joe/medium en/en_US/john/medium en/en_US/norman/medium en/en_US/hfc_male/medium en/en_US/bryce/medium"
RUN mkdir -p /voices \
 && for spec in $PIPER_VOICES; do \
      lang_cc=$(echo "$spec" | cut -d/ -f2); name=$(echo "$spec" | cut -d/ -f3); quality=$(echo "$spec" | cut -d/ -f4); \
      file="${lang_cc}-${name}-${quality}"; \
      base="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/${spec}/${file}"; \
      curl -fsSL --retry 3 -o "/voices/${file}.onnx" "${base}.onnx" \
   && curl -fsSL --retry 3 -o "/voices/${file}.onnx.json" "${base}.onnx.json" || exit 1; \
    done \
 && chmod -R a+rX /voices

COPY server.py /app/server.py

RUN useradd --uid 10002 --system --no-create-home --shell /usr/sbin/nologin piper
USER piper

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health',timeout=4).status==200 else 1)"
CMD ["python", "/app/server.py"]
