# T06 contract and implementation arrangement

User clarified 开始实现T06. Codex implements independently; no DSH or subagents. Starting baseline: T05 990 passed, all accepted hashes verified before work. Retain original tests and snapshots. No independent evaluation material, online inference, c13/c18 transmission or global environment modifications.

Use a mature local engine, PyMuPDF, behind a thin versioned adapter; do not implement PDF decoding, layout recognition, OCR or formula transcription ourselves. The PDF skill was investigated as an authoring/workflow guide, not as the application's deterministic parser runtime. Project optional PDF dependency is pinned; installation is into this project's .venv only.

1. Freeze representative failures and real PDF controls before changing production code.
2. Opt-in pymupdf backend; retain pypdf default and historical sources. Add an offline source-ingest command and an explicit backend option for Agent source ingestion.
3. Preserve original PDF bytes, pinned engine/version and adapter version, actual parser output as an immutable referenced artifact, every page including empty/failed pages, native text blocks and geometric coordinates. Page character offsets refer to immutable extracted page text, never PDF byte offsets. Do not reconstruct invented raw-PDF text positions.
4. Expose mature-engine table results as structured, unverified auxiliary data; do not promote converted Markdown/table rows or image descriptions to faithful quotes. Keep image/textless pages and unsupported formula/OCR capabilities visible. Do not silently fall back to another engine.
5. Use an isolated bounded parser process with strict positive time/output/page/block/table-cell limits. Reject corrupt/encrypted PDFs and incomplete results rather than creating successful partial sources; retain raw bytes if parsing fails. Native-engine repair or table failure is disclosed and cannot imply complete extraction.
6. Citations can carry intersecting block/bbox locators tied to exact source/span and original PDF. Recompute locators when checking claims; export retains raw PDF and parser-output closure. Retrieval/inspect keep existing source whitelist and character budgets.
7. Make parser/coverage warnings visible to Agent without injecting document content or unlimited metadata into its initial context. Unknown visual or semantic evidence remains unknown; a parser does not establish T05 binding, units or scientific truth.
8. Run meaningful actual extraction, scan/image/table, source whitelist, byte-preservation, tamper/limit/timeout, legacy, real-ODE, export and recovery checks. Record full regression, exact diffs/hashes, environment addition and limitations. No model-capability improvement claims.
