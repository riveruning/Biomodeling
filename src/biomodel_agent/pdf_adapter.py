"""Versioned, local PyMuPDF adapter. PDF decoding/layout belong to the engine."""
from __future__ import annotations

from contextlib import redirect_stdout
import json
import math
import multiprocessing as mp
import queue
import sys

from .deadlines import Deadline, DeadlineExceeded, positive_seconds

ADAPTER_VERSION = 1
DEFAULT_LIMITS = {'max_pages': 200, 'max_chars': 2_000_000, 'max_blocks': 20_000,
                  'max_tables': 1000, 'max_cells': 100_000}
MAX_LOCATOR_BLOCKS = 32


def options(timeout_s=30., limits=None):
    timeout = positive_seconds(timeout_s, 'PDF timeout', maximum=300)
    if limits is not None and (not isinstance(limits, dict) or set(limits)-set(DEFAULT_LIMITS)):
        raise ValueError('unknown PDF limits')
    values = dict(DEFAULT_LIMITS)
    for key, value in (limits or {}).items():
        if type(value) is not int or not 1 <= value <= DEFAULT_LIMITS[key]:
            raise ValueError(f'PDF limit {key} must be an integer in 1..{DEFAULT_LIMITS[key]}')
        values[key] = value
    return timeout, values


def _bbox(values):
    result = [float(v) for v in values]
    if len(result) != 4 or not all(math.isfinite(v) for v in result) or result[0] > result[2] or result[1] > result[3]:
        raise ValueError('invalid PDF engine bounding box')
    return result


def _extract(data, limits):
    # Only this isolated worker imports or invokes the native PDF engine.
    import pymupdf
    pages, warnings = [], []
    totals = dict.fromkeys(DEFAULT_LIMITS, 0)
    def charge(key, amount):
        totals[key] += amount
        if totals[key] > limits[key]:
            raise ValueError('PDF output limit exceeded: '+key)
    with pymupdf.open(stream=data, filetype='pdf') as document:
        if document.needs_pass:
            raise ValueError('encrypted PDF requires an explicitly decrypted local copy')
        charge('max_pages', document.page_count)
        if document.page_count < 1:
            raise ValueError('PDF has no pages')
        if document.is_repaired:
            warnings.append('PDF engine repaired document structure in memory; original bytes retained; review required')
        for i, page in enumerate(document, 1):
            blocks, parts, images = [], [], []
            cursor = 0
            flags = pymupdf.TEXTFLAGS_BLOCKS & ~pymupdf.TEXT_PRESERVE_IMAGES
            for native in page.get_text('blocks', flags=flags, sort=True):
                if native[6] != 0:
                    continue
                text = native[4]
                if not isinstance(text, str):
                    raise ValueError('PDF engine returned non-text block')
                charge('max_chars', len(text))
                charge('max_blocks', 1)
                block = {'id': f'p{i}:b{native[5]}', 'engine_block': int(native[5]),
                         'bbox': _bbox(native[:4]), 'start': cursor, 'end': cursor+len(text),
                         'text': text, 'text_layer': 'native_pdf_text'}
                blocks.append(block)
                parts.append(text)
                cursor += len(text)
                if not text.endswith('\n'):
                    parts.append('\n')
                    cursor += 1
                    charge('max_chars', 1)
            text = ''.join(parts)
            for image in page.get_image_info(hashes=False):
                charge('max_blocks', 1)
                images.append({'bbox': _bbox(image['bbox'])})
            status = 'text_extracted' if text.strip() else 'textless'
            if images and text.strip():
                status = 'text_with_images'
            if not text.strip():
                warnings.append(f'page {i}: no extractable text; blank/scan/vector content needs visual review or OCR')
            if images:
                warnings.append(f'page {i}: image regions were not transcribed; visual/formula review required')
            if '\ufffd' in text:
                warnings.append(f'page {i}: replacement characters in extracted text; review font decoding')
            tables, table_status = [], 'completed'
            try:
                found = page.find_tables().tables
            except Exception:
                found, table_status = [], 'failed'
                warnings.append(f'page {i}: native table detection failed; text is retained, table coverage unknown')
            for j, table in enumerate(found, 1):
                charge('max_tables', 1)
                charge('max_cells', table.row_count*table.col_count)
                try:
                    rows = table.extract()
                    cells = [None if cell is None else _bbox(cell) for cell in table.cells]
                    if (len(cells) != table.row_count*table.col_count or len(rows) != table.row_count or any(len(row) != table.col_count for row in rows) or
                            any(cell is not None and not isinstance(cell, str) for row in rows for cell in row)):
                        raise ValueError('invalid table shape')
                    charge('max_chars', sum(len(cell) for row in rows for cell in row if cell is not None))
                    tables.append({'id': f'p{i}:t{j}', 'bbox': _bbox(table.bbox), 'rows': rows, 'cells': cells,
                                   'row_count': table.row_count, 'column_count': table.col_count,
                                   'status': 'extracted_unverified', 'citation_role': 'auxiliary_not_page_text'})
                except ValueError as exc:
                    if 'limit exceeded' in str(exc):
                        raise
                    table_status = 'failed'
                    warnings.append(f'page {i}: native table {j} extraction failed; no cell values invented')
                except Exception:
                    table_status = 'failed'
                    warnings.append(f'page {i}: native table {j} extraction failed; no cell values invented')
            pages.append({'number': i, 'text': text, 'width': float(page.rect.width), 'height': float(page.rect.height),
                          'rotation': int(page.rotation), 'cropbox': _bbox(page.cropbox),
                          'blocks': blocks, 'images': images, 'status': status,
                          'tables': tables, 'table_status': table_status})
    result = {'adapter_version': ADAPTER_VERSION, 'engine': 'pymupdf', 'engine_version': pymupdf.__version__,
              'coordinate_space': 'pymupdf_unrotated_page_points',
              'text_coordinates': 'zero_based_characters_in_extracted_page_text_not_pdf_bytes',
              'text_assembly': 'native_sorted_blocks_with_newline_v1', 'pages': pages, 'warnings': warnings,
              'capabilities': {'text': 'native_pdf_text', 'tables': 'native_unverified', 'ocr': 'not_run',
                               'formula_recognition': 'not_run', 'reading_order': 'engine_coordinate_sort_unverified'},
              'limits': limits, 'output_counts': totals}
    json.dumps(result, allow_nan=False)
    return result


def _worker(payload, out):
    try:
        # Native library notices must not race with the application's JSON wire.
        with redirect_stdout(sys.stderr):
            result = _extract(*payload)
        out.put({'status': 'ok', 'payload': result})
    except Exception as exc:
        out.put({'status': 'failed', 'error': f'PDF parser failed ({type(exc).__name__}): {str(exc)[:500]}'})


def parse(data, *, timeout_s=30., limits=None):
    """No fallback, network, document writes, OCR or unbounded in-process parse."""
    timeout, bounds = options(timeout_s, limits)
    deadline = Deadline(timeout)
    ctx = mp.get_context('spawn')
    out = ctx.Queue()
    process = ctx.Process(target=_worker, args=((data, bounds), out), daemon=True)
    started = False
    try:
        process.start()
        started = True
        while True:
            try:
                result = out.get(timeout=min(.05, deadline.remaining()))
                deadline.remaining()
                if result.get('status') != 'ok':
                    raise ValueError(result.get('error', 'PDF parser failed'))
                return result['payload']
            except queue.Empty:
                if not process.is_alive():
                    raise ValueError('PDF parser exited without a complete result')
    except DeadlineExceeded as exc:
        raise ValueError('PDF parsing timeout budget exceeded') from exc
    finally:
        if started or process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=.2)
            if process.is_alive():
                process.kill()
                process.join(timeout=.2)
            if process.is_alive():
                raise RuntimeError('PDF parser could not be stopped')
        out.close()
        out.join_thread()


def locate(store, source_ref, page_number, start, end):
    """Return bounded geometry for an exact source span; legacy sources lack it."""
    from .storage import ArtifactError
    source = store.get(source_ref)['payload']
    output_ref = source.get('parser_output_ref')
    if not output_ref:
        return None
    parsed = store.get(output_ref)
    data = parsed['payload']
    if (parsed['kind'] != 'pdf_extraction' or data.get('adapter_version') != ADAPTER_VERSION or
            source.get('adapter_version') != ADAPTER_VERSION or source.get('capabilities') != data.get('capabilities') or
            data.get('pages') != source['pages'] or data.get('engine') != source['parser'] or
            data.get('engine_version') != source['parser_version'] or source['blob_ref'] not in parsed['refs']):
        raise ArtifactError('source differs from immutable PDF parser output')
    page = next((p for p in data['pages'] if p['number'] == page_number), None)
    if (page is None or any(type(v) is not int for v in (page_number, start, end)) or
            not 0 <= start < end <= len(page['text'])):
        raise ArtifactError('invalid PDF source span')
    hits = [b for b in page['blocks'] if b['start'] < end and b['end'] > start]
    return {'pdf_blob_ref': source['blob_ref'], 'parser_output_ref': output_ref, 'page': page_number,
            'start': start, 'end': end, 'coordinate_space': data['coordinate_space'],
            'text_layer': 'native_pdf_text', 'block_count': len(hits), 'truncated': len(hits) > MAX_LOCATOR_BLOCKS,
            'blocks': [{'id': b['id'], 'bbox': b['bbox'], 'start': max(start, b['start']), 'end': min(end, b['end'])}
                       for b in hits[:MAX_LOCATOR_BLOCKS]]}
