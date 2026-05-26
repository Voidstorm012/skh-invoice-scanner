#!/usr/bin/env python3
"""Scan SingHealth/Sengkang medical bill PDFs into structured exports.

The program treats the input folder as an inbox. On a real run, successfully
parsed PDFs are renamed to YYYY-MM-DD_BILLREF.pdf and moved to the processed
folder so future runs only see new bills.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - exercised by users without deps
    fitz = None


DATE_FORMATS = (
    "%d %b %Y %I:%M %p",
    "%d %b %Y",
)

SUMMARY_FIELDS = {
    "TOTAL AMOUNT(BEFORE GOVT SUBSIDY)": "total_before_govt_subsidy",
    "TOTAL AMOUNT (BEFORE GOVT SUBSIDY)": "total_before_govt_subsidy",
    "GOVT SUBSIDY": "govt_subsidy",
    "TOTAL AMOUNT (BEFORE GST)": "total_before_gst",
    "9% GST": "gst",
    "GST absorbed by Govt": "gst_absorbed_by_govt",
    "TOTAL AMOUNT (AFTER GOVT SUBSIDY)": "total_after_govt_subsidy",
    "Payable by MEDISAVE": "payable_by_medisave",
    "TOTAL AMOUNT PAYABLE": "total_amount_payable",
    "Net Payment made": "net_payment_made",
    "FINAL AMOUNT PAYABLE": "final_amount_payable",
}

INVOICE_COLUMNS = [
    "source_file",
    "new_file",
    "bill_ref_no",
    "bill_date",
    "document_type",
    "provider",
    "patient_name",
    "nric_fin_mrn",
    "hrn",
    "location",
    "visit_date",
    "admission_date",
    "discharge_date",
    "total_before_govt_subsidy",
    "govt_subsidy",
    "total_before_gst",
    "gst",
    "gst_absorbed_by_govt",
    "total_after_govt_subsidy",
    "payable_by_medisave",
    "total_amount_payable",
    "net_payment_made",
    "final_amount_payable",
    "payment_date",
    "payment_mode",
    "payment_amount",
    "status",
    "page_count",
    "duplicate_bill_ref",
]

ITEM_COLUMNS = [
    "bill_ref_no",
    "bill_date",
    "source_file",
    "service_category",
    "charge_group",
    "description",
    "before_govt_subsidy",
    "after_govt_subsidy",
]


@dataclass
class ChargeItem:
    service_category: str = ""
    charge_group: str = ""
    description: str = ""
    before_govt_subsidy: str = ""
    after_govt_subsidy: str = ""


@dataclass
class Invoice:
    source_file: str
    new_file: str = ""
    bill_ref_no: str = ""
    bill_date: str = ""
    document_type: str = ""
    provider: str = "Sengkang General Hospital"
    patient_name: str = ""
    nric_fin_mrn: str = ""
    hrn: str = ""
    location: str = ""
    visit_date: str = ""
    admission_date: str = ""
    discharge_date: str = ""
    total_before_govt_subsidy: str = ""
    govt_subsidy: str = ""
    total_before_gst: str = ""
    gst: str = ""
    gst_absorbed_by_govt: str = ""
    total_after_govt_subsidy: str = ""
    payable_by_medisave: str = ""
    total_amount_payable: str = ""
    net_payment_made: str = ""
    final_amount_payable: str = ""
    payment_date: str = ""
    payment_mode: str = ""
    payment_amount: str = ""
    status: str = ""
    page_count: int = 0
    duplicate_bill_ref: bool = False
    raw_payment_summary: str = ""
    charge_items: list[ChargeItem] = field(default_factory=list)


def clean_line(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def clean_lines(text: str) -> list[str]:
    return [line for line in (clean_line(line) for line in text.splitlines()) if line]


def amount_to_decimal(value: str) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def is_amount(value: str) -> bool:
    return bool(re.fullmatch(r"-?\d{1,3}(?:,\d{3})*(?:\.\d{2})|-?\d+(?:\.\d{2})", value))


def normalize_date(value: str) -> str:
    value = clean_line(value)
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(value.upper(), fmt)
            if "%I:%M" in fmt:
                return parsed.strftime("%Y-%m-%d %H:%M")
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value


def value_after_label(lines: list[str], label: str) -> str:
    for i, line in enumerate(lines):
        if line == label:
            for candidate in lines[i + 1 : i + 5]:
                if candidate != "$":
                    return candidate
        if line.startswith(label + " "):
            return line[len(label) :].strip()
    return ""


def parse_summary_amounts(invoice: Invoice, lines: list[str]) -> None:
    try:
        end = lines.index("CHARGES")
        summary_lines = lines[:end]
    except ValueError:
        summary_lines = lines

    for i, line in enumerate(summary_lines):
        key = SUMMARY_FIELDS.get(line)
        if not key:
            continue
        for candidate in summary_lines[i + 1 : i + 5]:
            if candidate == "$":
                continue
            if is_amount(candidate):
                setattr(invoice, key, candidate)
                break


def parse_header(invoice: Invoice, text: str) -> None:
    lines = clean_lines(text)
    invoice.document_type = next((line for line in lines if line.startswith("TAX INVOICE")), "")
    invoice.bill_ref_no = value_after_label(lines, "BILL REF. NO.")
    invoice.bill_date = normalize_date(value_after_label(lines, "BILL DATE"))
    invoice.hrn = value_after_label(lines, "HRN")
    invoice.nric_fin_mrn = value_after_label(lines, "NRIC / FIN / MRN")
    invoice.location = value_after_label(lines, "LOCATION")
    invoice.visit_date = normalize_date(value_after_label(lines, "VISIT DATE"))
    invoice.admission_date = normalize_date(value_after_label(lines, "ADMISSION DATE"))
    invoice.discharge_date = normalize_date(value_after_label(lines, "DISCHARGE DATE"))
    invoice.patient_name = value_after_label(lines, "PATIENT NAME")

    if not invoice.patient_name:
        match = re.search(r"\n\s*([A-Z][A-Z ]+?)\s*\n\s*PRINTED ON:", text.replace("\xa0", " "))
        if match:
            invoice.patient_name = clean_line(match.group(1))

    parse_summary_amounts(invoice, lines)


def classify_status(final_amount: str) -> str:
    final = amount_to_decimal(final_amount)
    if final is None:
        return "unknown"
    if final == 0:
        return "paid"
    if final > 0:
        return "outstanding"
    return "credit"


def group_words_into_rows(words: Iterable[tuple]) -> list[list[tuple]]:
    rows: list[list[tuple]] = []
    for word in sorted(words, key=lambda w: (round(w[1], 1), w[0])):
        x0, y0, *_ = word
        if not rows or abs(rows[-1][0][1] - y0) > 3:
            rows.append([word])
        else:
            rows[-1].append(word)
    return rows


def words_text(words: list[tuple]) -> str:
    return clean_line(" ".join(word[4] for word in sorted(words, key=lambda w: w[0])))


def is_likely_charge_group(text: str) -> bool:
    if not text:
        return False
    if text.startswith("("):
        return False
    has_letter = any(ch.isalpha() for ch in text)
    return has_letter and text.upper() == text


def parse_charge_items(page) -> list[ChargeItem]:
    words = page.get_text("words")
    table_words = [w for w in words if 330 <= w[1] <= 620 and w[0] >= 45]
    rows = group_words_into_rows(table_words)

    items: list[ChargeItem] = []
    service_parts: list[str] = []
    current_service = ""
    current_group = ""

    for row in rows:
        service = words_text([w for w in row if 45 <= w[0] < 175])
        desc = words_text([w for w in row if 175 <= w[0] < 400])
        before = words_text([w for w in row if 400 <= w[0] < 455])
        after = words_text([w for w in row if 455 <= w[0] < 550])

        if service == "SERVICES" and desc == "DESCRIPTION":
            continue
        if desc.startswith("TOTAL AMOUNT") or desc in {"GOVT SUBSIDY", "9% GST"}:
            break

        if service:
            if before or after:
                current_service = service
                service_parts = []
            else:
                service_parts.append(service)
                current_service = clean_line(" ".join(service_parts))

        if before and after and is_amount(before) and is_amount(after):
            items.append(
                ChargeItem(
                    service_category=current_service,
                    charge_group=current_group,
                    description=desc,
                    before_govt_subsidy=before,
                    after_govt_subsidy=after,
                )
            )
            service_parts = []
            continue

        if before or after:
            continue

        if desc and items and desc.startswith("("):
            items[-1].description = clean_line(f"{items[-1].description} {desc}")
        elif desc and is_likely_charge_group(desc):
            current_group = desc

    return items


def slice_between(lines: list[str], start: str, end: str) -> list[str]:
    try:
        start_i = lines.index(start)
    except ValueError:
        return []
    try:
        end_i = lines.index(end, start_i + 1)
    except ValueError:
        end_i = len(lines)
    return lines[start_i:end_i]


def parse_payment(invoice: Invoice, text: str) -> None:
    lines = clean_lines(text)
    invoice.raw_payment_summary = "\n".join(slice_between(lines, "PAYMENT SUMMARY", "PAYMENT OPTIONS & ADVISORY"))

    try:
        payor_i = lines.index("PAYOR(S)")
    except ValueError:
        return

    section = []
    for line in lines[payor_i + 1 :]:
        if line in {"Net Payment made", "PAYMENT OPTIONS & ADVISORY", "FINAL AMOUNT PAYABLE"}:
            break
        if line in {"TRANSACTION/RECEIPT", "DATE", "PAYMENT MODE", "AMOUNT ($)"}:
            continue
        section.append(line)

    for line in section:
        if re.fullmatch(r"\d{2} [A-Z]{3} \d{4}", line):
            invoice.payment_date = normalize_date(line)
        elif line.startswith("EPAY") or "CARD" in line or "CREDIT" in line:
            invoice.payment_mode = line
        elif is_amount(line):
            invoice.payment_amount = line
            break


def extract_invoice(pdf_path: Path) -> Invoice:
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed. Run: python3 -m pip install -r requirements.txt")

    doc = fitz.open(pdf_path)
    page_texts = [page.get_text("text") for page in doc]
    full_text = "\n".join(page_texts)

    invoice = Invoice(source_file=pdf_path.name, page_count=doc.page_count)
    parse_header(invoice, full_text)
    if doc.page_count:
        invoice.charge_items = parse_charge_items(doc[0])
    parse_payment(invoice, full_text)
    invoice.status = classify_status(invoice.final_amount_payable)
    return invoice


def safe_filename_part(value: str) -> str:
    value = re.sub(r"[^\w.-]+", "_", value.strip())
    return value.strip("._")


def destination_for(invoice: Invoice, processed_dir: Path) -> Path:
    date_part = invoice.bill_date or "unknown-date"
    bill_part = safe_filename_part(invoice.bill_ref_no or Path(invoice.source_file).stem)
    base = f"{date_part}_{bill_part}.pdf"
    candidate = processed_dir / base
    if not candidate.exists():
        return candidate
    for i in range(2, 1000):
        candidate = processed_dir / f"{date_part}_{bill_part}_{i}.pdf"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find an available filename for {base}")


def invoice_row(invoice: Invoice) -> dict[str, object]:
    data = asdict(invoice)
    return {key: data.get(key, "") for key in INVOICE_COLUMNS}


def item_rows(invoice: Invoice) -> list[dict[str, str]]:
    rows = []
    for item in invoice.charge_items:
        rows.append(
            {
                "bill_ref_no": invoice.bill_ref_no,
                "bill_date": invoice.bill_date,
                "source_file": invoice.source_file,
                **asdict(item),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, object]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, invoices: list[Invoice]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump([asdict(invoice) for invoice in invoices], handle, indent=2)


def append_log(path: Path, entries: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def existing_bill_refs(log_path: Path) -> set[str]:
    refs: set[str] = set()
    if not log_path.exists():
        return refs
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("status") == "success" and entry.get("bill_ref_no"):
                refs.add(entry["bill_ref_no"])
    return refs


def process(args: argparse.Namespace) -> int:
    input_dir = Path(args.input)
    processed_dir = Path(args.processed)
    output_dir = Path(args.output)
    log_path = output_dir / "processed_log.jsonl"

    if not input_dir.exists():
        print(f"Input folder does not exist: {input_dir}", file=sys.stderr)
        return 2

    pdfs = sorted(input_dir.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {input_dir}")
        return 0

    known_refs = existing_bill_refs(log_path)
    seen_refs: set[str] = set()
    invoices: list[Invoice] = []
    errors: list[dict[str, object]] = []
    log_entries: list[dict[str, object]] = []

    for pdf_path in pdfs:
        try:
            invoice = extract_invoice(pdf_path)
            if not invoice.bill_ref_no or not invoice.bill_date:
                raise ValueError("Missing bill reference number or bill date")

            invoice.duplicate_bill_ref = invoice.bill_ref_no in known_refs or invoice.bill_ref_no in seen_refs
            seen_refs.add(invoice.bill_ref_no)
            destination = destination_for(invoice, processed_dir)
            invoice.new_file = destination.name
            invoices.append(invoice)

            if not args.dry_run:
                processed_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(pdf_path), str(destination))

            log_entries.append(
                {
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    "status": "success",
                    "source_file": invoice.source_file,
                    "destination_file": invoice.new_file,
                    "bill_ref_no": invoice.bill_ref_no,
                    "bill_date": invoice.bill_date,
                    "duplicate_bill_ref": invoice.duplicate_bill_ref,
                    "dry_run": args.dry_run,
                }
            )
        except Exception as exc:  # keep processing the rest of the folder
            errors.append({"source_file": pdf_path.name, "error": str(exc)})
            log_entries.append(
                {
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    "status": "error",
                    "source_file": pdf_path.name,
                    "error": str(exc),
                    "dry_run": args.dry_run,
                }
            )

    if not args.dry_run:
        write_csv(output_dir / "invoices.csv", [invoice_row(inv) for inv in invoices], INVOICE_COLUMNS)
        write_csv(output_dir / "charge_items.csv", [row for inv in invoices for row in item_rows(inv)], ITEM_COLUMNS)
        write_json(output_dir / "invoices.json", invoices)
        append_log(log_path, log_entries)

    print(f"Found PDFs: {len(pdfs)}")
    print(f"Parsed: {len(invoices)}")
    print(f"Errors: {len(errors)}")
    if args.dry_run:
        print("Dry run: no files were moved and no reports were written.")
    else:
        print(f"Moved parsed PDFs to: {processed_dir}")
        print(f"Wrote reports to: {output_dir}")

    for invoice in invoices[:5]:
        duplicate = " duplicate" if invoice.duplicate_bill_ref else ""
        print(f"- {invoice.source_file} -> {invoice.new_file} ({invoice.status}{duplicate})")
    if len(invoices) > 5:
        print(f"... {len(invoices) - 5} more parsed invoices")

    if errors:
        print("\nErrors:")
        for error in errors[:10]:
            print(f"- {error['source_file']}: {error['error']}")
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse and archive medical bill PDFs.")
    parser.add_argument("--input", default="Bills", help="Folder containing unprocessed PDF bills.")
    parser.add_argument("--processed", default="Scanned Bills", help="Folder where parsed PDFs are moved.")
    parser.add_argument("--output", default="scan_output", help="Folder for CSV/JSON reports and log.")
    parser.add_argument("--dry-run", action="store_true", help="Parse only; do not move files or write reports.")
    return parser


def main() -> int:
    return process(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
