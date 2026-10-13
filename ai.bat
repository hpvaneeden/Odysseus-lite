@echo off

wt -w 0 new-tab --title "Ollama" -d "C:\Users\hpvan" pwsh -NoExit -Command "& 'C:\Users\hpvan\AppData\Local\Programs\Ollama\ollama.exe' serve" ^
; new-tab --title "SearXNG" -d "C:\Users\hpvan\Apps\searxng-windows\scripts" pwsh -NoExit -Command "& '.\run.ps1'" ^
; new-tab --title "ChromaDB" -d "C:\Users\hpvan\odysseus-main" pwsh -NoExit -Command "& '.\chroma-venv\Scripts\chroma.exe' run --host 127.0.0.1 --port 8100" ^
; new-tab --title "Odysseus" -d "C:\Users\hpvan\odysseus-main" pwsh -NoExit -Command "$env:PATH += '\;C:\Users\hpvan\odysseus-main\venv\Lib\site-packages\nvidia\cublas\bin'\; .\launch-windows.ps1 -BindHost 0.0.0.0"
