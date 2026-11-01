#!/usr/bin/env pwsh
<#
.SYNOPSIS
    让 ~/.agents/skills 成为指向本仓库 skills/ 的 junction，各 agent 直接读写同一份文件。

.DESCRIPTION
    本仓库的 skills/ 是唯一真相：自研 skill 提交，第三方 skill 以 _ext- 前缀放在同一目录但不提交
    （见 .gitignore）。各 agent 读的是 %USERPROFILE%\.agents\skills，本脚本把这个位置变成指向
    仓库 skills/ 的 junction，从而不再需要复制，也不会出现两份内容不同步。

    优先尝试“整根链接”：一个 junction 覆盖全部 skill，之后新增 skill 不用再跑本脚本。
    如果 ~/.agents/skills 正被运行中的 agent 占用（Windows 上表现为重命名被拒绝），
    退化为“逐 skill 链接”，此时新增 skill 后需要再跑一次。

.PARAMETER DryRun
    只打印将要做什么，不写盘。

.PARAMETER ProjectSkills
    源目录，默认是脚本同级的 skills/。

.PARAMETER Target
    链接位置，默认 %USERPROFILE%\.agents\skills。

.PARAMETER Force
    逐 skill 模式下，允许把目标里已存在的实体目录移到临时备份区后替换为 junction。
    不加此参数时实体目录只会被报告，不会被改动。

.PARAMETER Prune
    逐 skill 模式下，删除目标里指向已不存在源目录的悬空 junction。只删 junction，不删实体目录。

.EXAMPLE
    pwsh ./link_skills.ps1 -DryRun
    pwsh ./link_skills.ps1
    pwsh ./link_skills.ps1 -Force
#>
param(
    [switch]$DryRun,
    [string]$ProjectSkills = (Join-Path $PSScriptRoot 'skills'),
    [string]$Target = (Join-Path $env:USERPROFILE '.agents\skills'),
    [switch]$Force,
    [switch]$Prune
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-JunctionTarget {
    param([string]$Path)
    $item = Get-Item $Path -Force
    if ($item.LinkType -ne 'Junction' -and $item.LinkType -ne 'SymbolicLink') { return $null }
    return ($item.Target | Select-Object -First 1)
}

function New-SkillJunction {
    param([string]$Link, [string]$Source)
    New-Item -ItemType Junction -Path $Link -Target $Source | Out-Null
}

function Write-Plan {
    param([string]$Message)
    $prefix = if ($DryRun) { '[dry-run] ' } else { '[link] ' }
    Write-Host ($prefix + $Message)
}

# ---------------------------------------------------------------- 前置检查
if (-not (Test-Path $ProjectSkills -PathType Container)) {
    Write-Error "源目录不存在: $ProjectSkills"
    exit 1
}
$ProjectSkills = (Resolve-Path $ProjectSkills).Path

$skills = @(Get-ChildItem $ProjectSkills -Directory | Where-Object {
    Test-Path (Join-Path $_.FullName 'SKILL.md')
})
if ($skills.Count -eq 0) {
    Write-Error "源目录里没有 skill（需要 <name>/SKILL.md）: $ProjectSkills"
    exit 1
}

Write-Host "source: $ProjectSkills  ($($skills.Count) skills)"
Write-Host "target: $Target"
Write-Host "mode  : $(if ($DryRun) { 'dry-run (no writes)' } else { 'apply' })"
Write-Host ''

# ---------------------------------------------------------------- 整根链接
$targetParent = Split-Path $Target -Parent
$existingJunction = if (Test-Path $Target) { Get-JunctionTarget $Target } else { $null }

if ($existingJunction) {
    if ($existingJunction.TrimEnd('\', '/') -eq $ProjectSkills.TrimEnd('\', '/')) {
        Write-Host '[ok] 目标已经是本仓库 skills/ 的链接，无需改动'
        exit 0
    }
    Write-Warning "目标已是指向别处的链接: $existingJunction"
    Write-Warning '先手工删除该链接再运行本脚本'
    exit 1
}

function Convert-RootToJunction {
    if (-not (Test-Path $Target)) {
        New-SkillJunction -Link $Target -Source $ProjectSkills
        return $true
    }
    $backup = "$Target.old-$(Get-Date -Format yyyyMMddHHmmss)"
    $backupName = Split-Path $backup -Leaf
    try {
        Rename-Item $Target $backupName
    } catch {
        return $false
    }
    try {
        New-SkillJunction -Link $Target -Source $ProjectSkills
    } catch {
        Rename-Item $backup $Target
        throw
    }
    Write-Host "[ok] 已把 $backup 替换为指向仓库的 junction"
    Write-Host "     原副本留在 $backup，确认无误后可删除"
    return $true
}

if ($DryRun) {
    Write-Plan "尝试把 $Target 整体替换为指向 $ProjectSkills 的 junction"
    if (Test-Path $Target) { Write-Plan "  做法：先把 $Target 重命名为 <同名>.old-<时间戳>，再建 junction" }
    Write-Plan '若整根替换被拒绝（目录被占用），才会走下面的逐 skill 模式'
} else {
    try {
        if (Convert-RootToJunction) { exit 0 }
    } catch {
        Write-Warning "整根链接失败: $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------- 逐 skill 链接
Write-Host ''
Write-Host '逐 skill 链接模式（整根替换不可用，通常是 agent 正在占用该目录）'
Write-Host ''

$backupDir = Join-Path $env:TEMP ("skills-backup-" + (Get-Date -Format yyyyMMddHHmmss))
$changed = 0
$failed = 0

if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $Target | Out-Null }

foreach ($skill in $skills) {
    $link = Join-Path $Target $skill.Name
    $current = if (Test-Path $link) { Get-JunctionTarget $link } else { $null }

    if ($current -and $current.TrimEnd('\', '/') -eq $skill.FullName.TrimEnd('\', '/')) {
        Write-Host "[ok]   $($skill.Name) 已是正确链接"
        continue
    }

    if ($current) {
        Write-Plan "$($skill.Name) 链接指向别处（$current），重建"
        if (-not $DryRun) { Remove-Item $link -Force }
    } elseif (Test-Path $link) {
        if (-not $Force) {
            Write-Warning "[skip] $($skill.Name): 目标是实体目录。加 -Force 可移到备份区后替换"
            $failed++
            continue
        }
        Write-Plan "$($skill.Name) 实体目录 -> 备份区"
        if (-not $DryRun) {
            New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
            Move-Item $link (Join-Path $backupDir $skill.Name) -Force
        }
    } else {
        Write-Plan "$($skill.Name) 新建链接"
    }

    if (-not $DryRun) { New-SkillJunction -Link $link -Source $skill.FullName }
    $changed++
}

# 悬空链接清理：只动 junction，不动实体目录
if ($Prune -and (Test-Path $Target)) {
    foreach ($entry in @(Get-ChildItem $Target -Force)) {
        $jt = Get-JunctionTarget $entry.FullName
        if (-not $jt) { continue }
        if (Test-Path $jt) { continue }
        Write-Plan "删除悬空链接 $($entry.Name) -> $jt"
        if (-not $DryRun) { Remove-Item $entry.FullName -Force }
        $changed++
    }
}

Write-Host ''
Write-Host "done: $changed 项变更, $failed 项跳过" + $(if ($DryRun) { '  (dry-run)' } else { '' })
if ($failed -gt 0) { exit 1 }
exit 0
