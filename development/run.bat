@echo off
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "INFRA_DIR=%SCRIPT_DIR%\.."
set "ENV_FILE=%SCRIPT_DIR%\.env"
set "EXAMPLE_FILE=%SCRIPT_DIR%\.env.example"
set "COMPOSE_FILE=%SCRIPT_DIR%\compose.yaml"

if "%~1"=="" goto :usage
if "%~1"=="--help" goto :usage
if "%~1"=="-h" goto :usage

set "ACTION=%~1"
shift

set "BUILD=0"
set "SERVICE="
set "MODE=docker"

:parse_args
if "%~1"=="" goto :args_parsed
if "%~1"=="--help" goto :usage
if "%~1"=="-h" goto :usage
if "%~1"=="--build" goto :parse_build
if "%~1"=="--mode" goto :parse_mode
if "%~1"=="--service" goto :parse_service
set "FIRST_CHAR=%~1"
set "FIRST_CHAR=!FIRST_CHAR:~0,1!"
if "!FIRST_CHAR!"=="-" (
    echo Unknown option: %~1 >&2
    goto :usage
)
if not "%SERVICE%"=="" (
    echo Only one service can be selected >&2
    exit /b 2
)
set "SERVICE=%~1"
shift
goto :parse_args

REM NOTE: shift must NOT be followed by %~1 inside the same parenthesized
REM block. cmd expands %~1 for the whole block before it runs, so the value
REM would be read from before the shift. These handlers therefore live in
REM their own labels, where each line is expanded only when it executes.

:parse_build
set "BUILD=1"
shift
goto :parse_args

:parse_mode
shift
if "%~1"=="" (
    echo Missing value for --mode ^(docker or host^) >&2
    exit /b 2
)
set "MODE=%~1"
shift
goto :parse_args

:parse_service
shift
if "%~1"=="" (
    echo Missing value for --service >&2
    exit /b 2
)
set "SERVICE=%~1"
shift
goto :parse_args

:args_parsed
if not "%MODE%"=="docker" if not "%MODE%"=="host" (
    echo Unknown mode: %MODE% ^(choose docker or host^) >&2
    exit /b 2
)

if not "%SERVICE%"=="" (
    set "VALID_SVC=0"
    for %%s in (rabbitmq identity-service delivery-service merchant-service notification-worker gateway web) do (
        if "%SERVICE%"=="%%s" set "VALID_SVC=1"
    )
    if "!VALID_SVC!"=="0" (
        echo Unknown service: %SERVICE% >&2
        goto :usage
    )
)

if "%BUILD%"=="1" if not "%ACTION%"=="up" (
    echo --build is only valid with up >&2
    exit /b 2
)
if "%BUILD%"=="1" if not "%MODE%"=="docker" (
    echo --build is for Docker mode; host mode uses installed SDK tooling >&2
    exit /b 2
)

if "%MODE%"=="host" if not "%ACTION%"=="migrate" (
    if not "%ACTION%"=="up" (
        echo Host mode supports full-stack "up" and explicit "migrate"; stop apps with Ctrl-C. >&2
        exit /b 2
    )
    if not "%SERVICE%"=="" (
        echo Host mode supports full-stack "up" and explicit "migrate"; stop apps with Ctrl-C. >&2
        exit /b 2
    )
)

set "VALID_ACT=0"
for %%a in (up build down ps logs config migrate) do (
    if "%ACTION%"=="%%a" set "VALID_ACT=1"
)
if "!VALID_ACT!"=="0" (
    echo Unknown command: %ACTION% >&2
    goto :usage
)

if "%ACTION%"=="migrate" (
    if not "%SERVICE%"=="identity-service" if not "%SERVICE%"=="delivery-service" (
        echo migrate requires identity-service or delivery-service >&2
        exit /b 2
    )
)

call :initialize_env
if errorlevel 1 exit /b %errorlevel%

if "%MODE%"=="host" (
    if "%ACTION%"=="migrate" (
        call :run_host_migration
        exit /b !errorlevel!
    ) else (
        call :start_host_stack
        exit /b !errorlevel!
    )
)

pushd "%INFRA_DIR%"

if "%ACTION%"=="migrate" (
    set "MIGRATION_TASK="
    if "%SERVICE%"=="identity-service" set "MIGRATION_TASK=identity-migrate"
    if "%SERVICE%"=="delivery-service" set "MIGRATION_TASK=delivery-migrate"
    docker compose --env-file "%ENV_FILE%" -f "%COMPOSE_FILE%" --profile database-migrations run --rm --no-deps !MIGRATION_TASK!
    set "EXIT_CODE=!errorlevel!"
    popd
    exit /b !EXIT_CODE!
)

if "%ACTION%"=="up" (
    if "%BUILD%"=="1" (
        if not "%SERVICE%"=="" (
            docker compose --env-file "%ENV_FILE%" -f "%COMPOSE_FILE%" up -d --build %SERVICE%
        ) else (
            docker compose --env-file "%ENV_FILE%" -f "%COMPOSE_FILE%" up -d --build
        )
    ) else (
        if not "%SERVICE%"=="" (
            docker compose --env-file "%ENV_FILE%" -f "%COMPOSE_FILE%" up -d %SERVICE%
        ) else (
            docker compose --env-file "%ENV_FILE%" -f "%COMPOSE_FILE%" up -d
        )
    )
    set "EXIT_CODE=!errorlevel!"
    popd
    exit /b !EXIT_CODE!
)

if "%ACTION%"=="build" (
    if not "%SERVICE%"=="" (
        docker compose --env-file "%ENV_FILE%" -f "%COMPOSE_FILE%" build %SERVICE%
    ) else (
        docker compose --env-file "%ENV_FILE%" -f "%COMPOSE_FILE%" build
    )
    set "EXIT_CODE=!errorlevel!"
    popd
    exit /b !EXIT_CODE!
)

if "%ACTION%"=="down" (
    if not "%SERVICE%"=="" (
        echo down stops the whole development stack; do not pass a service >&2
        popd
        exit /b 2
    )
    docker compose --env-file "%ENV_FILE%" -f "%COMPOSE_FILE%" down
    set "EXIT_CODE=!errorlevel!"
    popd
    exit /b !EXIT_CODE!
)

if "%ACTION%"=="ps" (
    if not "%SERVICE%"=="" (
        echo ps lists the whole development stack; do not pass a service >&2
        popd
        exit /b 2
    )
    docker compose --env-file "%ENV_FILE%" -f "%COMPOSE_FILE%" ps
    set "EXIT_CODE=!errorlevel!"
    popd
    exit /b !EXIT_CODE!
)

if "%ACTION%"=="logs" (
    if not "%SERVICE%"=="" (
        docker compose --env-file "%ENV_FILE%" -f "%COMPOSE_FILE%" logs --follow --tail=100 %SERVICE%
    ) else (
        docker compose --env-file "%ENV_FILE%" -f "%COMPOSE_FILE%" logs --follow --tail=100
    )
    set "EXIT_CODE=!errorlevel!"
    popd
    exit /b !EXIT_CODE!
)

if "%ACTION%"=="config" (
    if not "%SERVICE%"=="" (
        echo config validates the whole development stack; do not pass a service >&2
        popd
        exit /b 2
    )
    docker compose --env-file "%ENV_FILE%" -f "%COMPOSE_FILE%" config --quiet
    set "EXIT_CODE=!errorlevel!"
    popd
    exit /b !EXIT_CODE!
)

popd
exit /b 0

:usage
echo Usage:
echo   development\run.bat up [--mode docker^|host] [--build] [service]
echo   development\run.bat migrate [--mode docker^|host] identity-service^|delivery-service
echo   development\run.bat build [service]              # Docker image build
echo   development\run.bat down^|ps^|logs^|config          # Docker Compose
echo.
echo Services: rabbitmq, identity-service, delivery-service, merchant-service,
echo           notification-worker, gateway, web
echo.
echo Docker is the default mode. Host mode starts backend and frontend processes
echo directly; stop with Ctrl-C. Docker build/start is required before PR merge.
echo Migrations are explicit one-shot tasks; up never applies database migrations.
exit /b 2

:initialize_env
if not exist "%ENV_FILE%" (
    copy "%EXAMPLE_FILE%" "%ENV_FILE%" >nul
)

REM A password exported in the environment always wins.
if defined RABBITMQ_DEFAULT_PASS exit /b 0

REM If the file still holds the GENERATE_LOCALLY placeholder, generate one.
findstr /R /C:"^RABBITMQ_DEFAULT_PASS=GENERATE_LOCALLY" "%ENV_FILE%" >nul
if not errorlevel 1 goto :generate_rabbit_pass

REM Otherwise keep any existing non-empty value (so the password stays stable
REM across runs and keeps matching the RabbitMQ data volume).
REM findstr regex has no "+" operator, so "..*" is used for "one or more chars".
findstr /R /C:"^RABBITMQ_DEFAULT_PASS=..*" "%ENV_FILE%" >nul
if not errorlevel 1 goto :check_signing_key

:generate_rabbit_pass
set "RABBIT_PASS="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "$b = New-Object byte[] 24; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { '{0:x2}' -f $_ })"`) do set "RABBIT_PASS=%%p"

if not defined RABBIT_PASS (
    echo PowerShell is required to generate a local RabbitMQ password >&2
    exit /b 1
)

REM ASCII keeps the file free of a BOM so Docker reads the first key correctly.
powershell -NoProfile -Command "$c = Get-Content -LiteralPath '%ENV_FILE%'; if ($c -match '^RABBITMQ_DEFAULT_PASS=') { $c = $c -replace '^RABBITMQ_DEFAULT_PASS=.*$', ('RABBITMQ_DEFAULT_PASS=' + '%RABBIT_PASS%') } else { $c = @($c) + ('RABBITMQ_DEFAULT_PASS=' + '%RABBIT_PASS%') }; Set-Content -LiteralPath '%ENV_FILE%' -Value $c -Encoding ASCII"
if errorlevel 1 (
    echo Failed to write the generated RabbitMQ password to %ENV_FILE% >&2
    exit /b 1
)
echo Generated a local RabbitMQ password in %ENV_FILE%

:check_signing_key
if not exist "%TEMP%\swiftdrop\identity" (
    mkdir "%TEMP%\swiftdrop\identity" 2>nul
)
exit /b 0

:run_host_migration
REM Connection strings may contain "!" so delayed expansion is turned off here.
REM The setlocal is undone automatically when this subroutine returns.
setlocal DisableDelayedExpansion
set "BACKEND_DIR=%INFRA_DIR%\..\swiftdrop-backend"
set "MIG_CONN="
for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    if "%SERVICE%"=="identity-service" if "%%A"=="IDENTITY_MIGRATION_CONNECTION_STRING" set "MIG_CONN=%%B"
    if "%SERVICE%"=="delivery-service" if "%%A"=="DELIVERY_DATABASE_CONNECTION_STRING" set "MIG_CONN=%%B"
)
if "%SERVICE%"=="identity-service" (
    set "MIG_SETTING=ConnectionStrings__MigrationDatabase"
    set "MIG_PROJECT=src\Services\Identity\SwiftDrop.Identity.Api"
) else (
    set "MIG_SETTING=ConnectionStrings__Database"
    set "MIG_PROJECT=src\Services\Delivery\SwiftDrop.Delivery.Api"
)
if "%MIG_CONN%"=="" (
    echo Configure the owning migration connection before migrating %SERVICE% >&2
    exit /b 1
)
pushd "%BACKEND_DIR%"
set "ASPNETCORE_ENVIRONMENT=Development"
set "DOTNET_ENVIRONMENT=Development"
set "%MIG_SETTING%=%MIG_CONN%"
echo Applying %SERVICE% database migrations...
dotnet run --no-launch-profile --project "%MIG_PROJECT%" -- --migrate
set "EXIT_CODE=%errorlevel%"
popd
exit /b %EXIT_CODE%

:start_host_stack
set "BACKEND_DIR=%INFRA_DIR%\..\swiftdrop-backend"
set "FRONTEND_DIR=%INFRA_DIR%\..\swiftdrop-frontend"
where dotnet >nul 2>&1 || (echo Host mode requires the .NET SDK pinned by backend\global.json >&2 & exit /b 1)
where npm >nul 2>&1 || (echo Host mode requires Node.js and npm pinned by frontend\.nvmrc >&2 & exit /b 1)
where curl >nul 2>&1 || (echo Host mode requires curl to check service health endpoints >&2 & exit /b 1)

echo Starting SwiftDrop services in host mode...
set "ASPNETCORE_ENVIRONMENT=Development"
set "DOTNET_ENVIRONMENT=Development"

start "SwiftDrop Identity Service" cmd /k "cd /d %BACKEND_DIR% && dotnet run --no-launch-profile --project src\Services\Identity\SwiftDrop.Identity.Api"
start "SwiftDrop Delivery Service" cmd /k "cd /d %BACKEND_DIR% && dotnet run --no-launch-profile --project src\Services\Delivery\SwiftDrop.Delivery.Api"
start "SwiftDrop Merchant Service" cmd /k "cd /d %BACKEND_DIR% && dotnet run --no-launch-profile --project src\Services\Merchant\SwiftDrop.Merchant.Api"
start "SwiftDrop Notification Worker" cmd /k "cd /d %BACKEND_DIR% && dotnet run --no-launch-profile --project src\Workers\SwiftDrop.Notification.Worker"
start "SwiftDrop Gateway" cmd /k "cd /d %BACKEND_DIR% && dotnet run --no-launch-profile --project src\Gateway\SwiftDrop.Gateway"
start "SwiftDrop Web" cmd /k "cd /d %FRONTEND_DIR% && npm run dev"

echo All host services have been started in separate windows.
echo Web UI: http://localhost:5173
echo Gateway: http://localhost:5000
exit /b 0