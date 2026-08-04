from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class JobPaths:
    storage_root: Path
    job_root: Path
    input_dir: Path
    output_dir: Path
    debug_dir: Path
    logs_dir: Path
    input_source_path: Path
    input_part_path: Path
    output_result_path: Path
    stdout_log_path: Path
    stderr_log_path: Path

    def with_extension(self, extension: str) -> JobPaths:
        normalized = extension.lower()
        input_source_path = self.input_dir / f"source{normalized}"
        input_part_path = self.input_dir / f"source{normalized}.part"
        return JobPaths(
            storage_root=self.storage_root,
            job_root=self.job_root,
            input_dir=self.input_dir,
            output_dir=self.output_dir,
            debug_dir=self.debug_dir,
            logs_dir=self.logs_dir,
            input_source_path=input_source_path,
            input_part_path=input_part_path,
            output_result_path=self.output_result_path,
            stdout_log_path=self.stdout_log_path,
            stderr_log_path=self.stderr_log_path,
        )

    @property
    def input_relative_path(self) -> str:
        return self.input_source_path.relative_to(self.storage_root).as_posix()

    @property
    def output_relative_path(self) -> str:
        return self.output_result_path.relative_to(self.storage_root).as_posix()

    @property
    def debug_relative_path(self) -> str:
        return self.debug_dir.relative_to(self.storage_root).as_posix()

    @property
    def stdout_log_relative_path(self) -> str:
        return self.stdout_log_path.relative_to(self.storage_root).as_posix()

    @property
    def stderr_log_relative_path(self) -> str:
        return self.stderr_log_path.relative_to(self.storage_root).as_posix()


def build_job_paths(storage_root: Path, job_id: UUID, extension: str = ".pdf") -> JobPaths:
    resolved_root = storage_root.resolve()
    job_root = (resolved_root / "jobs" / str(job_id)).resolve()
    if not job_root.is_relative_to(resolved_root):
        raise ValueError("generated job paths must remain inside STORAGE_ROOT")

    input_dir = job_root / "input"
    output_dir = job_root / "output"
    debug_dir = job_root / "debug"
    logs_dir = job_root / "logs"
    normalized = extension.lower()
    input_source_path = input_dir / f"source{normalized}"
    input_part_path = input_dir / f"source{normalized}.part"
    output_result_path = output_dir / "result.xlsx"
    stdout_log_path = logs_dir / "stdout.log"
    stderr_log_path = logs_dir / "stderr.log"

    return JobPaths(
        storage_root=resolved_root,
        job_root=job_root,
        input_dir=input_dir,
        output_dir=output_dir,
        debug_dir=debug_dir,
        logs_dir=logs_dir,
        input_source_path=input_source_path,
        input_part_path=input_part_path,
        output_result_path=output_result_path,
        stdout_log_path=stdout_log_path,
        stderr_log_path=stderr_log_path,
    )
