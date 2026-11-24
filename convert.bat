@echo off
rem Drag an Assetto Corsa track folder, or the zip it was downloaded as, onto
rem this file to convert it to GTR2. It installs into
rem GTR2\GameData\Locations. A staged copy is left in
rem out\<track>\gtr2\<track>. A zip is extracted into out\_zips.
rem
rem For a track with several layouts, type the layout when asked (for
rem example: gp).
setlocal
set "HERE=%~dp0"
if "%~1"=="" (
  echo Drag an Assetto Corsa track folder or zip onto convert.bat.
  pause
  exit /b 1
)
rem Run Python rather than just looking for it; the PATH entry may be a Store stub.
python -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10 or newer from python.org and
  echo tick "Add python.exe to PATH" in the installer, then try again.
  pause
  exit /b 1
)
set "TRACKDIR=%~1"
for %%Z in ("%TRACKDIR%") do set "TRACKEXT=%%~xZ"
shift
set "BUILD_ARGS="
:collect_args
if "%~1"=="" goto :args_collected
set "BUILD_ARGS=%BUILD_ARGS% %1"
shift
goto :collect_args
:args_collected
if /i not "%TRACKEXT%"==".zip" goto :folder
rem track_zip prints the extracted folder; its messages go to the screen.
set "ZIPPATH=%TRACKDIR%"
set "TRACKDIR="
for /f "usebackq delims=" %%L in (`python "%HERE%track_zip.py" "%ZIPPATH%"`) do set "TRACKDIR=%%L"
if not defined TRACKDIR (
  echo.
  echo Conversion FAILED. Read the messages above.
  pause
  exit /b 1
)
:folder
for %%T in ("%TRACKDIR%") do set "TRACK=%%~nxT"
set "LAYOUT="
if exist "%TRACKDIR%\models_*.ini" (
  echo This track has layouts:
  for %%G in ("%TRACKDIR%\models_*.ini") do echo    %%~nG
  set /p LAYOUT="Layout (the part after models_, Enter for the default): "
)
:run
echo.
echo Converting "%TRACK%" from scratch -- geometry will be rebuilt.
echo GTR2 export and conversion may take several minutes.
echo.
if defined LAYOUT (
  python "%HERE%build_track.py" "%TRACKDIR%" --install auto --layout "%LAYOUT%"%BUILD_ARGS%
) else (
  python "%HERE%build_track.py" "%TRACKDIR%" --install auto%BUILD_ARGS%
)
if errorlevel 1 (
  echo.
  echo Conversion FAILED. Read the messages above.
  pause
  exit /b 1
)
if defined LAYOUT set "TRACK=%TRACK%_%LAYOUT%"
echo.
echo Done. GTR2-ready files are in "%HERE%out\%TRACK%\gtr2\%TRACK%".
echo The track was also installed to the detected GTR2 location.
pause
