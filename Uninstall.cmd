@echo off
rem Removes the startup task and firewall rule. Keeps this folder and your files.
powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%~dp0scripts\uninstall.ps1\"'"
