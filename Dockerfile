FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Rome

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data/uploads

EXPOSE 8085
CMD ["python", "-c", "import sitecustomize, queue_sort, runpy; runpy.run_path('runner.py', run_name='__main__')"]
