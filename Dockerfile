# Container image for any Docker host (Hugging Face Spaces, Fly.io, Cloud Run...).
# Pass the keys as environment variables or host secrets, never bake them in.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY firewatch ./firewatch
ENV FIREWATCH_CACHE=/tmp/firewatch PORT=7860
EXPOSE 7860
CMD uvicorn firewatch.server:app --host 0.0.0.0 --port $PORT
