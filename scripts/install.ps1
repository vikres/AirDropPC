# iPhone-PC Drop - installer. Lives in scripts; run it via Install.cmd in the folder above (it asks for admin rights).
# Safe to re-run any time (after updating iphone_drop.py, Python or Tailscale):
# keeps token.txt, replaces the firewall rule and startup task, restarts the receiver.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)   # the project folder, one level up from scripts\
$file = Join-Path $here "iphone_drop.py"
$tokFile = Join-Path $here "token.txt"
$valuesFile = Join-Path $here "shortcut-values.txt"

try {
    if (-not (Test-Path $file)) { throw "iphone_drop.py not found next to install.ps1." }
    if ($here -like "*\Temp*") { throw "Move this folder somewhere permanent first (it is in a Temp folder)." }

    # Python (real path, not the Microsoft Store alias)
    $py = ""
    try { $py = (& python -c "import sys; print(sys.executable)" 2>$null).Trim() } catch {}
    if (-not $py -or -not (Test-Path $py)) { throw "Python not found. Install it from python.org (tick 'Add python.exe to PATH') and run Install.cmd again." }
    $ver = (& $py -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
    if ([version]$ver -lt [version]"3.9") { throw "Python $ver is too old; need 3.9 or newer." }
    $pyw = Join-Path (Split-Path $py) "pythonw.exe"
    if (-not (Test-Path $pyw)) { throw "pythonw.exe not found next to $py" }
    Write-Host "[1/6] Python $ver found: $py"

    Write-Host "[2/6] Installing packages (flask waitress pyperclip win11toast pillow pillow-heif)..."
    & $py -m pip install --upgrade --quiet --disable-pip-version-check flask waitress pyperclip win11toast pillow pillow-heif
    if ($LASTEXITCODE -ne 0) { throw "pip install failed (see messages above)." }

    Write-Host "[3/6] Token..."
    $token = ""
    if (Test-Path $tokFile) { $token = ([IO.File]::ReadAllText($tokFile)).Trim([char]0xFEFF, " ", "`r", "`n", "`t") }
    if (-not $token) {
        $token = (& $py -c "import secrets; print(secrets.token_urlsafe(32))").Trim()
        [IO.File]::WriteAllText($tokFile, $token, (New-Object Text.ASCIIEncoding))
        Write-Host "      new token created in token.txt"
    } else { Write-Host "      existing token.txt kept" }

    Write-Host "[4/6] Firewall rule (port 8765, Tailscale addresses only)..."
    Get-NetFirewallRule -DisplayName "iPhone drop (8765)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName "iPhone drop (8765)" -Direction Inbound -Protocol TCP `
        -LocalPort 8765 -RemoteAddress 100.64.0.0/10 -Action Allow -Profile Any | Out-Null

    Write-Host "[5/6] Startup task (at logon, on battery too, auto-restart)..."
    $action   = New-ScheduledTaskAction -Execute $pyw -Argument "`"$file`"" -WorkingDirectory $here
    $trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "iPhone drop" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Stop-ScheduledTask -TaskName "iPhone drop" -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*iphone_drop.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
    Start-ScheduledTask -TaskName "iPhone drop"

    Write-Host "[6/6] Checking it runs (up to 40 s)..."
    $listening = $null
    for ($i = 0; $i -lt 40 -and -not $listening; $i++) {
        Start-Sleep -Seconds 1
        $listening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    }
    if ($listening) { Write-Host "      Receiver is running." -ForegroundColor Green }
    else {
        Write-Host "      Receiver is NOT running. Run:  python `"$file`"  in this folder to see the error." -ForegroundColor Red
    }

    $ip = ""
    $ts = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
    if (-not $ts -and (Test-Path "C:\Program Files\Tailscale\tailscale.exe")) { $ts = "C:\Program Files\Tailscale\tailscale.exe" }
    if ($ts) { try { $ip = ((& $ts ip -4 2>$null) | Select-Object -First 1).Trim() } catch {} }
    if (-not $ip) { $ip = "<not found - install/sign in to Tailscale, then run Install.cmd again>" }

    $values = @"
Values for the iPhone Shortcut (see README.md). Keep this private.

PC address : http://$($ip):8765
Token      : $token
"@
    [IO.File]::WriteAllText($valuesFile, $values, (New-Object Text.UTF8Encoding $false))
    Write-Host ""
    Write-Host $values
    Write-Host ""
    Write-Host "Saved in shortcut-values.txt" -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host "INSTALL FAILED: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Write-Host ""
    Read-Host "Press Enter to close"
}
