@echo off
set "GIT_SSH_COMMAND=ssh -o BatchMode=yes"
if not exist "D:\Github Repositories" mkdir "D:\Github Repositories"
if exist "D:\Github Repositories\Trading-Bot\.git" (
    echo Repo already exists, pulling...
    cd /d "D:\Github Repositories\Trading-Bot"
    git fetch origin
    git checkout feat/fase-3-risk-execution-guardian
    git pull origin feat/fase-3-risk-execution-guardian
) else (
    echo Cloning fresh repository...
    if exist "D:\Github Repositories\Trading-Bot" rmdir /s /q "D:\Github Repositories\Trading-Bot"
    git clone git@github.com:JoaquinSasso/Trading-Bot.git "D:\Github Repositories\Trading-Bot"
    cd /d "D:\Github Repositories\Trading-Bot"
    git checkout feat/fase-3-risk-execution-guardian
)
echo Done!
