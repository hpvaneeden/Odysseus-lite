@echo off

wt -w 0 new-tab --title "Ollama" -d "C:\Users\hpvan" pwsh -NoExit -Command "& 'C:\Users\hpvan\AppData\Local\Programs\Ollama\ollama.exe' serve" ^
; new-tab --title "SearXNG" -d "C:\Users\hpvan\Apps\searxng-windows\scripts" pwsh -NoExit -Command "& '.\run.ps1'" ^
; new-tab --title "Odysseus" -d "C:\Users\hpvan\odysseus-main" pwsh -NoExit -Command "& '.\launch-windows.ps1' -BindHost 0.0.0.0"