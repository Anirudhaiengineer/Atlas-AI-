
# Python runtime
FROM python:3.11-slim

# Application working directory
WORKDIR /app

# Copy dependency files
COPY requirements.txt requirements-kafka.txt requirements-ml.txt ./

# Install application dependencies
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -r requirements-kafka.txt \
    && pip install --no-cache-dir -r requirements-ml.txt

# Copy FastAPI application
COPY app/ ./app/

# API port
EXPOSE 8000

# Start FastAPI
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
