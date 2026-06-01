FROM ghcr.io/home-assistant/aarch64-base-python:3.12-alpine3.21

WORKDIR /app
COPY backend/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
