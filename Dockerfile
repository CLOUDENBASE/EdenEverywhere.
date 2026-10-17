FROM python:3.12-slim
WORKDIR /app
COPY server.py .
ENV PORT=8080 EE_DB=/data/eden.db
VOLUME /data
EXPOSE 8080
CMD ["python3", "server.py"]
