@echo off
rem Windows launcher for the fake claude. cmd.exe cuts a multi-line argument at its
rem first newline, so tests use the claude.exe that tests/conftest.py builds instead;
rem this shim serves single-line calls such as `claude --version`.
if "%FAKE_CLAUDE_PYTHON%"=="" (python "%~dp0fake_claude.py" %*) else ("%FAKE_CLAUDE_PYTHON%" "%~dp0fake_claude.py" %*)
