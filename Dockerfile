FROM python:3.12-slim
WORKDIR /app
RUN useradd --uid 10001 --create-home crm
COPY app.py zoom_sync.py jobs.py reporting.py rule_reports.py local_reports.py lesson_source.py google_services.py calendar_sync.py lesson_links.py video_sync.py backup_service.py healthcheck.py ./
COPY index.html ui.js integrations_ui.js ./
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD ["python", "healthcheck.py"]
CMD ["python", "-B", "app.py"]
