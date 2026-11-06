# iPhone-PC Drop - installer. Lives in scripts; run it via Install.cmd in the folder above (it asks for admin rights).
# Safe to re-run any time (after updating iphone_drop.py, Python or Tailscale):
# keeps token.txt, replaces the firewall rule and startup task, restarts the receiver.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)   # the project folder, one level up from scripts\
$file = Join-Path $here "iphone_drop.py"
$tokFile = Join-Path $here "token.txt"

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

    Write-Host "[2/6] Installing packages (flask waitress pyperclip win11toast pillow pillow-heif qrcode)..."
    & $py -m pip install --upgrade --quiet --disable-pip-version-check flask waitress pyperclip win11toast pillow pillow-heif qrcode
    if ($LASTEXITCODE -ne 0) { throw "pip install failed (see messages above)." }

    Write-Host "[3/6] Token..."
    $token = ""
    if (Test-Path $tokFile) { $token = ([IO.File]::ReadAllText($tokFile)).Trim([char]0xFEFF, " ", "`r", "`n", "`t") }
    if (-not $token) {
        $token = (& $py -c "import secrets; a='abcdefghjkmnpqrstuvwxyz23456789'; t=''.join(secrets.choice(a) for _ in range(8)); print(t[:4]+'-'+t[4:])").Trim()
        [IO.File]::WriteAllText($tokFile, $token, (New-Object Text.ASCIIEncoding))
        Write-Host "      created"
    } else { Write-Host "      kept (the Shortcut keeps working)" }

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
    if (-not $ip) {
        Write-Host ""
        Write-Host "Tailscale address not found. Install Tailscale, sign in, then run Install.cmd again." -ForegroundColor Red
        return
    }
    $address = "http://$($ip):8765"

    Write-Host ""
    Write-Host "Done. The Shortcut will ask for these two values." -ForegroundColor Green
    Write-Host "Scan a QR code with the iPhone camera to copy it, then paste it in." -ForegroundColor Green
    & $py (Join-Path $here "scripts\show_qr.py") $address $token
}
catch {
    Write-Host ""
    Write-Host "INSTALL FAILED: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Write-Host ""
    Read-Host "Press Enter to close"
}
