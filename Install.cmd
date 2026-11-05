@echo off
rem Installs or updates iPhone drop. Asks for admin rights (firewall rule + startup task).
powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%~dp0scripts\install.ps1\"'"
