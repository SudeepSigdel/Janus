# Janus overnight eval worker.
# Start once in the janus folder:  powershell -ExecutionPolicy Bypass -File scripts\eval_worker.ps1
# It watches results\queue\ for *.job files ({"kind": "...", "id": "..."}) and runs ONLY the fixed
# commands below -- job files cannot supply arbitrary commands or arguments. Output goes to
# results\overnight\<id>.{jsonl,log,status.json}. Keeps the PC awake while it runs (no settings
# changed; the request ends when this window closes). Stop it with Ctrl+C.

$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
$Queue = "results\queue"; $Out = "results\overnight"
New-Item -ItemType Directory -Force $Queue, $Out | Out-Null

Add-Type -Namespace Win32 -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[void][Win32.Power]::SetThreadExecutionState([uint32]"0x80000001")  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED

# The job list lives in scripts\eval_jobs.ps1 and is re-read before every job, so new job kinds
# work without restarting this window. It still only maps a kind to fixed commands.
. "$PSScriptRoot\eval_jobs.ps1"

Write-Host "Janus eval worker running in $(Get-Location). Leave this window open. Ctrl+C to stop."
while ($true) {
    Set-Content "$Queue\worker.heartbeat" (Get-Date -Format o)
    $job = Get-ChildItem "$Queue\*.job" -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -First 1
    if (-not $job) { Start-Sleep 15; continue }
    $spec = $null
    try { $spec = Get-Content $job.FullName -Raw | ConvertFrom-Json } catch {}
    Move-Item $job.FullName "$($job.FullName).taken" -Force
    $kind = "$($spec.kind)"; $id = "$($spec.id)"
    $status = @{ id = $id; kind = $kind; started = (Get-Date -Format o) }
    $cmds = $null
    . "$PSScriptRoot\eval_jobs.ps1"
    if ($id -match '^[a-z0-9][a-z0-9-]{0,60}$') { $cmds = @(JobCommands $kind $id) }
    if ($cmds.Count -eq 0) { $cmds = $null }
    if (-not $cmds) {
        $status.result = "rejected (unknown kind or bad id)"
    } else {
        $needsModel = $ModelKinds -contains $kind
        $ollamaOk = $true
        if ($needsModel) {
            try { Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 10 | Out-Null } catch { $ollamaOk = $false }
        }
        if (-not $ollamaOk) {
            $status.result = "ollama_down"
        } else {
            Set-Content "$Queue\running.json" ($status | ConvertTo-Json)
            Write-Host "$(Get-Date -Format t)  running $kind $id"
            $log = "$Out\$id.log"; Remove-Item $log -ErrorAction SilentlyContinue
            $codes = @()
            foreach ($line in @($cmds)) {
                $c = $line -split '\|'
                "`n>>> uv $($c -join ' ')" | Out-File $log -Append -Encoding utf8
                & uv @c *>&1 | Out-File $log -Append -Encoding utf8
                $codes += $LASTEXITCODE
            }
            $status.exit_codes = $codes
            $status.result = "finished"
            Remove-Item "$Queue\running.json" -ErrorAction SilentlyContinue
        }
    }
    $status.finished = (Get-Date -Format o)
    Set-Content "$Out\$id.status.json" ($status | ConvertTo-Json)
    Write-Host "$(Get-Date -Format t)  $id -> $($status.result)"
}
