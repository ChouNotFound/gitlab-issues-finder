<#
.SYNOPSIS
    一键检查环境并启动 GitLab Issues Finder。
.DESCRIPTION
    按顺序检查：
      1. Python 是否安装
      2. 虚拟环境 .venv 是否存在（不存在则询问是否创建）
      3. 依赖是否齐全（缺则自动 pip install）
      4. .env 是否存在（缺则从 .env.example 复制）
      5. .env 中 GITLAB_TOKEN 是否还是占位符
      6. 端口 WEB_PORT 是否被占用
    然后在 venv 中启动 uvicorn 服务。
.PARAMETER Detached
    后台启动（返回进程对象，不阻塞当前 shell）。
.PARAMETER NoBrowser
    不自动打开浏览器。
.EXAMPLE
    .\run.ps1
    .\run.ps1 -Detached
    .\run.ps1 -NoBrowser
#>

[CmdletBinding()]
param(
    [switch]$Detached,
    [switch]$NoBrowser
)

# ===== 常量 =====
$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ScriptDir '.venv'
$EnvFile = Join-Path $ScriptDir '.env'
$EnvExample = Join-Path $ScriptDir '.env.example'
$ReqFile = Join-Path $ScriptDir 'requirements.txt'

# 端口（与 .env 默认一致；启动时再从 .env 读真实值）
$DefaultPort = 8000
$DefaultServerHost = '127.0.0.1'

# ===== 输出辅助函数 =====
function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Ok($msg) {
    Write-Host "  [OK] $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host "  [WARN] $msg" -ForegroundColor Yellow
}

function Write-Err($msg) {
    Write-Host "  [ERR] $msg" -ForegroundColor Red
}

# ===== 1. Python 检查 =====
Write-Step "检查 Python 环境"
$PythonCmd = $null

foreach ($cmd in @('py', 'python', 'python3')) {
    try {
        $versionOutput = & $cmd --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $PythonCmd = $cmd
            Write-Ok "找到 $cmd ：$versionOutput"
            break
        }
    } catch {
        # 命令不存在，继续尝试下一个
    }
}

if (-not $PythonCmd) {
    Write-Err "未找到 Python。请先安装 Python 3.10+："
    Write-Host "       https://www.python.org/downloads/windows/" -ForegroundColor Yellow
    Write-Host "       安装时务必勾选 'Add Python to PATH'" -ForegroundColor Yellow
    exit 1
}

# ===== 2. 虚拟环境 =====
Write-Step "检查虚拟环境 .venv"
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

if (Test-Path $VenvPython) {
    Write-Ok "虚拟环境已存在：$VenvDir"
} else {
    Write-Warn "虚拟环境不存在：$VenvDir"
    $answer = Read-Host "    是否现在创建？[Y/n]"
    if ($answer -eq '' -or $answer -match '^[Yy]') {
        Write-Host "    正在创建虚拟环境..." -ForegroundColor Gray
        & $PythonCmd -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            Write-Err "创建虚拟环境失败"
            exit 1
        }
        Write-Ok "虚拟环境已创建"
    } else {
        Write-Err "已取消。请手动创建虚拟环境后再运行。"
        exit 1
    }
}

# ===== 3. 依赖检查 =====
Write-Step "检查依赖"
$NeedInstall = $false

try {
    & $VenvPython -c "import fastapi, uvicorn, gitlab, jinja2, dotenv" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "核心依赖已安装"
    } else {
        $NeedInstall = $true
    }
} catch {
    $NeedInstall = $true
}

if ($NeedInstall) {
    Write-Warn "依赖缺失或不完整，正在安装..."
    & $VenvPython -m pip install --disable-pip-version-check -r $ReqFile
    if ($LASTEXITCODE -ne 0) {
        Write-Err "依赖安装失败。请检查网络或 requirements.txt。"
        exit 1
    }
    Write-Ok "依赖安装完成"
}

# ===== 4. .env 检查 =====
Write-Step "检查 .env 配置"

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Write-Warn ".env 不存在，将从 .env.example 复制"
        Copy-Item $EnvExample $EnvFile
        Write-Ok ".env 已创建"
    } else {
        Write-Err ".env 和 .env.example 都不存在"
        exit 1
    }
}

# 检查 token 是否还是占位符
$envContent = Get-Content $EnvFile -Raw
if ($envContent -match 'GITLAB_TOKEN\s*=\s*(glpat-xxxxxxxxxxxxxxxxxxxx|请把您的 Personal Access Token 粘贴到这里|$)') {
    Write-Warn "GITLAB_TOKEN 仍是占位符！请编辑 .env 填入真实 Token 后再启动。"
    Write-Host "       编辑命令：notepad `"$EnvFile`"" -ForegroundColor Yellow
    $answer = Read-Host "    是否仍要继续启动（方便检查首页 UI）？[y/N]"
    if ($answer -notmatch '^[Yy]') {
        exit 1
    }
} else {
    Write-Ok ".env 中 GITLAB_TOKEN 已配置"
}

# 从 .env 读取实际端口
$PortMatch = [regex]::Match($envContent, 'WEB_PORT\s*=\s*(\d+)')
$Port = if ($PortMatch.Success) { [int]$PortMatch.Groups[1].Value } else { $DefaultPort }

$HostMatch = [regex]::Match($envContent, 'WEB_HOST\s*=\s*(\S+)')
$ServerHost = if ($HostMatch.Success) { $HostMatch.Groups[1].Value } else { $DefaultServerHost }

# ===== 5. 端口检查 =====
Write-Step "检查端口 $Port 是否被占用"

$PortInUse = $false
try {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
    if ($conn) {
        $PortInUse = $true
        $existingPid = $conn.OwningProcess
        $existingProc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        $procName = if ($existingProc) { $existingProc.ProcessName } else { 'unknown' }
    }
} catch {
    $PortInUse = $false
}

if ($PortInUse) {
    Write-Warn "端口 $Port 已被占用（PID=$existingPid, 进程=$procName）"
    $answer = Read-Host "    是否尝试停止该进程？[y/N]"
    if ($answer -match '^[Yy]') {
        try {
            Stop-Process -Id $existingPid -Force
            Write-Ok "已停止 PID=$existingPid"
            Start-Sleep -Seconds 1
            $PortInUse = $false
        } catch {
            Write-Err "停止进程失败：$_"
            exit 1
        }
    } else {
        Write-Err "请先释放端口 $Port，或修改 .env 中 WEB_PORT 改为其他值"
        exit 1
    }
} else {
    Write-Ok "端口 $Port 可用"
}

# ===== 6. 启动服务 =====
Write-Step "启动服务"

# src-layout：uvicorn 不会读 pyproject.toml 的 pythonpath，需手动把 src/ 加进 sys.path
$env:PYTHONPATH = Join-Path $ScriptDir 'src'
$Url = "http://$($ServerHost):$Port"

if ($Detached) {
    # 后台启动：使用 Start-Process，返回进程对象
    Write-Host "    后台启动中..." -ForegroundColor Gray
    $proc = Start-Process -FilePath $VenvPython `
                           -ArgumentList @('-m', 'uvicorn', 'gitlab_issues_finder.app:app',
                                           '--host', $ServerHost, '--port', $Port) `
                           -WorkingDirectory $ScriptDir `
                           -WindowStyle Hidden `
                           -RedirectStandardOutput (Join-Path $ScriptDir 'uvicorn.out.log') `
                           -RedirectStandardError  (Join-Path $ScriptDir 'uvicorn.err.log') `
                           -PassThru
    Write-Ok "已在后台启动 (PID=$($proc.Id))"
    Write-Host ""
    Write-Host "    访问地址：$Url" -ForegroundColor Green
    Write-Host "    日志文件：uvicorn.out.log / uvicorn.err.log" -ForegroundColor Gray
    Write-Host "    停止命令：Stop-Process -Id $($proc.Id)" -ForegroundColor Gray

    # 等待服务就绪
    Write-Host ""
    Write-Host "    等待服务就绪..." -NoNewline -ForegroundColor Gray
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $resp = Invoke-WebRequest -Uri $Url -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
            if ($resp.StatusCode -eq 200) { $ready = $true; break }
        } catch {}
        Write-Host "." -NoNewline -ForegroundColor Gray
    }
    Write-Host ""

    if ($ready) {
        Write-Ok "服务已就绪"
        if (-not $NoBrowser) {
            Start-Process $Url
            Write-Host "    已打开浏览器" -ForegroundColor Gray
        }
    } else {
        Write-Warn "服务启动超时（10 秒），请查看日志确认"
    }
} else {
    # 前台启动：直接运行，Ctrl+C 终止
    Write-Host "    前台启动，按 Ctrl+C 停止" -ForegroundColor Gray
    Write-Host "    访问地址：$Url" -ForegroundColor Green
    Write-Host ""

    if (-not $NoBrowser) {
        # 延迟 2 秒打开浏览器，等 uvicorn 先启动起来
        Start-Job -ScriptBlock {
            param($u)
            Start-Sleep -Seconds 2
            Start-Process $u
        } -ArgumentList $Url | Out-Null
    }

    Push-Location $ScriptDir
    try {
        & $VenvPython -m uvicorn gitlab_issues_finder.app:app --host $ServerHost --port $Port
    } finally {
        Pop-Location
    }
}