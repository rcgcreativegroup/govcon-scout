# MyBidMatch Browser Leads Report

**Generated:** 2026-05-21 23:40:14

## Error

No date entries could be extracted from the directory page.

**Possible causes:**
- URL is wrong or has expired
- Page structure has changed
- Network/timeout issue

**Errors:**
- 403 Forbidden — OutreachSystems requires an active session cookie to access the MyBidMatch directory. Save your session first:
  python src/save_mybidmatch_login.py
Then re-run with: --storage-state mybidmatch_auth.json
