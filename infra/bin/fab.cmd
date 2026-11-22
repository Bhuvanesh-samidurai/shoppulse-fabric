@echo off
rem Runs the Fabric CLI from the repo's .venv as a Python module.
rem Smart App Control blocks the pip-generated fab.exe launcher; this wrapper avoids it.
"%~dp0..\..\.venv\Scripts\python.exe" -m fabric_cli.main %*
