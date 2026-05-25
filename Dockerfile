FROM python:3.13-slim

WORKDIR /app

# Install runtime dependencies first — cached unless requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source then register the package so importlib.metadata.version() resolves.
# --no-deps avoids reinstalling the deps already in the layer above.
COPY . .
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8080

LABEL org.opencontainers.image.source="https://github.com/GregoireF/notiftk"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
