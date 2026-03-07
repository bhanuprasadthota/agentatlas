FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md /app/
COPY agentatlas /app/agentatlas
COPY compare_benchmark_runs.py backfill_fingerprints.py test_execute.py /app/
COPY supabase /app/supabase

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "agentatlas.api:app", "--host", "0.0.0.0", "--port", "8000"]
