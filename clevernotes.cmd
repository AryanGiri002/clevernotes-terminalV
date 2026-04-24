@echo off
setlocal

REM clevernotes launcher (Windows / Docker).
REM
REM Bind-mounts:
REM   %CD%                    -> /work   (user's lecture folder; NOTES/ lands back here)
REM   %APPDATA%\clevernotes   -> /root/.config/clevernotes   (reads config.env)
REM
REM Override the image tag for local dev: set CLEVERNOTES_IMAGE=...

if "%CLEVERNOTES_IMAGE%"=="" set "CLEVERNOTES_IMAGE=002giriaryan/clevernotes:latest"

where docker >nul 2>nul
if errorlevel 1 (
    echo clevernotes: docker command not found. 1>&2
    echo Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and re-run. 1>&2
    exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
    echo clevernotes: Docker daemon is not reachable. 1>&2
    echo Start Docker Desktop (wait for the whale icon to stop animating) and try again. 1>&2
    exit /b 1
)

if not exist "%APPDATA%\clevernotes\config.env" (
    echo clevernotes: no config found at %APPDATA%\clevernotes\config.env 1>&2
    echo Run install.ps1 first to set up your API keys. 1>&2
    exit /b 1
)

docker run --rm -it ^
    -v "%CD%:/work" ^
    -v "%APPDATA%\clevernotes:/root/.config/clevernotes" ^
    -w /work ^
    "%CLEVERNOTES_IMAGE%" %*

endlocal
