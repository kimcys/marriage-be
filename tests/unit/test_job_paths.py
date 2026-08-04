from pathlib import Path
from uuid import UUID

from marriage_ocr_api.jobs.paths import build_job_paths


def test_generated_job_paths_stay_under_storage_root() -> None:
    storage_root = Path("/tmp/marriage-storage")
    job_id = UUID("123e4567-e89b-12d3-a456-426614174000")

    paths = build_job_paths(storage_root, job_id)
    resolved_root = storage_root.resolve()

    assert paths.job_root.is_relative_to(resolved_root)
    assert paths.input_source_path.is_relative_to(resolved_root)
    assert paths.output_result_path.is_relative_to(resolved_root)
    assert paths.debug_dir.is_relative_to(resolved_root)
    assert paths.stdout_log_path.is_relative_to(resolved_root)
    assert paths.stderr_log_path.is_relative_to(resolved_root)
    assert paths.input_relative_path == f"jobs/{job_id}/input/source.pdf"
