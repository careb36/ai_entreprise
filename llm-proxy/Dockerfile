FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    flask==3.0.0 \
    requests==2.31.0 \
    gunicorn==21.2.0

COPY app.py /app/

EXPOSE 5100

CMD ["gunicorn", "-b", "0.0.0.0:5100", "-w", "2", "app:app"]
