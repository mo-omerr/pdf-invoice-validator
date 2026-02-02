# PDF Invoice Validator

AI-powered PDF invoice extraction and validation system using Claude API.

## Features

- Monitors a folder for PDF uploads
- Automatically detects vendor from invoice
- Creates templates for new vendors
- Extracts all invoice data using AI (Claude Vision)
- Exports to Excel with Dashboard and Charts
- Shows trends and patterns in invoice data
- Moves processed PDFs to a separate folder

## Dashboard & Charts

Each exported Excel file includes:

- **Dashboard Sheet**: Key metrics (total amount, average, highest/lowest invoice) with visual charts
- **Summary Sheet**: Invoice list with data table and trend charts
- **Invoice Sheets**: Individual invoice details

### Charts Included:
1. **Bar Chart**: Invoice amounts over time
2. **Line Chart**: Amount trend visualization
3. **Stacked Bar Chart**: Subtotal vs Tax breakdown
4. **Pie Chart**: Invoice distribution (percentage)

## Setup

### 1. Create virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install anthropic watchdog PyMuPDF Pillow openpyxl python-dotenv fastapi uvicorn python-multipart jinja2
```

### 3. Configure API key

Copy the example env file and add your Anthropic API key:

```bash
cp .env.example .env
```

Edit `.env` and add your key:

```
ANTHROPIC_API_KEY=your-api-key-here
```

## Usage

### Option 1: Web Interface (Recommended)

Start the web server:

```bash
python app.py
```

Open your browser and go to `http://localhost:8000`

Features:
- Drag and drop PDF upload
- Real-time processing with AI
- Interactive dashboard with charts
- View all extracted invoice data
- Download Excel report

### Option 2: Folder Watcher (CLI)

```bash
python pdf_watcher.py
```

This will:
- Watch `./input/` for new PDF files
- Save Excel output to `./output/`
- Move processed PDFs to `./processed/`
- Store/load templates from `./templates/`

### Custom directories

```bash
python pdf_watcher.py /path/to/watch --output /path/to/output --processed /path/to/processed --templates /path/to/templates
```

### Command line options

| Option | Short | Description |
|--------|-------|-------------|
| `folder` | | Folder to watch (default: `./input`) |
| `--output` | `-o` | Excel output directory (default: `./output`) |
| `--processed` | `-p` | Processed PDFs directory (default: `./processed`) |
| `--templates` | `-t` | Templates directory (default: `./templates`) |

## Folder Structure

```
.
├── input/           # Drop PDF files here
├── output/          # Excel files are saved here
├── processed/       # Processed PDFs are moved here
├── templates/       # Vendor templates (auto-generated)
├── app.py           # Web frontend (FastAPI)
├── templates_html/  # HTML templates for web UI
├── pdf_watcher.py   # Folder watcher script (CLI)
├── pdf_validator.py # Validation and extraction logic
├── .env             # API key configuration
└── README.md
```

## How it works

1. Drop a PDF into `./input/`
2. The system detects the vendor using AI
3. If template exists, it's used for validation
4. If no template, a new one is created automatically
5. All invoices are extracted and validated
6. Results are exported to Excel
7. PDF is moved to `./processed/`
