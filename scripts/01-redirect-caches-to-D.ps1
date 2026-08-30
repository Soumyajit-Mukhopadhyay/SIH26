# ORCA — keep C: from filling up again.
# Points every package-manager cache at D:.  Run once, in a NORMAL PowerShell.
# Reopen your terminal (and VS Code) afterwards so the new variables are picked up.

$root = 'D:\orca-cache'
foreach ($d in 'uv','pip','npm','hf','torch','conda') {
  New-Item -ItemType Directory -Force -Path "$root\$d" | Out-Null
}

# uv + pip + HuggingFace + torch  (user-level env vars, persist across sessions)
[Environment]::SetEnvironmentVariable('UV_CACHE_DIR',   "$root\uv",    'User')
[Environment]::SetEnvironmentVariable('PIP_CACHE_DIR',  "$root\pip",   'User')
[Environment]::SetEnvironmentVariable('HF_HOME',        "$root\hf",    'User')
[Environment]::SetEnvironmentVariable('TORCH_HOME',     "$root\torch", 'User')
[Environment]::SetEnvironmentVariable('UV_PYTHON_INSTALL_DIR', 'D:\orca-python', 'User')

# npm cache
npm config set cache "$root\npm" --global

# conda (only if conda is on PATH)
if (Get-Command conda -EA SilentlyContinue) {
  conda config --add pkgs_dirs "$root\conda"
}

Write-Host "`nCaches now live under $root :" -ForegroundColor Green
[Environment]::GetEnvironmentVariables('User').GetEnumerator() |
  Where-Object { $_.Name -match 'UV_|PIP_|HF_HOME|TORCH_HOME' } |
  Sort-Object Name | Format-Table -AutoSize

Write-Host "npm cache -> $(npm config get cache)" -ForegroundColor Green
Write-Host "`nClose and reopen your terminal / VS Code before installing anything." -ForegroundColor Yellow
