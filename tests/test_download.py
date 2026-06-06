import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/aivps")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from app.config import settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.job import Job, JobStatus  # noqa: E402


class DownloadMockDB:
    def __init__(self, job: Job | None = None):
        self.job = job

    def get(self, model, pk):
        if model is Job and self.job is not None and str(pk) == str(self.job.id):
            return self.job
        return None


def make_job(
    *,
    status: JobStatus = JobStatus.done,
    output_path: str | None = None,
    expires_at: datetime | None = None,
    original_filename: str = "../../unsafe original.mp4",
) -> Job:
    now = datetime.utcnow()
    return Job(
        id=uuid4(),
        session_id=str(uuid4()),
        status=status,
        original_filename=original_filename,
        stored_filename=f"{uuid4()}.mp4",
        input_path="/uploads/input.mp4",
        output_path=output_path,
        video_size_bytes=123,
        created_at=now,
        expires_at=expires_at or now + timedelta(hours=24),
    )


def make_zip(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("dummy.txt", "hello")
    return path.read_bytes()


def download_client(job: Job | None, output_dir: Path):
    old_output_dir = settings.output_dir
    settings.output_dir = str(output_dir)

    def override():
        yield DownloadMockDB(job)

    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    return client, old_output_dir


def cleanup_download_client(client: TestClient, old_output_dir: str) -> None:
    client.close()
    app.dependency_overrides.clear()
    settings.output_dir = old_output_dir


def test_download_happy_path(tmp_path):
    output_dir = tmp_path / "outputs"
    zip_path = output_dir / "llm_analysis_package_lecture_20260604_123456.zip"
    expected_bytes = make_zip(zip_path)
    job = make_job(output_path=str(zip_path))
    client, old_output_dir = download_client(job, output_dir)
    try:
        response = client.get(f"/download/{job.id}")
    finally:
        cleanup_download_client(client, old_output_dir)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    assert zip_path.name in response.headers["content-disposition"]
    assert response.content == expected_bytes


def test_download_job_not_found(tmp_path):
    client, old_output_dir = download_client(None, tmp_path / "outputs")
    try:
        response = client.get(f"/download/{uuid4()}")
    finally:
        cleanup_download_client(client, old_output_dir)

    assert response.status_code == 404
    assert response.json()["detail"] == "job_not_found"


def test_download_job_not_done_pending(tmp_path):
    response = request_download_for_status(tmp_path, JobStatus.pending)
    assert response.status_code == 409
    assert response.json()["detail"] == "job_not_ready"


def test_download_job_not_done_queued(tmp_path):
    response = request_download_for_status(tmp_path, JobStatus.queued)
    assert response.status_code == 409
    assert response.json()["detail"] == "job_not_ready"


def test_download_job_not_done_processing(tmp_path):
    response = request_download_for_status(tmp_path, JobStatus.processing)
    assert response.status_code == 409
    assert response.json()["detail"] == "job_not_ready"


def test_download_job_not_done_failed(tmp_path):
    response = request_download_for_status(tmp_path, JobStatus.failed)
    assert response.status_code == 409
    assert response.json()["detail"] == "job_not_ready"


def request_download_for_status(tmp_path, status: JobStatus):
    output_dir = tmp_path / "outputs"
    zip_path = output_dir / "llm_analysis_package_lecture_20260604_123456.zip"
    make_zip(zip_path)
    job = make_job(status=status, output_path=str(zip_path))
    client, old_output_dir = download_client(job, output_dir)
    try:
        return client.get(f"/download/{job.id}")
    finally:
        cleanup_download_client(client, old_output_dir)


def test_download_expired(tmp_path):
    output_dir = tmp_path / "outputs"
    zip_path = output_dir / "llm_analysis_package_lecture_20260604_123456.zip"
    make_zip(zip_path)
    job = make_job(output_path=str(zip_path), expires_at=datetime.utcnow() - timedelta(seconds=1))
    client, old_output_dir = download_client(job, output_dir)
    try:
        response = client.get(f"/download/{job.id}")
    finally:
        cleanup_download_client(client, old_output_dir)

    assert response.status_code == 410
    assert response.json()["detail"] == "job_expired"


def test_download_output_path_null(tmp_path):
    job = make_job(output_path=None)
    client, old_output_dir = download_client(job, tmp_path / "outputs")
    try:
        response = client.get(f"/download/{job.id}")
    finally:
        cleanup_download_client(client, old_output_dir)

    assert response.status_code == 500
    assert response.json()["detail"] == "output_path_missing"


def test_download_path_traversal_db(tmp_path):
    output_dir = tmp_path / "outputs"
    outside_path = tmp_path / "outside" / "llm_analysis_package_escape_20260604_123456.zip"
    make_zip(outside_path)
    job = make_job(output_path=str(outside_path))
    client, old_output_dir = download_client(job, output_dir)
    try:
        response = client.get(f"/download/{job.id}")
    finally:
        cleanup_download_client(client, old_output_dir)

    assert outside_path.exists()
    assert response.status_code == 500
    assert response.json()["detail"] == "internal_error"


def test_download_file_missing(tmp_path):
    output_dir = tmp_path / "outputs"
    missing_path = output_dir / "llm_analysis_package_missing_20260604_123456.zip"
    job = make_job(output_path=str(missing_path))
    client, old_output_dir = download_client(job, output_dir)
    try:
        response = client.get(f"/download/{job.id}")
    finally:
        cleanup_download_client(client, old_output_dir)

    assert response.status_code == 500
    assert response.json()["detail"] == "output_file_missing"


def test_download_invalid_job_id(tmp_path):
    client, old_output_dir = download_client(None, tmp_path / "outputs")
    try:
        response = client.get("/download/not-a-uuid")
    finally:
        cleanup_download_client(client, old_output_dir)

    assert response.status_code == 422


def test_download_content_disposition_safe(tmp_path):
    output_dir = tmp_path / "outputs"
    zip_path = output_dir / "llm_analysis_package_safe_20260604_123456.zip"
    make_zip(zip_path)
    job = make_job(output_path=str(zip_path), original_filename="../../evil name.mp4")
    client, old_output_dir = download_client(job, output_dir)
    try:
        response = client.get(f"/download/{job.id}")
    finally:
        cleanup_download_client(client, old_output_dir)

    content_disposition = response.headers["content-disposition"]
    assert response.status_code == 200
    assert zip_path.name in content_disposition
    assert "evil" not in content_disposition
    assert ".." not in content_disposition
