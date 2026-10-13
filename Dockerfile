FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CADENCE_HOME=/workspace
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 1000 --create-home cadence
COPY cadence ./cadence
USER cadence
EXPOSE 8787
CMD ["python", "-m", "cadence", "run"]
