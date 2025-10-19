FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /konempleo
COPY ./requirements.txt /konempleo/requirements.txt

# Librerías del sistema necesarias para tus dependencias (tesseract, opencv, lxml, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ make \
    libpq-dev \
    libxml2-dev libxslt1-dev \
    libgl1 libglib2.0-0 \
    tesseract-ocr libtesseract-dev \
    poppler-utils \
  && rm -rf /var/lib/apt/lists/*

# Primero PyTorch CPU (ruedas precompiladas) y luego el resto
RUN python -m pip install --upgrade pip \
 && python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
      torch==2.4.1 torchvision==0.19.1 \
 && python -m pip install --no-cache-dir -r /konempleo/requirements.txt

# Copia el código
COPY . /konempleo/

# Variables y puerto
ENV PORT=8000 APP_ENV=production
EXPOSE 8000

# Arranque
RUN chmod +x /konempleo/docker-start.sh
CMD ["/konempleo/docker-start.sh"]
