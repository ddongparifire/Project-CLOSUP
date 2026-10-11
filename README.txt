CLOSUP LEGACY DIGITAL ACCESSIBILITY REMEDIATION
Prototype workflow

Purpose

This prototype workflow is for remediating approximately 2,900 legacy Drupal
table records (about 3,200 records total). A record may contain up to four HTML
code fields. The goal is to bring tables to WCAG 2.1 AA and align them with the
2025 table template.

The deterministic table tools and local sample-processing loop are implemented
as a prototype. Drupal integrations, AI caption generation, and production use
have not been implemented or approved.

Before processing records

- Use an AI platform approved by the University of Michigan (U-M).
- Select and test deterministic rule tools, and prepare the agent instructions.
- Back up source content and preserve each record's original HTML for rollback.
- Classify records and hand-check a sample of tables against the 2025 template.
- Confirm access and review arrangements listed under Open setup decisions.
- Before introducing any real CLOSUP data, make this repository private and
  confirm the approved data-handling environment and access controls.

Processing workflow

1. Fetch a record's HTML and relevant context, including its template and
   classification.
2. Scan for accessibility issues. Apply deterministic, tested rule-tool fixes
   first.
3. Use AI judgment only for issues the rules cannot resolve, such as writing a
   caption or interpreting ambiguous headers. Treat all AI-written text as
   requiring human review.
4. Validate the result for accessibility and confirm that table cell data is
   unchanged. Write passing results to staging only.
5. Flag validation failures, low-confidence results, and unknown patterns for
   human review. Do not silently accept or publish them.
6. Have a human review every flagged record, all AI-written text, and a random
   5-10% sample of records that pass automatically.
7. For a rejected result, allow one feedback-and-retry cycle. If it is rejected
   a second time, route it for manual remediation. Log reviewer feedback and
   update the rules or agent instructions when issues recur.
8. Publish only human-approved records, in batches of a few hundred. The agent
   cannot publish to production.
9. Re-scan every live table after publication and screen-reader-test
   representative examples. Use the preserved original HTML if rollback is
   needed.

Roles

- Rule tools: Handle known, deterministic fixes that have been tested.
- Remediation agent: Fetch context, run tools, use limited judgment for
  unresolved issues, validate, write passing results to staging, and flag
  uncertain or failed results. It must not publish to production.
- Human reviewer: Review flagged records and AI-written text, sample
  auto-passing records, approve publication, and resolve items sent for manual
  remediation.

Safeguards

- Keep a backup and the original HTML for every processed record.
- Preserve table cell data; validate before writing results to staging.
- Keep low-confidence, failed, and unknown cases out of the passing path.
- Require human approval before production publication.
- Record outcomes and feedback so recurring issues can improve the tools and
  instructions.

Open setup decisions

- Which U-M-approved AI platform to use and its cost.
- How authentication and multi-factor authentication (MFA) will work.
- What staging, API, or Drush access is available and approved.
- Which sample records and 2025 table template will be used for setup and
  hand-checking.
- Who approves text written by AI.
- How Drupal revisions, date fields, and cache effects will be handled during
  staging and publication.

Prototype tools

The first rule-tool prototype is in accessibility_remediation/tables.py. It
uses BeautifulSoup with html5lib for tolerant HTML parsing; it does not connect
to Drupal or publish content. Python 3.10 or later is recommended.

To install its dependencies and run the tests from the repository root:

    python -m pip install -r requirements.txt
    python -m pytest

The ten made-up legacy examples are in samples/legacy/.

Run the local agent-loop prototype against all bundled samples with one command:

    python -m accessibility_remediation

It runs fetch (local file read), scan, deterministic fix, and cell-value
validation for every `.html` sample. Passing tables are written individually
under `workflow_output/staging/`. Tables needing review are listed with reasons
in `workflow_output/review.json`. The HTML comparison report for every table,
including before/after markup and caption-step status, is
`workflow_output/report.html`. Use `--samples-dir` and `--output-dir` to select
different local folders.

The caption step is an explicit placeholder: it does not invent or insert
caption text. A missing caption is routed through that step and remains in the
review list until caption drafting and human review are implemented. Outputs
are local demo artifacts only; the loop does not connect to Drupal or publish
content.

Use the scanner and conservative simple-table fixer from Python:

    from accessibility_remediation import apply_fix, apply_fixes_to_fields, scan_table

    issues = scan_table(table_html)
    result = apply_fix(table_html)
    fixed_html = result.html
    send_to_human_review = result.needs_review

For a record with multiple code fields, submit every supplied field together:

    record_result = apply_fixes_to_fields({
        "field_a": field_a_html,
        "field_b": field_b_html,
    })
    send_record_to_human_review = record_result.needs_review

`apply_fix` processes every table in its HTML field. Its result includes a
confidence score, per-table outcomes, and review reasons; the aggregate
confidence is the lowest table confidence. `apply_fixes_to_fields` also
aggregates across every supplied field. Any inferred first-row header is
explicitly low-confidence and requires human review, even when structural
validation passes. Unsupported tables remain unchanged and are flagged for
review, while other tables in the field are still processed. Results requiring
review must not enter an automatic staging path.

`scan_table` returns issue codes found across all tables, such as missing-caption, no-headers,
header-missing-scope, missing-thead, missing-tbody, unclosed-tag, merged-cells,
multi-level-headers, spacer-row, and irregular-rows. apply_fix handles only regular,
single-header-row tables: it may promote the first non-empty row to column
headers, adds scope values, and creates thead/tbody sections. It preserves the
ordered, normalized text values of all cells and raises an error if they differ.

The fixer deliberately refuses merged cells, irregular or spacer rows,
multi-level headers, ambiguous existing header structures, and footer rows.
It does not invent captions or determine whether inferred headers are
semantically correct. Such results need human review before they could be used.
