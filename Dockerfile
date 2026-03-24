FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY actuator.py .
COPY generate_audio.py .
COPY audio/ audio/

CMD ["python", "actuator.py"]
