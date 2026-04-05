# Simple Video Editor

(no description yet)

## Project Overview

Describe what this PowerShell project does and its primary goals.

## Key Files

- List important .ps1 files and their purposes here

## PowerShell Conventions

- UTF-8 BOM required on .ps1 files with non-ASCII characters
- CRLF line endings for Windows PowerShell compatibility
- Never use `$input` as a variable name — it is reserved by PowerShell
- Modulo preserves sign in PS5: `-1 % n = -1`, not `n-1`. Guard negative indices.
- Use `[PSCustomObject]@{...}` for objects; `@{...}` for hashtables
- `ConvertFrom-Json` returns PSCustomObject — use `Add-Member` to add properties

## Parameter Style

```powershell
function Do-Something {
    param(
        [string]$Name,
        [int]$Count = 1
    )
    # implementation
}
```

## Commands

```powershell
# Run the script
powershell -ExecutionPolicy Bypass -File .\main.ps1
```

