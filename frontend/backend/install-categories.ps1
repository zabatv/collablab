# Ставит недостающие эндпоинты категорий в их бэкенд на Windows.
#
# Запуск одной строкой:
#   [Net.ServicePointManager]::SecurityProtocol='Tls12'; iwr https://robots07.com/backend/install-categories.ps1 -OutFile $env:TEMP\i.ps1 -UseBasicParsing; powershell -ExecutionPolicy Bypass -File $env:TEMP\i.ps1
#
# Что делает:
#   1. кладёт src/routes/admin-categories.js;
#   2. дописывает две строки в src/admin-app.js (с резервной копией);
#   3. проверяет, что получилось.
# Повторный запуск безопасен: уже сделанное не дублируется.

param(
  [string]$Backend = 'C:\shop-backend',
  [string]$Source = 'https://robots07.com/backend/admin-categories.js'
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Good($text) { Write-Host "   $text" -ForegroundColor Green }
function Bad($text) { Write-Host "   $text" -ForegroundColor Red }

$appFile = Join-Path $Backend 'src\admin-app.js'
if (-not (Test-Path $appFile)) {
  Bad "Не нашёл $appFile"
  Bad "Если бэкенд лежит в другой папке, запустите: .\i.ps1 -Backend C:\путь\к\бэкенду"
  exit 1
}

Step 'Кладу admin-categories.js'
$routes = Join-Path $Backend 'src\routes'
$target = Join-Path $routes 'admin-categories.js'
New-Item -ItemType Directory -Force -Path $routes | Out-Null
Invoke-WebRequest $Source -OutFile $target -UseBasicParsing
Good "$target — $((Get-Item $target).Length) байт"

Step 'Подключаю в сервере админки'
$text = Get-Content $appFile -Raw -Encoding UTF8
if ($text -match 'admin-categories') {
  Good 'Уже подключено, файл не трогаю'
} else {
  $backup = "$appFile.before-categories"
  if (-not (Test-Path $backup)) { Copy-Item $appFile $backup }

  $importAnchor = "import { categoriesRouter } from './routes/categories.js';"
  $mountAnchor = "  app.use('/categories', categoriesRouter(pool));"
  if ($text -notmatch [regex]::Escape($importAnchor) -or $text -notmatch [regex]::Escape($mountAnchor)) {
    Bad 'Не нашёл, куда вписать строки: admin-app.js отличается от ожидаемого.'
    Bad 'Добавьте вручную рядом с categoriesRouter:'
    Bad "  import { adminCategoriesRouter } from './routes/admin-categories.js';"
    Bad "  app.use('/categories', adminCategoriesRouter(pool));"
    exit 1
  }

  $text = $text.Replace($importAnchor,
    "import { adminCategoriesRouter } from './routes/admin-categories.js';`r`n$importAnchor")
  # строкой ниже чтения: GET забирает categoriesRouter, остальное — наш
  $text = $text.Replace($mountAnchor,
    "$mountAnchor`r`n  app.use('/categories', adminCategoriesRouter(pool));")
  [IO.File]::WriteAllText($appFile, $text, (New-Object Text.UTF8Encoding $false))
  Good "Вписано, прежний файл сохранён как $(Split-Path $backup -Leaf)"
}

Step 'Проверяю'
$found = Select-String -Path $appFile -Pattern 'admin-categories' | Measure-Object
if ($found.Count -eq 2) { Good 'Импорт и подключение на месте' } else { Bad "Ожидал две строки, нашёл $($found.Count)" }

Write-Host "`nОсталось перезапустить сервер админки (порт 3001) — тем же способом, каким он запущен." -ForegroundColor Yellow
Write-Host "После этого категории заводятся и правятся прямо из админки сайта." -ForegroundColor Yellow
