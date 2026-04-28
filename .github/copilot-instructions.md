## Development

```powershell
# Ctrl+Shift+B → "Start Dev (VS Code Terminals)"
# Or: azd up
```

## Hooks

| Hook | Event | What It Does |
|------|-------|-------------|
| **Commit Gate** | `preToolUse` | Blocks direct `git commit`. Follow `committing-code` skill → commit via `-F COMMIT_MESSAGE.md`. |
| **Test Reminder** | `preToolUse` | Advisory: reminds to run tests if test files exist for staged changes. |
| **Doc Sync** | `postToolUse` | Reminds to update `ARCHITECTURE-FLOW.md` when architecture-sensitive files are edited. |
