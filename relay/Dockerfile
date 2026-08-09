FROM python:3.13-alpine

RUN addgroup -S aira && adduser -S -G aira aira
WORKDIR /app
COPY aira_relay_server.py /app/aira_relay_server.py
USER aira
EXPOSE 8787
CMD ["python", "-B", "/app/aira_relay_server.py", "--host", "0.0.0.0", "--port", "8787"]
