# T06 ownership, evidence and delivery

The user clarified that 修正T06 means 开始实现T06. Codex implements and validates independently, continuing the current arrangement. No DSH, subagents, model calls or independent evaluation materials.

1. Verify every hash accepted for T05 and freeze source/config/doc/test hashes and before snapshots. Accepted baseline is 990 tests.
2. Read find-skills and inspect the official PDF skill plus official PyMuPDF interfaces. A skill is a guide, while the application needs a deterministic runtime. Choose a thin, explicit local PyMuPDF backend; preserve legacy pypdf defaults.
3. Sandbox PyPI lookup could not find the distribution. After explicit tool approval, query official PyPI and install wheel-only PyMuPDF 1.28.2 into project .venv with --no-deps. Preserve package/version/license and the unchanged base lock; add a separate optional lock and project extra. No PDF or project material uploaded.
4. Freeze and run 13 minimum acceptance controls against the unchanged code: all fail. Save baseline log/XML.
5. Implement isolated bounded native parsing, normalized layout/table artifact, original-byte retention, exact extracted-text offsets and block locators. Integrate offline CLI, existing Agent ingestion/inspection and bounded capability warnings. Retain old source hashes and test files.
6. Run 83 targeted checks and 33 expanded controls. Review actual table/cell/image extraction, blank/scan pages, geometry tampering, crashes/timeouts and original result adoption. Add table/page fault injection, legacy determinism and CLI/Agent integration; 109 expanded checks pass.
7. First complete run passes 1003 checks. A later full attempt reveals native stdout noise racing with JSON CLI output. Reproduce it deterministically with a flushed native diagnostic, redirect native Python diagnostics to stderr inside the worker, and re-run focused and final complete checks. Keep both the original failure and corrected result.
8. Archive a locally generated synthetic PDF and PNG preview, actual native extraction, real ODE result, immutable bundle, fault artifacts and T03b recovery without recomputation. These are development controls with scripted decisions, not independent or real-model performance scores.
9. Freeze exact final files, diff, hashes, environment record and final acceptance. Update the task queue; T07 requires material/annotation review and is not claimed complete by these synthetic parser controls.

Known limits: no OCR or mathematical formula recognition/transcription; native table data remains unverified; coordinate sorting is not a proof of reading order. Character offsets name the extracted page text, not bytes in the original PDF. No OS RAM or arbitrary crash recovery guarantee. The old c13/c18 USTC material-transmission rejection remains in force.
