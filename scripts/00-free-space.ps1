# ORCA — reclaim space on C:
# Run in an ADMIN PowerShell.  Goal: at least 15 GB free on C:.
# Every line is safe: caches, temp files and package archives that rebuild on demand.

Write-Host "Free on C: BEFORE = $([math]::Round((Get-PSDrive C).Free/1GB,2)) GB" -ForegroundColor Cyan

$targets = @(
  # --- failed/partial package downloads (this is what broke first) ---
  "$env:APPDATA\uv\python\.temp",
  "$env:LOCALAPPDATA\uv\cache",
  "$env:LOCALAPPDATA\pip\cache",
  "$env:LOCALAPPDATA\npm-cache\_cacache",
  "$env:LOCALAPPDATA\Yarn\Cache",
  # --- user + system temp ---
  "$env:TEMP",
  "C:\Windows\Temp",
  # --- browser / editor caches that regenerate ---
  "$env:LOCALAPPDATA\Microsoft\Edge\User Data\Default\Cache",
  "$env:APPDATA\Code\Cache",
  "$env:APPDATA\Code\CachedData",
  "$env:APPDATA\Code\CachedExtensionVSIXs",
  # --- conda / nuget ---
  "$env:USERPROFILE\anaconda3\pkgs",
  "$env:USERPROFILE\.nuget\packages",
  "$env:USERPROFILE\.cache"
)

foreach ($t in $targets) {
  if (Test-Path $t) {
    $before = (Get-ChildItem $t -Recurse -File -Force -EA SilentlyContinue | Measure-Object Length -Sum).Sum
    Get-ChildItem $t -Force -EA SilentlyContinue | Remove-Item -Recurse -Force -EA SilentlyContinue
    Write-Host ("  cleared {0,-72} {1,8:N2} GB" -f $t, ($before/1GB))
  }
}

# Windows Update leftovers + component store (the usual multi-GB win)
Write-Host "`nRunning DISM component cleanup (this takes a few minutes)..." -ForegroundColor Yellow
Dism.exe /Online /Cleanup-Image /StartComponentCleanup /Quiet

# Recycle bin
Clear-RecycleBin -Force -EA SilentlyContinue

Write-Host "`nFree on C: AFTER  = $([math]::Round((Get-PSDrive C).Free/1GB,2)) GB" -ForegroundColor Green
Write-Host @"

If you are still under 15 GB, the biggest remaining wins are usually:
  1. Move the WSL / Docker Desktop disk to D:
       wsl --shutdown
       (Docker Desktop > Settings > Resources > Disk image location -> D:\docker)
  2. Hibernation file (frees ~= your RAM size):
       powercfg /h off
  3. Find what is actually large:
       Get-ChildItem C:\ -Directory -Force | ForEach-Object {
         `$s=(Get-ChildItem `$_.FullName -Recurse -File -Force -EA SilentlyContinue |
              Measure-Object Length -Sum).Sum
         [pscustomobject]@{Dir=`$_.Name; GB=[math]::Round(`$s/1GB,2)}
       } | Sort-Object GB -Descending | Select-Object -First 15

"@ -ForegroundColor DarkGray
