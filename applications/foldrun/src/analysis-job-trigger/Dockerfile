FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV PORT=8080
CMD exec functions-framework --target=trigger_analysis --signature-type=cloudevent --port=$PORT
