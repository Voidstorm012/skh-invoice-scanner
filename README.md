# SKH Invoice Scanner

Parse Sengkang General Hospital / SingHealth tax invoice PDFs into structured
CSV and JSON reports, then archive each scanned PDF so future runs only process
new bills.

The scanner is built for the SKH invoice layout found in generated PDF bills.
It reads embedded PDF text first, which is faster and more accurate than OCR for
these invoices.

## What It Extracts

- Bill reference number and bill date
- Patient name, NRIC / FIN / MRN, HRN, and location
- Visit date, admission date, and discharge date when available
- Charge items with service category, charge group, description, and amounts
- Government subsidy, GST, GST absorbed by government, Medisave, payable amount,
  net payment made, and final amount payable
- Payment date, payment mode, payment amount, and paid/outstanding status

## Folder Workflow

Use `Bills/` as the inbox for new PDFs:

```text
Bills/
```

After a successful scan, PDFs are moved to:

```text
Scanned Bills/
```

Archived PDFs are renamed using:

```text
YYYY-MM-DD_BILLREF.pdf
```

Example:

```text
2026-02-26_Q224204470Z0086.pdf
```

This keeps `Bills/` empty after processing, so adding new PDFs later and running
the scanner again will process only the new files.

## Setup

```bash
python3 -m pip install -r requirements.txt
```

## Preview A Run

Use dry-run mode first. It parses files and shows what would happen without
moving PDFs or writing reports.

```bash
python3 scan_bills.py --dry-run
```

## Scan Bills

```bash
python3 scan_bills.py
```

By default, this uses:

```text
Input folder:      Bills/
Archive folder:    Scanned Bills/
Output folder:     scan_output/
```

## Output Files

A successful run writes:

```text
scan_output/invoices.csv
scan_output/charge_items.csv
scan_output/invoices.json
scan_output/processed_log.jsonl
```

`invoices.csv` contains one row per bill. `charge_items.csv` contains one row per
charge line item.

## Custom Folders

```bash
python3 scan_bills.py \
  --input "Bills" \
  --processed "Scanned Bills" \
  --output "scan_output"
```

## Privacy

Medical bills contain personal and financial information. The repository is set
up to ignore local bill folders and generated output files:

- `Bills/`
- `Scanned Bills/`
- `scan_output/`
- `.ocr_tmp/`

Keep those files local unless you intentionally decide to share them.
