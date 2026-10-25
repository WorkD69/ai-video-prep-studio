FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layers ordered by how rarely they change: the ~2 GB CUDA / whisper stack is
# cached on its own, so app or crv changes never re-download it.
COPY requirements-media.txt ./
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements-media.txt

COPY requirements.txt requirements-crv.txt ./
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt -r requirements-crv.txt

# cuBLAS / cuDNN come from pip wheels; CTranslate2 loads them via the dynamic linker.
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib

# yt-dlp, ffmpeg and crv parse untrusted media: run without root.
RUN useradd --create-home --uid 1000 app

COPY . .

USER app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
