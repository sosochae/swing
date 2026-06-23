@echo off
setlocal EnableDelayedExpansion
for /f "skip=1 tokens=1" %%D in ('wmic os get LocalDateTime') do (
    set DT=%%D
    set TODAY=!DT:~0,4!-!DT:~4,2!-!DT:~6,2!
    goto :gotdate
)
:gotdate
cd /d C:\MCP\Swing
call .venv\Scripts\activate

python scripts/run_sell_pipeline.py --real

set "NOTE_PATH=C:\lian\swing-procedure\notes\sell\%TODAY%.md"
start "" "obsidian://open?path=%NOTE_PATH%"

timeout /t 2 /nobreak >nul
powershell -NoProfile -Command "
Add-Type -AssemblyName Microsoft.VisualBasic;
Add-Type -AssemblyName System.Windows.Forms;
$proc = Get-Process -Name 'Obsidian' -ErrorAction SilentlyContinue | Select-Object -First 1;
if ($proc) {
    [Microsoft.VisualBasic.Interaction]::AppActivate($proc.Id);
    Start-Sleep -Milliseconds 400;
    [System.Windows.Forms.SendKeys]::SendWait('^t');
    Start-Sleep -Milliseconds 400;
    [System.Windows.Forms.SendKeys]::SendWait('^+s');
}
"
