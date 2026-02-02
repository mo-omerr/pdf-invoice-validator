#!/usr/bin/env python3
"""
PDF File Watcher with Multi-Template AI Validation
Monitors a folder for PDF uploads, automatically detects vendor,
creates templates for new vendors, and validates/exports to Excel.
"""

import time
import sys
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from pdf_validator import validate_pdf, format_report, export_to_excel


class PDFHandler(FileSystemEventHandler):
    """Handler that triggers when PDF files are created in the watched folder."""

    def __init__(self, templates_dir: str = None, output_dir: str = None, processed_dir: str = None):
        super().__init__()
        self.templates_dir = templates_dir
        self.output_dir = output_dir
        self.processed_dir = processed_dir

    def _process_pdf(self, file_path: Path):
        """Process and validate a PDF file using AI."""
        print(f"\n{'='*70}")
        print(f"PDF file uploaded: {file_path.name}")
        print(f"{'='*70}")

        # Wait a moment to ensure file is fully written
        time.sleep(1.0)

        try:
            # Validate the PDF using AI (with auto template detection/creation)
            report = validate_pdf(str(file_path), templates_dir=self.templates_dir)
            print(format_report(report))

            # Export to Excel
            if report.invoices_found > 0:
                if self.output_dir:
                    output_path = Path(self.output_dir) / f"{file_path.stem}_invoices.xlsx"
                else:
                    output_path = file_path.parent / f"{file_path.stem}_invoices.xlsx"

                excel_path = export_to_excel(report, str(output_path))
                print(f"\nExcel file created: {excel_path}")

            if report.is_valid:
                print(f"\nRESULT: {file_path.name} is VALID")
            else:
                print(f"\nRESULT: {file_path.name} has VALIDATION ISSUES")
                if report.invoices_valid > 0:
                    print(f"  {report.invoices_valid} of {report.invoices_found} invoices are valid")

            if report.template_created:
                print(f"\nNOTE: New template was created for vendor '{report.template_name}'")
                print(f"      Template saved to: ./templates/")

            # Move processed PDF to processed folder
            if self.processed_dir:
                processed_path = Path(self.processed_dir) / file_path.name
                # Handle duplicate filenames
                if processed_path.exists():
                    base = processed_path.stem
                    ext = processed_path.suffix
                    counter = 1
                    while processed_path.exists():
                        processed_path = Path(self.processed_dir) / f"{base}_{counter}{ext}"
                        counter += 1
                import shutil
                shutil.move(str(file_path), str(processed_path))
                print(f"\nPDF moved to: {processed_path}")

        except Exception as e:
            import traceback
            print(f"Error validating PDF: {e}")
            traceback.print_exc()

    def on_created(self, event):
        """Called when a file or directory is created."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        if file_path.suffix.lower() == '.pdf':
            self._process_pdf(file_path)

    def on_moved(self, event):
        """Called when a file is moved into the watched folder."""
        if event.is_directory:
            return

        file_path = Path(event.dest_path)
        if file_path.suffix.lower() == '.pdf':
            self._process_pdf(file_path)


def watch_folder(folder_path: str, templates_dir: str = None, output_dir: str = None, processed_dir: str = None):
    """
    Watch a folder for PDF file uploads and validate them using AI.

    Args:
        folder_path: Path to the folder to monitor
        templates_dir: Directory for templates (optional, defaults to ./templates)
        output_dir: Directory for Excel output (optional, defaults to ./output)
        processed_dir: Directory to move processed PDFs (optional, defaults to ./processed)
    """
    path = Path(folder_path)

    if not path.exists():
        print(f"Error: Folder '{folder_path}' does not exist.")
        sys.exit(1)

    if not path.is_dir():
        print(f"Error: '{folder_path}' is not a directory.")
        sys.exit(1)

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY environment variable not set")
        print("Set it with: export ANTHROPIC_API_KEY='your-api-key'")
        sys.exit(1)

    # Set default templates directory
    if templates_dir is None:
        templates_dir = Path(__file__).parent / "templates"
    templates_dir = Path(templates_dir)
    templates_dir.mkdir(exist_ok=True)

    # Set default output directory
    if output_dir is None:
        output_dir = Path(__file__).parent / "output"
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    # Set default processed directory
    if processed_dir is None:
        processed_dir = Path(__file__).parent / "processed"
    processed_dir = Path(processed_dir)
    processed_dir.mkdir(exist_ok=True)

    event_handler = PDFHandler(str(templates_dir), str(output_dir), str(processed_dir))
    observer = Observer()
    observer.schedule(event_handler, str(path), recursive=False)

    print(f"{'='*70}")
    print("PDF INVOICE WATCHER - Multi-Template Support")
    print(f"{'='*70}")
    print(f"Watching folder: {path.absolute()}")
    print(f"Templates directory: {templates_dir.absolute()}")
    print(f"Excel output directory: {output_dir.absolute()}")
    print(f"Processed PDFs directory: {processed_dir.absolute()}")
    print("")
    print("Features:")
    print("  - Automatically detects vendor from PDF")
    print("  - Uses existing template if vendor is known")
    print("  - Creates new template for unknown vendors")
    print("  - Validates all invoices in PDF")
    print("  - Exports to Excel (one sheet per invoice)")
    print("  - Moves processed PDFs to processed folder")
    print("")
    print("Waiting for PDF files... (Press Ctrl+C to stop)")
    print("-" * 70)

    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping watcher...")
        observer.stop()

    observer.join()
    print("Watcher stopped.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Watch a folder for PDF uploads and validate invoices"
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default="./input",
        help="Folder to watch for PDF uploads (default: ./input)"
    )
    parser.add_argument(
        "--templates",
        "-t",
        help="Templates directory (default: ./templates)"
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output directory for Excel files (default: ./output)"
    )
    parser.add_argument(
        "--processed",
        "-p",
        help="Directory to move processed PDFs (default: ./processed)"
    )

    args = parser.parse_args()
    watch_folder(args.folder, args.templates, args.output, args.processed)
