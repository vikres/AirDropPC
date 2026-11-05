# iPhone-PC Drop - uninstaller. Lives in scripts; run it via Uninstall.cmd in the folder above (it asks for admin rights).
# Stops the receiver and removes the startup task and firewall rule.
# Leaves this folder, token.txt, your files in Downloads and the text history untouched.

$ErrorActionPreference = "Continue"
Stop-ScheduledTask -TaskName "iPhone drop" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "iPhone drop" -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*iphone_drop.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-NetFirewallRule -DisplayName "iPhone drop (8765)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host "Removed: startup task, firewall rule, running receiver." -ForegroundColor Green
Write-Host "Kept: this folder, token.txt, your files. Delete the folder yourself if you want it gone."
Read-Host "Press Enter to close"
