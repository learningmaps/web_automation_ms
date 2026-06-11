# Project Conventions: Parivesh Dashboard

## Column Name Mappings

The SQL in `load_agendas()` uses these aliases — always use the alias (right), never the raw DB column (left):

| DB column | Alias in DataFrame |
|---|---|
| `subject` | `raw_subject` |
| `id` | `id` |
| `date` | `date` |
| `pdffilepath` | `pdffilepath` |

### Common mistakes to avoid
- Do NOT use `'subject'` — it's `'raw_subject'`
- Always verify column names against `load_agendas()` query before referencing them in pandas code

## Export Sheet Fields

**Sheet "Proposal Details" columns:**
agenda_id, meeting_id, committee_type, agenda_date, meeting_start_date, meeting_end_date, agenda_subject, sector_name, statename_derived, matched_keywords, sr_no, proposal_no, file_no, project_name, proposal_for, activity, sector, state, district, proponent, has_mom, mom_date, mom_meeting_id, mom_subject, agenda_pdf_url, mom_pdf_url

**Sheet "Agendas Summary" columns:**
agenda_id, meeting_id, date, committee_type, agenda_subject, sector_name, statename_derived, matched_keywords, has_mom, proposal_count, agenda_pdf_url, mom_pdf_url
