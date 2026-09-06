@echo off
cd /d %~dp0
if not exist backups mkdir backups
set TS=%date:~6,4%-%date:~3,2%-%date:~0,2%_%time:~0,2%-%time:~3,2%
set TS=%TS: =0%
copy normwear.db backups\normwear_%TS%.db >nul
echo Backup: backups\normwear_%TS%.db
dir backups | find "normwear"
pause
