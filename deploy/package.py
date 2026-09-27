"""Create code archive only; secrets and CRM records are never bundled."""
from pathlib import Path
import tarfile

root=Path(__file__).resolve().parent.parent
files=['Dockerfile','.dockerignore','compose.yaml','index.html','ui.js','integrations_ui.js',
       'app.py','zoom_sync.py','jobs.py','reporting.py','rule_reports.py','local_reports.py','lesson_source.py','google_services.py',
       'calendar_sync.py','lesson_links.py','video_sync.py','backup_service.py','healthcheck.py','DEPLOYMENT.md','RELEASE_STATUS.md','SERVER_PLAN.md','LOCAL_REPORTS.md']
files += [str(p.relative_to(root)) for p in (root/'deploy').iterdir() if p.is_file()]
out=root/'.runtime'/'crm-deploy.tar.gz';out.parent.mkdir(exist_ok=True)
with tarfile.open(out,'w:gz') as archive:
    for name in files: archive.add(root/name,arcname=Path(name).as_posix())
print(out)
