FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agente.py .

CMD ["uvicorn", "agente:app", "--host", "0.0.0.0", "--port", "3000"]
