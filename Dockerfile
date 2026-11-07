FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m appuser && mkdir -p instance && chown -R appuser /app
USER appuser
ENV FLASK_APP=wsgi.py
EXPOSE 8000
CMD ["sh", "-c", "flask init-db && gunicorn -w 2 -b 0.0.0.0:${PORT:-8000} wsgi:app"]
