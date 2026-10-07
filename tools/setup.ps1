<#
.SYNOPSIS
    Установка и проверка Deploy NLC на компьютере с Windows.

.DESCRIPTION
    Проверяет внешние программы (Python, Node.js, Appium, драйвер
    uiautomator2, Java, Android SDK), создаёт окружение venv, ставит
    зависимости и Chromium для Playwright, создаёт .env из .env.example,
    прогоняет тесты и стартовые проверки бота.

    Скрипт можно запускать повторно: уже сделанные шаги пропускаются.
    Внешние программы он не устанавливает, а только проверяет и говорит,
    чего не хватает (см. docs/INSTALL.md).

.PARAMETER CheckOnly
    Только проверить окружение, ничего не устанавливать и не менять.

.PARAMETER ConfigureTablet
    Применить к подключённому планшету рабочие настройки: отключить
    анимации, закрепить портретную ориентацию, установить и включить
    ADBKeyBoard, запретить уведомления HeyDealer.

.PARAMETER SkipTests
    Не запускать тесты.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\setup.ps1
    powershell -ExecutionPolicy Bypass -File tools\setup.ps1 -ConfigureTablet
    powershell -ExecutionPolicy Bypass -File tools\setup.ps1 -CheckOnly
#>
param(
    [switch]$CheckOnly,
    [switch]$ConfigureTablet,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Версии, на которых система работает в эксплуатации.
$Expected = @{
    Python      = '3.12'
    Appium      = '3.6.0'
    Uiautomator = '8.4.0'
}

$results = New-Object System.Collections.Generic.List[object]

function Add-Result([string]$Name, [string]$Status, [string]$Detail) {
    $results.Add([pscustomobject]@{ Step = $Name; Status = $Status; Detail = $Detail })
    $color = switch ($Status) { 'OK' { 'Green' } 'WARN' { 'Yellow' } default { 'Red' } }
    Write-Host ("[{0}] {1}: {2}" -f $Status, $Name, $Detail) -ForegroundColor $color
}

function Get-CommandOutput([string]$Exe, [string[]]$Arguments) {
    try {
        $out = & $Exe @Arguments 2>&1 | Out-String
        return @{ Ok = ($LASTEXITCODE -eq 0); Text = $out.Trim() }
    } catch {
        return @{ Ok = $false; Text = $_.Exception.Message }
    }
}

Write-Host "=== Deploy NLC: установка и проверка ($Root) ===" -ForegroundColor Cyan

# --- 1. Внешние программы ----------------------------------------------------

$py = Get-CommandOutput 'py' @("-$($Expected.Python)", '--version')
if ($py.Ok) { Add-Result 'Python' 'OK' $py.Text }
else { Add-Result 'Python' 'FAIL' "Не найден Python $($Expected.Python) (py -$($Expected.Python)). Установите с python.org с галочкой 'Add python.exe to PATH'." }

$node = Get-CommandOutput 'node' @('--version')
if ($node.Ok) { Add-Result 'Node.js' 'OK' $node.Text }
else { Add-Result 'Node.js' 'FAIL' 'Не найден node. Установите Node.js LTS с nodejs.org.' }

$appium = Get-CommandOutput 'appium' @('--version')
if ($appium.Ok) {
    $ver = ($appium.Text -split "`n")[-1].Trim()
    if ($ver -eq $Expected.Appium) { Add-Result 'Appium' 'OK' $ver }
    else { Add-Result 'Appium' 'WARN' "Установлен $ver, проверено на $($Expected.Appium): npm install -g appium@$($Expected.Appium)" }
} else { Add-Result 'Appium' 'FAIL' "Не найден appium: npm install -g appium@$($Expected.Appium)" }

$drivers = Get-CommandOutput 'appium' @('driver', 'list', '--installed')
if ($drivers.Text -match 'uiautomator2@([\d\.]+)') {
    $dv = $Matches[1]
    if ($dv -eq $Expected.Uiautomator) { Add-Result 'Драйвер uiautomator2' 'OK' $dv }
    else { Add-Result 'Драйвер uiautomator2' 'WARN' "Установлен $dv, проверено на $($Expected.Uiautomator)" }
} else { Add-Result 'Драйвер uiautomator2' 'FAIL' "Не установлен: appium driver install uiautomator2@$($Expected.Uiautomator)" }

$java = Get-CommandOutput 'java' @('-version')
if ($java.Ok -or $java.Text -match 'version') { Add-Result 'Java' 'OK' ((($java.Text -split "`n")[0]) -replace '^java(\.exe)?\s*:\s*', '') }
else { Add-Result 'Java' 'FAIL' 'Не найдена java. Установите JDK 17+ (adoptium.net).' }

$sdk = $env:ANDROID_HOME
if (-not $sdk) { $sdk = $env:ANDROID_SDK_ROOT }
if (-not $sdk) {
    Add-Result 'ANDROID_HOME' 'FAIL' 'Переменная не задана. Appium (uiautomator2) без неё не создаёт сессию. См. docs/INSTALL.md, раздел Android SDK.'
} else {
    $adbInSdk = Join-Path $sdk 'platform-tools\adb.exe'
    $bt = Join-Path $sdk 'build-tools'
    if (-not (Test-Path $adbInSdk)) { Add-Result 'ANDROID_HOME' 'FAIL' "$sdk\platform-tools\adb.exe не найден" }
    elseif (-not (Test-Path $bt) -or -not (Get-ChildItem $bt -Directory -ErrorAction SilentlyContinue)) {
        Add-Result 'ANDROID_HOME' 'FAIL' "$sdk\build-tools пуст: sdkmanager ""build-tools;34.0.0"""
    } else { Add-Result 'ANDROID_HOME' 'OK' $sdk }
}

$adb = Get-CommandOutput 'adb' @('version')
if ($adb.Ok) { Add-Result 'adb в PATH' 'OK' (($adb.Text -split "`n")[0]) }
else { Add-Result 'adb в PATH' 'FAIL' 'adb не найден в PATH: добавьте <ANDROID_HOME>\platform-tools в Path (нужно для start_all*.bat).' }

# --- 2. Окружение Python -----------------------------------------------------

$venvPy = Join-Path $Root 'venv\Scripts\python.exe'
if (-not $CheckOnly) {
    if (-not (Test-Path $venvPy)) {
        Write-Host 'Создаю venv...'
        & py "-$($Expected.Python)" -m venv venv
    }
    if (Test-Path $venvPy) {
        Write-Host 'Устанавливаю зависимости из requirements-lock.txt...'
        & $venvPy -m pip install --disable-pip-version-check -q -r requirements-lock.txt
        if ($LASTEXITCODE -eq 0) { Add-Result 'Зависимости Python' 'OK' 'requirements-lock.txt установлен' }
        else { Add-Result 'Зависимости Python' 'FAIL' 'pip install завершился с ошибкой (см. вывод выше)' }

        Write-Host 'Устанавливаю Chromium для Playwright...'
        & $venvPy -m playwright install chromium
        if ($LASTEXITCODE -eq 0) { Add-Result 'Chromium (Playwright)' 'OK' 'установлен' }
        else { Add-Result 'Chromium (Playwright)' 'FAIL' 'playwright install chromium завершился с ошибкой' }
    } else {
        Add-Result 'venv' 'FAIL' 'Не удалось создать venv'
    }
} elseif (Test-Path $venvPy) {
    Add-Result 'venv' 'OK' 'есть'
} else {
    Add-Result 'venv' 'FAIL' 'нет venv: запустите скрипт без -CheckOnly'
}

# --- 3. Настройки ------------------------------------------------------------

$envFile = Join-Path $Root '.env'
if (-not (Test-Path $envFile)) {
    if ($CheckOnly) { Add-Result '.env' 'FAIL' 'файла нет' }
    else {
        Copy-Item (Join-Path $Root '.env.example') $envFile
        Add-Result '.env' 'WARN' 'создан из .env.example — заполните значения (BOT_TOKEN обязателен)'
    }
}
$tokenSet = $false
if (Test-Path $envFile) {
    $envText = Get-Content $envFile -Encoding UTF8
    $tokenSet = [bool]($envText | Where-Object { $_ -match '^\s*BOT_TOKEN\s*=\s*\S+' })
    $missing = @('BOT_TOKEN', 'ADMIN_CHAT_ID', 'ANDROID_UDID', 'CARMOODO_USERNAME', 'CARMOODO_PASSWORD',
                 'PARTSNUMBER_USERNAME', 'PARTSNUMBER_PASSWORD') |
        Where-Object { $k = $_; -not ($envText | Where-Object { $_ -match "^\s*$k\s*=\s*\S+" }) }
    if ($missing) { Add-Result '.env' 'WARN' ("не заполнено: " + ($missing -join ', ')) }
    else { Add-Result '.env' 'OK' 'основные значения заполнены' }
}

# --- 4. Тесты ----------------------------------------------------------------

if (-not $SkipTests -and (Test-Path $venvPy)) {
    Write-Host 'Запускаю тесты...'
    $env:PYTHONIOENCODING = 'utf-8'
    $testOut = & $venvPy -m unittest discover -s tests -p 'test_*.py' 2>&1 | Out-String
    $summary = ($testOut -split "`n" | Where-Object { $_ -match '^\s*(Ran |OK|FAILED)' }) -join ' '
    if ($LASTEXITCODE -eq 0) { Add-Result 'Тесты' 'OK' $summary.Trim() }
    else { Write-Host $testOut; Add-Result 'Тесты' 'FAIL' $summary.Trim() }
}

# --- 5. Планшет --------------------------------------------------------------

$devices = Get-CommandOutput 'adb' @('devices')
$deviceLines = @($devices.Text -split "`n" | Where-Object { $_ -match "\t" })
if (-not $devices.Ok) {
    Add-Result 'Планшет' 'FAIL' 'adb недоступен'
} elseif ($deviceLines.Count -eq 0) {
    Add-Result 'Планшет' 'FAIL' 'не подключён: кабель, питание, отладка по USB'
} elseif ($deviceLines -match 'unauthorized') {
    Add-Result 'Планшет' 'FAIL' 'unauthorized: подтвердите отладку по USB на экране планшета'
} else {
    $model = (& adb shell getprop ro.product.model 2>$null | Out-String).Trim()
    $android = (& adb shell getprop ro.build.version.release 2>$null | Out-String).Trim()
    $size = (& adb shell wm size 2>$null | Out-String).Trim()
    $locale = (& adb shell getprop persist.sys.locale 2>$null | Out-String).Trim()
    Add-Result 'Планшет' 'OK' "$model, Android $android, $size, язык $locale"
    if ($deviceLines.Count -gt 1) { Add-Result 'Планшет' 'WARN' 'подключено несколько устройств — задайте ANDROID_UDID в .env' }
    if ($size -notmatch '800x1340') { Add-Result 'Экран планшета' 'WARN' 'система откалибрована на 800x1340 (Samsung SM-T220); на другом экране проверьте прокрутку истории HeyDealer' }

    if ($ConfigureTablet -and -not $CheckOnly) {
        Write-Host 'Настраиваю планшет...'
        foreach ($k in 'window_animation_scale', 'transition_animation_scale', 'animator_duration_scale') {
            & adb shell settings put global $k 0 | Out-Null
        }
        & adb shell settings put system accelerometer_rotation 0 | Out-Null
        & adb shell settings put system user_rotation 0 | Out-Null
        & adb install -r (Join-Path $Root 'ADBKeyboard.apk') | Out-Null
        & adb shell ime enable com.android.adbkeyboard/.AdbIME | Out-Null
        & adb shell ime set com.android.adbkeyboard/.AdbIME | Out-Null
        & adb shell cmd appops set kr.perfectree.heydealer POST_NOTIFICATION ignore 2>$null | Out-Null
        Add-Result 'Настройка планшета' 'OK' 'анимации 0, портрет, ADBKeyBoard активна, уведомления HeyDealer запрещены'
    }

    $packages = (& adb shell pm list packages 2>$null | Out-String)
    foreach ($p in @(
            @{ Id = 'kr.co.ggkucar.app'; Name = 'Carmoodo' },
            @{ Id = 'kr.perfectree.heydealer'; Name = 'HeyDealer' },
            @{ Id = 'com.android.adbkeyboard'; Name = 'ADBKeyBoard' })) {
        if ($packages -match [regex]::Escape("package:$($p.Id)")) { Add-Result "Приложение $($p.Name)" 'OK' 'установлено' }
        else { Add-Result "Приложение $($p.Name)" 'FAIL' "не установлено ($($p.Id))" }
    }
    $anim = (& adb shell settings get global window_animation_scale 2>$null | Out-String).Trim()
    if ($anim -notin @('0', '0.0')) { Add-Result 'Анимации' 'WARN' "window_animation_scale=$anim (рекомендуется 0; запустите с -ConfigureTablet)" }
    $ime = (& adb shell settings get secure default_input_method 2>$null | Out-String).Trim()
    if ($ime -ne 'com.android.adbkeyboard/.AdbIME') { Add-Result 'Клавиатура' 'FAIL' "активна $ime; нужна ADBKeyBoard (запустите с -ConfigureTablet)" }
    $google = (& adb shell dumpsys account 2>$null | Out-String)
    if ($google -notmatch 'type=com\.google') { Add-Result 'Google-аккаунт' 'FAIL' 'на планшете нет Google-аккаунта: без него не работает сброс рекламного ID (лимит HeyDealer)' }
    else { Add-Result 'Google-аккаунт' 'OK' 'есть' }
}

# --- 6. Стартовые проверки бота ----------------------------------------------

if ($tokenSet -and (Test-Path $venvPy)) {
    Write-Host 'Стартовые проверки бота (без подключения к Telegram)...'
    $env:PYTHONIOENCODING = 'utf-8'
    & $venvPy -c "import diagnostics, sys; sys.exit(0 if diagnostics.run_preflight() else 1)"
    if ($LASTEXITCODE -eq 0) { Add-Result 'Проверки бота' 'OK' 'все пройдены' }
    else { Add-Result 'Проверки бота' 'WARN' 'есть замечания — см. строки выше' }
} else {
    Add-Result 'Проверки бота' 'WARN' 'пропущены: BOT_TOKEN в .env не задан'
}

# --- Итог --------------------------------------------------------------------

Write-Host ''
Write-Host '=== Итог ===' -ForegroundColor Cyan
$results | Format-Table -AutoSize -Wrap | Out-String -Width 200 | Write-Host
$failed = @($results | Where-Object { $_.Status -eq 'FAIL' })
if ($failed.Count -gt 0) {
    Write-Host "Есть ошибки: $($failed.Count). Порядок исправления — docs/INSTALL.md." -ForegroundColor Red
    exit 1
}
Write-Host 'Готово. Запуск системы: start_all.bat' -ForegroundColor Green
exit 0
