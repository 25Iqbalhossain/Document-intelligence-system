# Performance Notes (measured)

All numbers below come from `scripts/benchmark_pdf.py` run in the development sandbox (single process, CPU only, no GPU).
They are **measurements of synthetic documents**, not production benchmarks. Re-run the script on target hardware.

## Digital PDF, batches of 20 pages (native text extraction)
| Pages | Seconds | Peak RSS (MB) |
|------:|--------:|--------------:|
| 100 | 0.06 | 95.1 |
| 300 | 0.17 | 95.1 |
| 1000 | 0.67 | 95.2 |

Peak memory did not grow with document size: it is set by the batch, not the file.
Caveat: the synthetic pages are plain text, 0.5 MB total. Real 1,000-page PDFs with embedded images and fonts will be heavier; this is not yet tested.

## Scanned PDF (OCR), 10-page sample, batches of 20
| Pipeline | Seconds / page | Peak RSS (MB) | Mean OCR confidence |
|----------|---------------:|--------------:|--------------------:|
| fastNlMeans denoise (first version) | 3.76 | 237 | 93.8 |
| median-filter denoise (current) | 0.80 | 233 | 93.6 |

Profile of one 300-dpi A4 page before the change: denoise 3.21 s, orientation detection 0.54 s, Tesseract 0.27 s, everything else under 0.15 s.

**Estimate, not measured:** at ~0.8 s/page a 1,000-page scanned PDF would take roughly 13 minutes on one worker, before parallelism. A 1,000-page scanned document has not been run end to end.

## Not yet measured
- Real scanned documents, dense invoices, poor-quality photos (the OCR confidence above is on clean synthetic text).
- Bengali OCR (no `ben` language pack in the sandbox; the Docker image installs it).
- Memory of a 1,000-page scanned PDF with real page images.
- XLSX with very large sheets (the XLSX path loads the workbook in normal mode to read merged cells, so memory scales with sheet size).
