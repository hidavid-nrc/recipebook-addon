ARG BUILD_FROM
FROM ${BUILD_FROM}

# Cache-bust: bump along with config.yaml version to force a clean rebuild
ARG BUILD_VERSION=0.6.5
ENV APP_VERSION=${BUILD_VERSION}

WORKDIR /app
COPY backend/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
