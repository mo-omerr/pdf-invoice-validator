#!/usr/bin/env python3
"""
Batch PDF Processor with Rate-Limited Background Scheduling

Features:
- Queue-based batch processing for multiple PDFs
- Rate limiting to avoid spamming Anthropic API
- Background processing using asyncio
- Progress tracking and status updates
- Configurable delay between API calls
"""

import asyncio
import uuid
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable
from enum import Enum
from datetime import datetime
import threading
from queue import Queue
import traceback

from pdf_validator import validate_pdf, export_to_excel, PDFValidationReport


class JobStatus(str, Enum):
    """Status of a batch processing job."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PDFJob:
    """Represents a single PDF processing job."""
    job_id: str
    file_path: str
    filename: str
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    report: Optional[PDFValidationReport] = None
    excel_path: Optional[str] = None
    error: Optional[str] = None
    progress: int = 0  # 0-100

    def to_dict(self) -> dict:
        """Convert job to dictionary for JSON serialization."""
        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "excel_path": Path(self.excel_path).name if self.excel_path else None,
            "error": self.error,
            "progress": self.progress,
            "invoices_found": self.report.invoices_found if self.report else 0,
            "is_valid": self.report.is_valid if self.report else None,
            "vendor": self.report.template_name if self.report else None,
        }


@dataclass
class BatchJob:
    """Represents a batch of PDF processing jobs."""
    batch_id: str
    jobs: List[PDFJob] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    status: JobStatus = JobStatus.PENDING

    @property
    def total_jobs(self) -> int:
        return len(self.jobs)

    @property
    def completed_jobs(self) -> int:
        return sum(1 for j in self.jobs if j.status in [JobStatus.COMPLETED, JobStatus.FAILED])

    @property
    def successful_jobs(self) -> int:
        return sum(1 for j in self.jobs if j.status == JobStatus.COMPLETED)

    @property
    def failed_jobs(self) -> int:
        return sum(1 for j in self.jobs if j.status == JobStatus.FAILED)

    @property
    def progress(self) -> int:
        if self.total_jobs == 0:
            return 0
        return int((self.completed_jobs / self.total_jobs) * 100)

    def to_dict(self) -> dict:
        """Convert batch to dictionary for JSON serialization."""
        return {
            "batch_id": self.batch_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "total_jobs": self.total_jobs,
            "completed_jobs": self.completed_jobs,
            "successful_jobs": self.successful_jobs,
            "failed_jobs": self.failed_jobs,
            "progress": self.progress,
            "jobs": [j.to_dict() for j in self.jobs],
        }


class BatchProcessor:
    """
    Manages batch processing of PDFs with rate limiting.

    Features:
    - Configurable delay between API calls (default: 2 seconds)
    - Background processing thread
    - Progress tracking
    - Callback support for status updates
    """

    def __init__(
        self,
        templates_dir: str = None,
        output_dir: str = None,
        processed_dir: str = None,
        delay_between_jobs: float = 2.0,  # seconds between API calls
        max_concurrent: int = 1,  # process one at a time for rate limiting
    ):
        self.templates_dir = templates_dir
        self.output_dir = Path(output_dir) if output_dir else Path("./output")
        self.processed_dir = Path(processed_dir) if processed_dir else Path("./processed")
        self.delay_between_jobs = delay_between_jobs
        self.max_concurrent = max_concurrent

        # Ensure directories exist
        self.output_dir.mkdir(exist_ok=True)
        self.processed_dir.mkdir(exist_ok=True)

        # Storage for batches and jobs
        self._batches: Dict[str, BatchJob] = {}
        self._jobs: Dict[str, PDFJob] = {}

        # Processing queue
        self._queue: Queue = Queue()

        # Background worker thread
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_running = False

        # Callbacks
        self._on_job_complete: Optional[Callable[[PDFJob], None]] = None
        self._on_batch_complete: Optional[Callable[[BatchJob], None]] = None

        # Lock for thread safety
        self._lock = threading.Lock()

    def start(self):
        """Start the background processor."""
        if self._is_running:
            return

        self._stop_event.clear()
        self._is_running = True
        self._worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self._worker_thread.start()
        print("BatchProcessor started")

    def stop(self):
        """Stop the background processor."""
        self._stop_event.set()
        self._is_running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
        print("BatchProcessor stopped")

    def set_callbacks(
        self,
        on_job_complete: Callable[[PDFJob], None] = None,
        on_batch_complete: Callable[[BatchJob], None] = None,
    ):
        """Set callback functions for job/batch completion."""
        self._on_job_complete = on_job_complete
        self._on_batch_complete = on_batch_complete

    def create_batch(self, file_paths: List[str]) -> BatchJob:
        """
        Create a new batch job for multiple PDFs.

        Args:
            file_paths: List of paths to PDF files

        Returns:
            BatchJob with unique batch_id
        """
        batch_id = str(uuid.uuid4())[:8]
        batch = BatchJob(batch_id=batch_id)

        for file_path in file_paths:
            path = Path(file_path)
            if path.exists() and path.suffix.lower() == '.pdf':
                job_id = str(uuid.uuid4())[:8]
                job = PDFJob(
                    job_id=job_id,
                    file_path=str(path),
                    filename=path.name,
                )
                batch.jobs.append(job)

                with self._lock:
                    self._jobs[job_id] = job

        with self._lock:
            self._batches[batch_id] = batch

        # Queue all jobs for processing
        for job in batch.jobs:
            self._queue.put((batch_id, job.job_id))

        # Start processor if not running
        if not self._is_running:
            self.start()

        print(f"Created batch {batch_id} with {len(batch.jobs)} jobs")
        return batch

    def add_single_job(self, file_path: str) -> PDFJob:
        """
        Add a single PDF for processing.

        Args:
            file_path: Path to PDF file

        Returns:
            PDFJob with unique job_id
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if path.suffix.lower() != '.pdf':
            raise ValueError("Only PDF files are allowed")

        job_id = str(uuid.uuid4())[:8]
        job = PDFJob(
            job_id=job_id,
            file_path=str(path),
            filename=path.name,
        )

        with self._lock:
            self._jobs[job_id] = job

        # Queue for processing
        self._queue.put((None, job_id))  # None batch_id for single jobs

        # Start processor if not running
        if not self._is_running:
            self.start()

        return job

    def get_batch(self, batch_id: str) -> Optional[BatchJob]:
        """Get batch status by ID."""
        with self._lock:
            return self._batches.get(batch_id)

    def get_job(self, job_id: str) -> Optional[PDFJob]:
        """Get job status by ID."""
        with self._lock:
            return self._jobs.get(job_id)

    def get_all_batches(self) -> List[BatchJob]:
        """Get all batches."""
        with self._lock:
            return list(self._batches.values())

    def get_queue_status(self) -> dict:
        """Get current queue status."""
        return {
            "is_running": self._is_running,
            "queue_size": self._queue.qsize(),
            "total_batches": len(self._batches),
            "total_jobs": len(self._jobs),
        }

    def cancel_batch(self, batch_id: str) -> bool:
        """Cancel a batch and all its pending jobs."""
        with self._lock:
            batch = self._batches.get(batch_id)
            if not batch:
                return False

            for job in batch.jobs:
                if job.status == JobStatus.PENDING:
                    job.status = JobStatus.CANCELLED

            batch.status = JobStatus.CANCELLED
            return True

    def _process_queue(self):
        """Background worker that processes the queue."""
        print("Queue processor started")

        while not self._stop_event.is_set():
            try:
                # Try to get a job from the queue with timeout
                try:
                    batch_id, job_id = self._queue.get(timeout=1.0)
                except:
                    continue

                with self._lock:
                    job = self._jobs.get(job_id)
                    batch = self._batches.get(batch_id) if batch_id else None

                if not job or job.status == JobStatus.CANCELLED:
                    self._queue.task_done()
                    continue

                # Process the job
                self._process_job(job, batch)

                # Mark queue task as done
                self._queue.task_done()

                # Rate limiting - wait before next job
                if not self._stop_event.is_set() and not self._queue.empty():
                    print(f"Rate limiting: waiting {self.delay_between_jobs}s before next job...")
                    time.sleep(self.delay_between_jobs)

            except Exception as e:
                print(f"Error in queue processor: {e}")
                traceback.print_exc()

        print("Queue processor stopped")

    def _process_job(self, job: PDFJob, batch: Optional[BatchJob]):
        """Process a single PDF job."""
        try:
            # Update status
            job.status = JobStatus.PROCESSING
            job.started_at = datetime.now()
            job.progress = 10

            if batch:
                batch.status = JobStatus.PROCESSING

            print(f"Processing job {job.job_id}: {job.filename}")

            # Validate PDF
            job.progress = 30
            report = validate_pdf(
                job.file_path,
                templates_dir=self.templates_dir
            )
            job.report = report
            job.progress = 70

            # Export to Excel if invoices found
            if report.invoices_found > 0:
                timestamp = datetime.now().strftime("%d%m%yT%H%M%S")
                output_path = self.output_dir / f"{Path(job.filename).stem}_invoices_{timestamp}.xlsx"
                excel_path = export_to_excel(report, str(output_path))
                job.excel_path = excel_path

            job.progress = 90

            # Move to processed folder
            src_path = Path(job.file_path)
            dst_path = self.processed_dir / src_path.name
            if dst_path.exists():
                base = dst_path.stem
                ext = dst_path.suffix
                counter = 1
                while dst_path.exists():
                    dst_path = self.processed_dir / f"{base}_{counter}{ext}"
                    counter += 1

            import shutil
            shutil.move(str(src_path), str(dst_path))

            # Mark complete
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now()
            job.progress = 100

            print(f"Job {job.job_id} completed: {report.invoices_found} invoices found")

        except Exception as e:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now()
            job.error = str(e)
            print(f"Job {job.job_id} failed: {e}")
            traceback.print_exc()

        # Trigger callbacks
        if self._on_job_complete:
            try:
                self._on_job_complete(job)
            except Exception as e:
                print(f"Callback error: {e}")

        # Check if batch is complete
        if batch:
            all_done = all(
                j.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
                for j in batch.jobs
            )
            if all_done:
                batch.status = JobStatus.COMPLETED
                if self._on_batch_complete:
                    try:
                        self._on_batch_complete(batch)
                    except Exception as e:
                        print(f"Batch callback error: {e}")


# Global processor instance (singleton pattern)
_processor: Optional[BatchProcessor] = None


def get_processor(
    templates_dir: str = None,
    output_dir: str = None,
    processed_dir: str = None,
    delay_between_jobs: float = 2.0,
) -> BatchProcessor:
    """
    Get or create the global batch processor instance.

    Args:
        templates_dir: Directory for templates
        output_dir: Directory for Excel output
        processed_dir: Directory for processed PDFs
        delay_between_jobs: Seconds to wait between API calls

    Returns:
        BatchProcessor instance
    """
    global _processor

    if _processor is None:
        _processor = BatchProcessor(
            templates_dir=templates_dir,
            output_dir=output_dir,
            processed_dir=processed_dir,
            delay_between_jobs=delay_between_jobs,
        )

    return _processor


def shutdown_processor():
    """Shutdown the global processor."""
    global _processor
    if _processor:
        _processor.stop()
        _processor = None


# CLI interface for testing
if __name__ == "__main__":
    import sys
    import os

    if len(sys.argv) < 2:
        print("Usage: python batch_processor.py <pdf1> [pdf2] [pdf3] ...")
        print("\nOptions:")
        print("  --delay <seconds>  Delay between jobs (default: 2.0)")
        print("\nExample:")
        print("  python batch_processor.py invoice1.pdf invoice2.pdf invoice3.pdf")
        sys.exit(1)

    # Parse arguments
    pdfs = []
    delay = 2.0
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--delay" and i + 1 < len(sys.argv):
            delay = float(sys.argv[i + 1])
            i += 2
        else:
            pdfs.append(sys.argv[i])
            i += 1

    if not pdfs:
        print("No PDF files specified")
        sys.exit(1)

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY environment variable not set")
        sys.exit(1)

    print(f"Processing {len(pdfs)} PDF(s) with {delay}s delay between jobs")
    print("=" * 60)

    # Create processor
    processor = get_processor(delay_between_jobs=delay)

    # Track completion
    completed = threading.Event()

    def on_batch_complete(batch: BatchJob):
        print("\n" + "=" * 60)
        print(f"BATCH COMPLETE: {batch.batch_id}")
        print(f"  Total: {batch.total_jobs}")
        print(f"  Successful: {batch.successful_jobs}")
        print(f"  Failed: {batch.failed_jobs}")
        print("=" * 60)
        completed.set()

    processor.set_callbacks(on_batch_complete=on_batch_complete)

    # Create batch
    batch = processor.create_batch(pdfs)

    # Wait for completion
    print(f"Batch {batch.batch_id} created, processing in background...")
    completed.wait()

    # Print final results
    print("\nFinal Results:")
    for job in batch.jobs:
        status = "OK" if job.status == JobStatus.COMPLETED else "FAILED"
        print(f"  [{status}] {job.filename}")
        if job.excel_path:
            print(f"       -> {Path(job.excel_path).name}")
        if job.error:
            print(f"       Error: {job.error}")

    shutdown_processor()
