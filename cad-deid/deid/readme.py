"""Generate README.txt for an attempt from the state DB (final paths only; no original identifiers).
usage: python -m deid.readme out_README.txt [attempt-number]"""
import collections, os, sys
from .pipeline import db

HEAD = '''De-identification pipeline: how to run, limitations, exceptions

HOW TO RUN (see code/):
  python3 -m venv venv && venv/bin/pip install -r code/requirements.txt
  1. python -m deid.pass1 manifest.tsv pass1.jsonl            # fetch identity-bearing bytes (HTTP Range for bulk geometry), collect identities
  2. python -m deid.merge manifest.tsv idents.json pass1.jsonl  # global identity dictionary (path identities + metadata/title-block identities)
  3. python -m deid.pipeline manifest.tsv idents.json         # download -> rewrite -> content-addressed store (+ sqlite state)
  3b. attempt 2 only: python -m deid.pdf_harvest / deid.ole_harvest / deid.merge2 (dictionary additions), then deid.pipeline --local-root cache --prev-db <attempt-1 state.db>, deid.attempt --pending (server-side copies)
  3c. attempt 3 only: python -m deid.dfcount cache df.json; python -m deid.pdf_harvest cache harvest_pdf3.jsonl; python -m deid.merge3 harvest_pdf3.jsonl df.json idents2.json pdf_extra.json; python -m deid.pdfdrop idents2.json state.db pdf_drop.json;
      then MACHINA_PSEUDO=v1 MACHINA_PDF=3 MACHINA_PDF_EXTRA=pdf_extra.json MACHINA_PDF_DROP=pdf_drop.json MACHINA_DF=df.json python -m deid.pipeline ... --local-root cache --prev-db <attempt-2 state.db> --only-from <pdf list>
      (the dictionary files hold original identifiers and are kept out of the submission)
  3d. attempt 4 only: python -m deid.logos build cache imgs.json logo_clusters.json (imgs.json: per-PDF md5 of embedded image streams, from deid.imgscan (python -m deid.imgscan cache imgs.json)); rerun 3c with MACHINA_LOGOS=logo_clusters.json
  3e. attempt 5 (final): python -m deid.pdfharv2 harvest cache harvest5.jsonl; python -m deid.pdfharv2 merge harvest5.jsonl df.json idents2.json manifest.tsv pdf_extra3.json pdf_extra5.json;
      rerun the pipeline with MACHINA_PDF_EXTRA=pdf_extra5.json MACHINA_OLE=3 MACHINA_LOGOS=logo_clusters.json over the PDF and OLE files
  4. python -m deid.upload_store --prefix <dest>/corpus/      # resumable upload; identical outputs uploaded once then server-side copied
  5. python -m deid.readme README.txt                         # this file
The pseudonym key is a random secret kept outside the submission; it is not included.

METHOD:
  Every identity (repo owner, repository/project names, catalog titles, website domains, person/organisation names found in file
  metadata and drawing title blocks, e-mail addresses, user and host names, phone numbers, URLs) maps to one pseudonym through
  HMAC(secret, normalised identity). A pseudonym has the same length, case style and separators as the original, so the same
  identity gets the same pseudonym in every file name, folder name, archive member and byte of content, and binary formats keep
  their internal offsets, lengths and checksums valid. Only identity tokens are renamed; every other path segment, the
  folder structure, extensions and the 12-hex repository suffix are unchanged. References between files change together with the
  files because file names and in-file references go through the same function.
  Containers are rewritten recursively: zip, FCStd, 3MF, F3D, ODG, gz, tar, PDF (metadata, XMP, outline, links, attachments, page text),
  SolidWorks chunk containers (checksums and sizes recomputed), OLE compound files (Inventor, SolidEdge, older SolidWorks).
  Embedded preview/thumbnail images are replaced by a neutral gray image of the same size and format.
  Attempt 2 additions: identities are also harvested from the body text of every PDF (e-mails, domains/URLs, phone numbers, street
  addresses, organisation names with company/institution suffixes, copyright holders, title-block label values, and spaCy NER
  person/organisation names filtered by a deny-list and a lower-case-usage test) and from OLE property sets; custom PDF /Info keys,
  annotations, form fields, links and outlines are rewritten; pre-2015 SolidWorks OLE files carry zlib-compressed *__ZLB streams, which
  are inflated, rewritten and re-deflated into the same compressed size; file and folder names that mix separators (for example
  'A-B c d') now match; unchanged files are server-side copies of attempt 1 (same bytes), re-processed files are uploaded.
  Attempt 3 (PDF rewrite, deid/pdfx.py): page text is matched on the exact glyph boxes of every page (PyMuPDF rawdict, whole page as one
  text, case/ligature/zero-width/line-break tolerant) instead of search_for, whose boxes were wrong on some pages and left the identity
  in the text layer; matched text is removed from the content stream (no fill, vector graphics untouched) and the pseudonym is written at
  the original baseline; every PDF is verified by re-extracting its text. Identities are now also found per PDF: author/account tokens of
  /Info and XMP ('DOMAIN\\user (HOST)'), the owner/repository/web scope names of the file's path (camelCase split), e-mails and non-public
  domains and phone numbers in the text, names printed next to title-block labels (drawn/checked/approved ... in several languages), names
  implied by initial+surname accounts and by e-mail local parts, plus a PDF-only dictionary of person/organisation names from the body
  text of all PDFs (document-frequency and vocabulary filter). Person/organisation names map word by word, so 'Smith, John', 'John Smith'
  and 'JOHN SMITH' share pseudonyms; dictionary identities keep the attempt 1/2 pseudonyms so every unchanged file stays consistent.
  Attempt 4 (deid/logos.py): logos. Raster images that recur in 3 or more PDFs are clustered by a 64-bit difference hash (re-encoded or resized
  copies join one cluster); clusters that look like drawing content (line art, large images, thin tile strips, single-source images away from
  the header/title-block band) are left alone. Every image matching a kept cluster, including nested form XObjects, is replaced by one
  deterministic plate per cluster (same bytes in every PDF, a different plate and generated pseudonym name for each cluster); any alpha mask
  is removed. Camel-case repository names now get the same pseudonym in PDF text ('Acme Robotics') as in the path ('AcmeRobotics').
  PDF output is now reproducible (the random trailer /ID written by MuPDF is replaced by a hash of the file).
  Attempt 5: (a) part/drawing numbers are identities inside PDF text: label-driven values (DWG NO, PART NUMBER, DOCUMENT NUMBER, Zeichnungsnummer ...),
  code chains, part families, the drawing's own file-name code, and part codes carried by any file or folder name of the corpus (descriptive
  snake_case names, words, hash ids and standards are excluded). They are replaced in PDF text and metadata only; file and folder names and native
  CAD content keep their codes, so drawing and model files stay paired by name. Pseudonyms keep the shape (letters stay letters, digit groups stay
  digit groups) and are collision-free for 1-3 letter words and digit-only groups. (b) more PDF identity types: institutions, companies (legal suffixes
  in several languages, PROPERTY OF / copyright holders), initials+surname, first-name+surname, street and postcode addresses, phones, project labels
  and CamelCase project names. (c) OLE files: every saved account of the file (author, last saved by, manager, company, also generic ones) is replaced
  in all streams including zlib members, and unallocated sectors and mini-sectors (stale copies of earlier property sets) are zeroed; stream contents,
  offsets and file length are unchanged. Nothing in a PDF is reverted to its original on a rendering warning.
  Fixed: PDFs with attachments failed in attempt 2 (embfile_upd) and fell back to a plain byte replace; they are now rewritten.

'''


def main(out, attempt='1'):
    c = db(); L = [HEAD]
    n = c.execute("select count(*) from files where status='ok'").fetchone()[0]
    L.append(f'FILES: {n} processed.\n\n')
    def lst(title, rows, cap=150):
        L.append(f'{title} ({len(rows)}):\n')
        for r in rows[:cap]: L.append('  ' + str(r) + '\n')
        if len(rows) > cap: L.append(f'  ... and {len(rows) - cap} more\n')
        L.append('\n')
    lst('Files copied without modification (DWG: compressed, CRC-protected sections that cannot be edited safely; strings in them are not pseudonymized)',
        [r[0] for r in c.execute("select out from files where status='ok' and lower(out) like '%.dwg'")])
    lst('Files copied unchanged because they are too large to process', [r[0] for r in c.execute("select out from files where note like 'unchanged%'")])
    lst('Empty files in the source (kept empty)', [r[0] for r in c.execute("select out from files where status='ok' and size=0")])
    lst('Files where a format handler failed and a plain byte-level pseudonym replacement was used instead (source defect or unsupported variant)',
        [f'{r[0]}  [{r[1][:80]}]' for r in c.execute("select out,note from files where status='ok' and note != '' and note not like 'unchanged%' and note not like 'pdf3:%' and note not like '%pdf3%' and note != 'reused from previous attempt'")])
    lst('PDFs rewritten by the attempt-3 handler whose self-check reported a warning (a pseudonym spelling that coincides with a dictionary word, or a replacement too wide for its box)',
        [f'{r[0]}  [{r[1][:80]}]' for r in c.execute("select out,note from files where status='ok' and note like '%pdf3%'")], cap=40)
    lst('Files that could not be processed (kept out of the corpus; reason shown)', [f'{r[0]}  [{r[1][:80]}]' for r in c.execute("select coalesce(out,rel),note from files where status in ('error','dlfail')")])
    d = c.execute("select count(*), sum(k-1) from (select count(*) k from files where status='ok' group by md5 having k>1)").fetchone()
    L.append(f'DUPLICATES: {d[1] or 0} output files are byte-identical to another output file ({d[0] or 0} groups); all are kept at their own paths.\n\n')
    L.append('KNOWN LIMITATIONS:\n  - Logos and text inside raster images and scanned PDF pages are not detected; embedded previews are blanked.\n'
             '  - Identities that appear only as free text in drawings (and never in a metadata field, title-block label or path) are not found.\n'
             '  - SolidWorks/Inventor/NX/Creo outputs were checked structurally (container parse, checksums), not opened in the native applications.\n'
             '  - Source defects are listed above: truncated downloads, archives with unsupported compression methods, broken PDFs.\n')
    open(out, 'w').write(''.join(L))


if __name__ == '__main__':
    main(*sys.argv[1:])
