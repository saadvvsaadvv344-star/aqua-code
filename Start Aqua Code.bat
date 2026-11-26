@echo off
cd /d "%~dp0"
where pythonw >nul 2>&1 && (start "" pythonw aqua_app.py & exit /b)
where pyw >nul 2>&1 && (start "" pyw aqua_app.py & exit /b)
where python >nul 2>&1 && (start "" python aqua_app.py & exit /b)
echo Aqua Code needs Python 3.9 or newer. Install it with:  winget install Python.Python.3.12
pause
