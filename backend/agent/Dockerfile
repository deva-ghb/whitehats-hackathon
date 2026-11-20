# WhiteHats collector agent image: read-only, unprivileged, one small Python file.
FROM python:3.12-slim
RUN pip install --no-cache-dir psutil==6.1.1 && useradd --system --uid 10001 collector
COPY collector.py /opt/whitehats/collector.py
USER 10001
ENTRYPOINT ["python", "/opt/whitehats/collector.py"]
