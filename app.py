#!/usr/bin/env python3
"""
FastAPI Web Frontend for PDF Invoice Validator
Simple interface to upload PDFs and view extracted invoice data with dashboard.

Features:
- Single PDF upload with immediate processing
- Batch upload with background processing and rate limiting
- Real-time status tracking for batch jobs
- Dashboard with charts and metrics
"""

import os
import shutil
from pathlib import Path
from typing import List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager

from pdf_validator import validate_pdf, export_to_excel
from batch_processor import get_processor, shutdown_processor, BatchProcessor


# Lifespan handler for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize batch processor
    processor = get_processor(
        templates_dir=str(TEMPLATES_DIR),
        output_dir=str(OUTPUT_DIR),
        processed_dir=str(PROCESSED_DIR),
        delay_between_jobs=2.0,  # 2 second delay between API calls
    )
    processor.start()
    print("Batch processor initialized")
    yield
    # Shutdown: Clean up
    shutdown_processor()
    print("Batch processor shut down")


app = FastAPI(
    title="PDF Invoice Validator",
    description="AI-powered PDF invoice extraction and validation with batch support",
    lifespan=lifespan,
)

# Directories
BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
PROCESSED_DIR = BASE_DIR / "processed"
TEMPLATES_DIR = BASE_DIR / "templates"

# Create directories if they don't exist
for dir_path in [INPUT_DIR, OUTPUT_DIR, PROCESSED_DIR, TEMPLATES_DIR]:
    dir_path.mkdir(exist_ok=True)

# Setup Jinja2 templates
templates = Jinja2Templates(directory=str(BASE_DIR / "templates_html"))


def _parse_currency(value):
    """Parse currency string to float."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace('$', '').replace(',', '').strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0


@app.get("/")
async def index(request: Request):
    """Main page with upload form and dashboard."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Handle single PDF file upload and process it immediately."""
    # Validate file type
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured. Please set it in .env file")

    try:
        # Save the uploaded file
        filename = file.filename.replace(" ", "_")
        file_path = INPUT_DIR / filename

        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)

        # Process the PDF
        report = validate_pdf(str(file_path), templates_dir=str(TEMPLATES_DIR))

        # Export to Excel
        excel_path = None
        if report.invoices_found > 0:
            from datetime import datetime
            timestamp = datetime.now().strftime("%d%m%yT%H%M%S")
            output_path = OUTPUT_DIR / f"{file_path.stem}_invoices_{timestamp}.xlsx"
            excel_path = export_to_excel(report, str(output_path))

        # Move to processed folder
        processed_path = PROCESSED_DIR / filename
        if processed_path.exists():
            base = processed_path.stem
            ext = processed_path.suffix
            counter = 1
            while processed_path.exists():
                processed_path = PROCESSED_DIR / f"{base}_{counter}{ext}"
                counter += 1
        shutil.move(str(file_path), str(processed_path))

        # Prepare response data
        invoices_data = []
        for inv in report.invoice_results:
            inv_data = {
                'data': inv.extracted_data,
                'line_items': inv.extracted_data.get('line_items', []),
                'is_valid': inv.is_valid,
                'errors': inv.errors
            }
            invoices_data.append(inv_data)

        # Calculate summary stats for dashboard
        total_amount = 0
        amounts = []
        for inv in report.invoice_results:
            amount = _parse_currency(inv.extracted_data.get('total') or inv.extracted_data.get('amount_due') or '0')
            amounts.append(amount)
            total_amount += amount

        avg_amount = total_amount / len(amounts) if amounts else 0
        max_amount = max(amounts) if amounts else 0
        min_amount = min(amounts) if amounts else 0

        response = {
            'success': True,
            'filename': filename,
            'vendor': report.template_name,
            'invoices_found': report.invoices_found,
            'invoices_valid': report.invoices_valid,
            'is_valid': report.is_valid,
            'template_created': report.template_created,
            'invoices': invoices_data,
            'excel_file': Path(excel_path).name if excel_path else None,
            'dashboard': {
                'total_amount': total_amount,
                'average_amount': avg_amount,
                'highest_amount': max_amount,
                'lowest_amount': min_amount,
                'amounts': amounts
            }
        }

        return JSONResponse(content=response)

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Batch Processing Endpoints
# =============================================================================

@app.post("/batch/upload")
async def batch_upload(files: List[UploadFile] = File(...)):
    """
    Upload multiple PDFs for batch processing.

    Files are queued and processed in the background with rate limiting
    to avoid spamming the Anthropic API.

    Returns a batch_id that can be used to track progress.
    """
    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    # Validate files
    pdf_files = []
    for file in files:
        if not file.filename.lower().endswith('.pdf'):
            continue  # Skip non-PDF files
        pdf_files.append(file)

    if not pdf_files:
        raise HTTPException(status_code=400, detail="No valid PDF files provided")

    try:
        # Save all files to input directory
        saved_paths = []
        for file in pdf_files:
            filename = file.filename.replace(" ", "_")
            file_path = INPUT_DIR / filename

            # Handle duplicate filenames
            if file_path.exists():
                base = file_path.stem
                ext = file_path.suffix
                counter = 1
                while file_path.exists():
                    file_path = INPUT_DIR / f"{base}_{counter}{ext}"
                    counter += 1

            with open(file_path, "wb") as buffer:
                content = await file.read()
                buffer.write(content)

            saved_paths.append(str(file_path))

        # Create batch job
        processor = get_processor()
        batch = processor.create_batch(saved_paths)

        return JSONResponse(content={
            "success": True,
            "message": f"Batch created with {len(saved_paths)} files",
            "batch_id": batch.batch_id,
            "total_files": len(saved_paths),
            "files": [Path(p).name for p in saved_paths],
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/batch/{batch_id}")
async def get_batch_status(batch_id: str):
    """
    Get the status of a batch processing job.

    Returns progress, individual job statuses, and results when complete.
    """
    processor = get_processor()
    batch = processor.get_batch(batch_id)

    if not batch:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")

    return JSONResponse(content=batch.to_dict())


@app.get("/batch/{batch_id}/results")
async def get_batch_results(batch_id: str):
    """
    Get detailed results for a completed batch.

    Returns invoice data and dashboard metrics for all processed files.
    """
    processor = get_processor()
    batch = processor.get_batch(batch_id)

    if not batch:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")

    # Aggregate results
    all_invoices = []
    total_amount = 0
    amounts = []
    vendors = set()

    for job in batch.jobs:
        if job.report:
            vendors.add(job.report.template_name)
            for inv in job.report.invoice_results:
                inv_data = {
                    'source_file': job.filename,
                    'data': inv.extracted_data,
                    'line_items': inv.extracted_data.get('line_items', []),
                    'is_valid': inv.is_valid,
                    'errors': inv.errors,
                }
                all_invoices.append(inv_data)

                amount = _parse_currency(
                    inv.extracted_data.get('total') or
                    inv.extracted_data.get('amount_due') or '0'
                )
                amounts.append(amount)
                total_amount += amount

    avg_amount = total_amount / len(amounts) if amounts else 0
    max_amount = max(amounts) if amounts else 0
    min_amount = min(amounts) if amounts else 0

    return JSONResponse(content={
        "batch_id": batch_id,
        "status": batch.status.value,
        "progress": batch.progress,
        "total_files": batch.total_jobs,
        "successful_files": batch.successful_jobs,
        "failed_files": batch.failed_jobs,
        "vendors": list(vendors),
        "invoices": all_invoices,
        "dashboard": {
            "total_invoices": len(all_invoices),
            "total_amount": total_amount,
            "average_amount": avg_amount,
            "highest_amount": max_amount,
            "lowest_amount": min_amount,
            "amounts": amounts,
        },
        "files": [
            {
                "filename": job.filename,
                "status": job.status.value,
                "excel_file": job.excel_path and Path(job.excel_path).name,
                "invoices_found": job.report.invoices_found if job.report else 0,
                "error": job.error,
            }
            for job in batch.jobs
        ],
    })


@app.post("/batch/{batch_id}/cancel")
async def cancel_batch(batch_id: str):
    """Cancel a batch and all its pending jobs."""
    processor = get_processor()
    success = processor.cancel_batch(batch_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")

    return JSONResponse(content={
        "success": True,
        "message": f"Batch {batch_id} cancelled",
    })


@app.get("/batches")
async def list_batches():
    """List all batch jobs and their status."""
    processor = get_processor()
    batches = processor.get_all_batches()

    return JSONResponse(content={
        "batches": [b.to_dict() for b in batches],
        "queue_status": processor.get_queue_status(),
    })


@app.get("/queue/status")
async def get_queue_status():
    """Get current queue status."""
    processor = get_processor()
    return JSONResponse(content=processor.get_queue_status())


@app.get("/job/{job_id}/results")
async def get_job_results(job_id: str):
    """
    Get detailed results for a single job.

    Returns invoice data and dashboard metrics in same format as single upload.
    Used for viewing individual file dashboards in batch processing.
    """
    processor = get_processor()
    job = processor.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if not job.report:
        raise HTTPException(status_code=400, detail=f"Job {job_id} has not completed processing")

    # Build response in same format as single upload
    invoices_data = []
    total_amount = 0
    amounts = []

    for inv in job.report.invoice_results:
        inv_data = {
            'data': inv.extracted_data,
            'line_items': inv.extracted_data.get('line_items', []),
            'is_valid': inv.is_valid,
            'errors': inv.errors,
        }
        invoices_data.append(inv_data)

        amount = _parse_currency(
            inv.extracted_data.get('total') or
            inv.extracted_data.get('amount_due') or '0'
        )
        amounts.append(amount)
        total_amount += amount

    avg_amount = total_amount / len(amounts) if amounts else 0
    max_amount = max(amounts) if amounts else 0
    min_amount = min(amounts) if amounts else 0

    return JSONResponse(content={
        'success': True,
        'job_id': job_id,
        'filename': job.filename,
        'vendor': job.report.template_name,
        'invoices_found': job.report.invoices_found,
        'invoices_valid': job.report.invoices_valid,
        'is_valid': job.report.is_valid,
        'template_created': job.report.template_created,
        'invoices': invoices_data,
        'excel_file': Path(job.excel_path).name if job.excel_path else None,
        'dashboard': {
            'total_amount': total_amount,
            'average_amount': avg_amount,
            'highest_amount': max_amount,
            'lowest_amount': min_amount,
            'amounts': amounts,
        }
    })


# =============================================================================
# File Download and History
# =============================================================================

@app.get("/download/{filename}")
async def download_file(filename: str):
    """Download generated Excel file."""
    file_path = OUTPUT_DIR / filename
    if file_path.exists():
        return FileResponse(
            path=str(file_path),
            filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    raise HTTPException(status_code=404, detail="File not found")


@app.get("/history")
async def get_history():
    """Get list of processed files and their outputs."""
    processed_files = list(PROCESSED_DIR.glob('*.pdf'))
    output_files = list(OUTPUT_DIR.glob('*.xlsx'))

    return {
        'processed': [f.name for f in processed_files],
        'outputs': [f.name for f in output_files]
    }


if __name__ == '__main__':
    import uvicorn
    print("=" * 70)
    print("PDF INVOICE VALIDATOR - Web Frontend (FastAPI)")
    print("=" * 70)
    print(f"Input directory: {INPUT_DIR}")
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Processed directory: {PROCESSED_DIR}")
    print(f"Templates directory: {TEMPLATES_DIR}")
    print("")
    print("Endpoints:")
    print("  POST /upload         - Upload single PDF (immediate processing)")
    print("  POST /batch/upload   - Upload multiple PDFs (background processing)")
    print("  GET  /batch/{id}     - Get batch status")
    print("  GET  /batches        - List all batches")
    print("")
    print("Starting web server at http://localhost:8000")
    print("=" * 70)
    uvicorn.run(app, host="0.0.0.0", port=8000)
