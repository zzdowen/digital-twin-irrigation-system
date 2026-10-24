<#
.SYNOPSIS
    FARM 灌溉监控看板 · Windows 本机一键部署 / 更新脚本。

.DESCRIPTION
    在 Windows 上执行（PowerShell 5.1 或 PowerShell 7 均可）。
    它做的事：
      1. 把本地项目目录打成 tar.gz（排除 data/ venv/ .env __pycache__ 等运行态与垃圾）
      2. scp 上传到服务器 /tmp/farm-latest.tar.gz
      3. ssh 在服务器上解包到 /opt/farm（**不删除**已有 data/ 与 .env）
      4. ssh 在服务器上执行 deploy/install.sh（幂等）→ 装依赖、装服务、重启
      5. ssh 做 /health 健康检查
    全程**绝不**触碰上游 irrigation-server / moisture-simulator / mosquitto 三个服务。

    说明：本脚本用 Windows 自带的 tar.exe（Win10 1803+ / Server 2019+ 自带）打包，
    ssh.exe / scp.exe 用 OpenSSH 客户端（Win10 1809+ 自带）。
    Linux / macOS / WSL 请改用 deploy/update.sh（走 rsync，更快）。

.PARAMETER Server
    服务器公网 IP，例如 203.0.113.10（必填）。

.PARAMETER KeyPath
    SSH 私钥路径，例如 $env:USERPROFILE\.ssh\id_rsa。
    不填则用默认密钥 / ssh-agent；首次连接会提示确认主机指纹或输入密码。

.PARAMETER User
    SSH 登录用户名，默认 root。

.PARAMETER Port
    看板监听端口，默认 8000。

.PARAMETER AppDir
    远程安装目录，默认 /opt/farm。

.PARAMETER NoRestart
    只上传和解包，不执行 install.sh、不重启服务。

.PARAMETER SkipHealth
    跳过健康检查。

.PARAMETER SkipPackage
    跳过打包，直接用本地已有的 .\farm-latest.tar.gz（调试用）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\deploy\deploy.ps1 -Server 203.0.113.10 -KeyPath $env:USERPROFILE\.ssh\id_rsa

.EXAMPLE
    # 密码登录（不传 -KeyPath，会提示输入密码）
    .\deploy\deploy.ps1 -Server 203.0.113.10
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Server,

    [string]$KeyPath = "",
    [string]$User = "root",
    [int]$Port = 8000,
    [string]$AppDir = "/opt/farm",
    [switch]$NoRestart,
    [switch]$SkipHealth,
    [switch]$SkipPackage
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ─────────────────────────── 输出助手 ───────────────────────────
function Write-Info { param([string]$m) Write-Host "[INFO] $m" -ForegroundColor Cyan }
function Write-Ok   { param([string]$m) Write-Host "[ OK ] $m" -ForegroundColor Green }
function Write-Warn { param([string]$m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Fail       { param([string]$m) Write-Host "[FAIL] $m" -ForegroundColor Red; exit 1 }

# ─────────────────────────── 路径与工具检查 ───────────────────────────
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$ReqFile     = Join-Path $ProjectRoot "backend\requirements.txt"
$SvcFile     = Join-Path $ScriptDir "farm.service"
$Tarball     = Join-Path $ProjectRoot "farm-latest.tar.gz"

Write-Host ""
Write-Host "════════════ FARM 看板部署（Windows 一键）════════════" -ForegroundColor Cyan
Write-Info "项目目录 : $ProjectRoot"
Write-Info "目标服务器: ${User}@${Server}  ($AppDir, 端口 $Port)"

if (-not (Test-Path $ReqFile)) { Fail "找不到 $ReqFile —— 请在 farm\ 目录里的 deploy\ 下运行本脚本。" }
if (-not (Test-Path $SvcFile)) { Fail "找不到 $SvcFile —— 项目不完整。" }

$TarExe = (Get-Command tar.exe -ErrorAction SilentlyContinue)
if (-not $TarExe) { Fail "找不到 tar.exe。需要 Windows 10 1803+ / Server 2019+（自带 bsdtar），或安装 Git for Windows 后把它的 tar 加进 PATH。" }

$SshExe = (Get-Command ssh.exe -ErrorAction SilentlyContinue)
if (-not $SshExe) { Fail "找不到 ssh.exe。请安装 OpenSSH 客户端：设置 → 应用 → 可选功能 → 添加功能 → OpenSSH 客户端。" }

$ScpExe = (Get-Command scp.exe -ErrorAction SilentlyContinue)
if (-not $ScpExe) { Fail "找不到 scp.exe。同上，随 OpenSSH 客户端一起安装。" }

# SSH 参数（与 deploy/update.sh 保持一致）
$SshArgs = @()
if ($KeyPath -ne "") {
    if (-not (Test-Path $KeyPath)) { Fail "私钥不存在：$KeyPath" }
    $SshArgs += @("-i", $KeyPath)
    # Windows 上私钥权限过宽时 OpenSSH 会拒绝；这里只提示，不自动改 ACL
    Write-Info "使用私钥 : $KeyPath"
}
$SshArgs += @("-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15")
$Target = "${User}@${Server}"

function Invoke-Remote {
    param([string]$Command, [switch]$AllowFail)
    $out = & ssh.exe @SshArgs $Target $Command 2>&1
    $code = $LASTEXITCODE
    if ($out) { $out | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
    if ($code -ne 0 -and -not $AllowFail) {
        Fail "远程命令失败（exit $code）：$Command"
    }
    return $code
}

# ─────────────────────────── 1. 打包 ───────────────────────────
if (-not $SkipPackage) {
    Write-Info "打包代码 → $Tarball"
    if (Test-Path $Tarball) { Remove-Item $Tarball -Force }

    # 只打我们需要上传的东西：backend / web / deploy / tests / docs / CONTRACT.md
    # 显式列目录比 --exclude 全排除更安全（不会把 farm.db、日志、探针脚本带上去）
    $items = @()
    foreach ($d in @("backend", "web", "deploy", "tests", "docs")) {
        $p = Join-Path $ProjectRoot $d
        if (Test-Path $p) { $items += $d }
    }
    $contract = Join-Path $ProjectRoot "CONTRACT.md"
    if (Test-Path $contract) { $items += "CONTRACT.md" }
    if ($items.Count -eq 0) { Fail "项目目录里没有可上传的内容（backend/web/deploy/tests/docs 全都不存在）。" }

    Push-Location $ProjectRoot
    try {
        & tar.exe -czf $Tarball `
            --exclude "__pycache__" `
            --exclude "*.pyc" `
            --exclude "*.pyo" `
            --exclude ".pytest_cache" `
            --exclude ".pytest-tmp-*" `
            --exclude "pytest-cache-files-*" `
            --exclude "node_modules" `
            --exclude ".DS_Store" `
            @items
        if ($LASTEXITCODE -ne 0) { Fail "tar 打包失败（exit $LASTEXITCODE）。" }
    }
    finally { Pop-Location }

    $size = [math]::Round((Get-Item $Tarball).Length / 1KB, 1)
    Write-Ok "打包完成：$size KB（含 $($items -join ', ')）"
}
else {
    if (-not (Test-Path $Tarball)) { Fail "-SkipPackage 指定了，但 $Tarball 不存在。" }
    Write-Info "跳过打包，使用已有 $Tarball"
}

# ─────────────────────────── 2. 连通性 ───────────────────────────
Write-Info "测试 SSH 连通性：$Target ……"
$null = Invoke-Remote -Command "true" -AllowFail
if ($LASTEXITCODE -ne 0) {
    Fail @"
SSH 连不上 $Target。依次检查：
  1) IP 是否正确（云厂商控制台 → 轻量应用服务器 → 公网 IP）
  2) 云防火墙是否放行 22/tcp
  3) 私钥是否匹配（不传 -KeyPath 时用的是默认密钥/ssh-agent，可能需要密码）
  4) 用户名是否为 $User
手动验证：ssh ${User}@${Server}
"@
}
Write-Ok "SSH 已连通。"

# ─────────────────────────── 3. 上传 ───────────────────────────
Write-Info "上传到服务器 /tmp/farm-latest.tar.gz ……"
& scp.exe @SshArgs $Tarball "${Target}:/tmp/farm-latest.tar.gz"
if ($LASTEXITCODE -ne 0) { Fail "scp 上传失败（exit $LASTEXITCODE）。检查磁盘空间：ssh ${Target} 'df -h /tmp'" }
Write-Ok "上传完成。"

# ─────────────────────────── 4. 远程解包 ───────────────────────────
# 关键：先 mkdir -p，再 tar -xzf 到 /opt/farm。
# 归档里**没有** data/ 和 .env，所以解包只会覆盖代码，不会碰数据库和管理员密码。
Write-Info "远程解包到 $AppDir ……"
Invoke-Remote -Command "set -e; mkdir -p $AppDir /tmp; tar -xzf /tmp/farm-latest.tar.gz -C $AppDir; echo unpacked"

# 属主：代码 root:farm 只读，数据目录归 farm（首次部署时 farm 用户还不存在，忽略错误）
$null = Invoke-Remote -AllowFail -Command @"
set +e
chown -R root:farm $AppDir 2>/dev/null
chmod -R g+rX $AppDir 2>/dev/null
chown -R farm:farm $AppDir/data 2>/dev/null
chmod 750 $AppDir $AppDir/data 2>/dev/null
[ -f $AppDir/.env ] && chown root:farm $AppDir/.env && chmod 640 $AppDir/.env
echo perms-ok
"@

if ($NoRestart) {
    Write-Warn "已指定 -NoRestart：代码已上传解包，但**没有**执行 install.sh、没有重启服务。"
    Write-Host ""
    Write-Host "手动完成：" -ForegroundColor Yellow
    Write-Host "  ssh ${Target} 'bash $AppDir/deploy/install.sh --yes --port $Port'"
    Write-Host ""
    exit 0
}

# ─────────────────────────── 5. 依赖 + 服务 + 重启 ───────────────────────────
# install.sh 是幂等的：已有 .env 就保留，已有 venv 就复用，最后 restart farm.service
Write-Info "远程执行 install.sh（装依赖 / 装 farm.service / 重启）……"
Invoke-Remote -Command "cd $AppDir && bash deploy/install.sh --yes --port $Port"
Write-Ok "install.sh 执行完成。"

# ─────────────────────────── 6. 健康检查 ───────────────────────────
if (-not $SkipHealth) {
    Write-Info "健康检查 http://127.0.0.1:$Port/health（最多 20 秒）……"
    $null = Invoke-Remote -AllowFail -Command "for i in `$(seq 1 20); do curl -fsS --max-time 2 http://127.0.0.1:$Port/health 2>/dev/null | grep -q '`"ok`"' && exit 0; sleep 1; done; exit 1"
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "20 秒内 /health 未返回 ok，最近 60 行日志："
        $null = Invoke-Remote -AllowFail -Command "journalctl -u farm.service -n 60 --no-pager"
        Write-Warn "上游服务状态（只读检查，绝不修改）："
        $null = Invoke-Remote -AllowFail -Command "systemctl is-active irrigation-server.service moisture-simulator.service mosquitto.service 2>&1 || true"
        Fail @"
服务未正常启动。回滚步骤（在服务器上执行）：
  ls -1t $AppDir/backups/app-*.tar.gz | head -3
  tar -xzf $AppDir/backups/app-<时间戳>.tar.gz -C $AppDir
  systemctl restart farm.service
注意：回滚 farm 不影响上游 irrigation-server，那个服务全程没被碰过。
"@
    }
    Write-Ok "健康检查通过。"
}

# ─────────────────────────── 7. 结果 ───────────────────────────
$publicIp = (& ssh.exe @SshArgs $Target "curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || echo ''" 2>$null)
if ([string]::IsNullOrWhiteSpace($publicIp)) { $publicIp = $Server }
$publicIp = $publicIp.Trim()

Write-Host ""
Write-Host "════════════ FARM 看板部署完成 ════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  访问地址 : http://${publicIp}:$Port/" -ForegroundColor White
Write-Host "  健康检查 : http://${publicIp}:$Port/health" -ForegroundColor White
Write-Host "  服务器   : ${Target}:$AppDir" -ForegroundColor White
Write-Host "  日志     : ssh ${Target} 'journalctl -u farm.service -f'" -ForegroundColor White
Write-Host ""
Write-Host "【⚠️ 云防火墙】" -ForegroundColor Yellow
Write-Host "  只放行了 22/5000/4096/6080/1883 —— 8000 需要手动加规则："
Write-Host "  云厂商控制台 → 轻量应用服务器 → 防火墙 → 添加规则"
Write-Host "      应用类型=自定义  协议=TCP  端口=$Port  来源=0.0.0.0/0  策略=允许"
Write-Host "  不加这条，浏览器访问 http://${publicIp}:$Port 会一直转圈直到超时。"
Write-Host ""
Write-Host "【验收自测】" -ForegroundColor Yellow
Write-Host "  ssh ${Target} `"cd $AppDir && bash tests/verify.sh --user admin --pass 当前密码`""
Write-Host "  11 条全 PASS 才算部署成功。"
Write-Host ""
Write-Host "【备注】" -ForegroundColor Yellow
Write-Host "  * 本次上传的是 $Tarball（可保留作回滚包，也可删掉）"
Write-Host "  * 服务器上的 data/ 与 .env 未被覆盖；管理员密码、历史采样都在"
Write-Host "  * 上游 irrigation-server / moisture-simulator / mosquitto 全程未被触碰"
Write-Host ""
