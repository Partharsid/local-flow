@echo off
setlocal
rem ============================================================
rem  Train LocalFlow's own dictation model (Qwen3-1.7B, LoRA)
rem  Double-click to run. Takes about 40-50 minutes on an RTX 4060.
rem  If it gets interrupted, run it again: it resumes from the last
rem  save (every 20 steps). Keep the laptop plugged in.
rem ============================================================
cd /d "%~dp0"
title LocalFlow model training
set PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
set PY=%~dp0.venv\Scripts\python.exe
set APPPY=%~dp0..\.venv\Scripts\python.exe
set QUANT=%APPDATA%\LocalFlow\runtime\llama\llama-quantize.exe
set OUT=runs\q17

if not exist "%PY%" (
  echo Training environment not found: %PY%
  pause & exit /b 1
)

echo.
echo [1/4] Training (resumes automatically if a save exists)...
"%PY%" train.py --base models\Qwen3-1.7B --out %OUT% --epochs 1.5 --tokens 2048 --accum 8
if errorlevel 1 goto failed

echo.
echo [2/4] Converting to GGUF...
"%PY%" convert_gguf.py %OUT%\merged %OUT%\f16.gguf
if errorlevel 1 goto failed

echo.
echo [3/4] Quantizing (Q5_K_M and Q8_0)...
"%QUANT%" %OUT%\f16.gguf %OUT%\localflow-dictation-1.7b-Q5_K_M.gguf Q5_K_M
if errorlevel 1 goto failed
"%QUANT%" %OUT%\f16.gguf %OUT%\localflow-dictation-1.7b-Q8_0.gguf Q8_0
if errorlevel 1 goto failed

echo.
echo [4/4] Testing against the held-out examples...
"%APPPY%" evaluate.py --gguf %OUT%\localflow-dictation-1.7b-Q5_K_M.gguf --name q17-Q5 > runs\score-q17-Q5.txt
"%APPPY%" evaluate.py --gguf %OUT%\localflow-dictation-1.7b-Q8_0.gguf --name q17-Q8 > runs\score-q17-Q8.txt
echo.
echo ===== Current model (Qwen3-4B) =====
echo ALL  n=324  sim 0.885  ms 616   (measured earlier)
echo.
echo ===== New model, Q5_K_M =====
type runs\score-q17-Q5.txt
echo.
echo ===== New model, Q8_0 =====
type runs\score-q17-Q8.txt
echo.
echo Done. Results are in training\runs\. Tell Claude "training finished" to compare and install.
pause
exit /b 0

:failed
echo.
echo Something failed (see the messages above). Run this file again to resume.
pause
exit /b 1
