# Meeting Intelligence Fabric

Feature-flagged module (`ENABLE_MEETING_INTELLIGENCE=false` by default). Does **not** modify calendar scheduling (`agents/meeting.py`).

## Flow

1. `list my recent meetings` — scans SharePoint/OneDrive `Recordings/` and `meeting transcript/` for `.vtt` files
2. `select meetings 1,3` — stores selection in session (`meeting_selected`)
3. `make a plan` / `create implementation plan` — LLM plan → SharePoint `MeetingPlans/` (`.md` + `.docx`)
4. `email the plan to alice@co.com` — Gmail (WhatsApp) or Graph `sendMail` (Teams)
5. `create devops board from plan` — creates AzDO Feature + Tasks (requires `ENABLE_BOARDS_AGENT`)

## Handoffs

- **Email Agent / Outlook** — unchanged; meeting email uses dedicated `meeting_mail` service
- **Boards Agent** — after plan publish, board bridge creates work items; say `my work items` to continue
- **Dev Agent** — say `check my repos` after board items are created

## Settings

See `.env.example` — `MEETING_TRANSCRIPT_FOLDERS`, `MEETING_PLANS_FOLDER`, `MEETING_LIST_MAX`.

## Graph permissions

Phase 1: `Sites.Read.All`, `Sites.ReadWrite.All`, `Files.Read.All`, optional `Mail.Send` for Teams email.

Sample transcript: `samples/meeting-transcript/`.
