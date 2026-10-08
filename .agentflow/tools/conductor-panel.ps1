<#
.SYNOPSIS
  The human's window onto the conductor: is it running, does it start at logon, who orchestrates, limits, questions.

.EXAMPLE
  .agentflow\tools\conductor-panel.ps1             # the window (also what the desktop shortcut opens)
  .agentflow\tools\conductor-panel.ps1 -Install    # autostart at logon + desktop shortcut + start the conductor now
  .agentflow\tools\conductor-panel.ps1 -Status     # one line each; exit 0 when the conductor runs
  .agentflow\tools\conductor-panel.ps1 -Start      # start the conductor (minimized window) unless it runs
  .agentflow\tools\conductor-panel.ps1 -Register | -Unregister | -Shortcut | -Stop

.DESCRIPTION
  Rules: .agentflow/docs/ai-handoff-protocol.md, section "Autonomous orchestration". Autostart is a Windows Task
  Scheduler task in the folder \AgentFlow\ for the current user, at logon, without a time limit; the conductor itself
  refuses a second instance. Runs in Windows PowerShell 5.1 and PowerShell 7. The window is in Russian: it is for the
  human. It reads .agentflow/tasks/.runtime/*.json and changes nothing but the conductor process and the task.
#>
param([switch]$Install, [switch]$Status, [switch]$Start, [switch]$Stop, [switch]$Register, [switch]$Unregister, [switch]$Shortcut)
$ErrorActionPreference = 'Stop'
$flowRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$root = (Resolve-Path (Join-Path $flowRoot '..')).Path
$rtDir = Join-Path $flowRoot 'tasks\.runtime'
$conductor = Join-Path $PSScriptRoot 'conductor.ps1'
$questions = Join-Path $flowRoot 'state\questions.md'

function Get-Hash([string]$text) {   # same as conductor.ps1
  $b = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($text))
  return ([BitConverter]::ToString($b) -replace '-', '').Substring(0, 16)
}
$rootHash = Get-Hash $root.ToLowerInvariant()
$mutexName = "Local\AgentFlow-Conductor-$rootHash"
$leaf = Split-Path $root -Leaf
$project = if ($leaf -eq 'code') { Split-Path (Split-Path $root) -Leaf } else { $leaf }   # <Project>\code layout
$taskPath = '\AgentFlow\'
$taskName = "Conductor $project $($rootHash.Substring(0, 6))"
$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $pwsh) { $pwsh = (Get-Command powershell).Source }
$stale = if ($env:AGENTFLOW_ORCHESTRATOR_STALE_MINUTES) { [int]$env:AGENTFLOW_ORCHESTRATOR_STALE_MINUTES } else { 20 }

function Read-Json([string]$name) {
  $p = Join-Path $rtDir $name
  if (-not (Test-Path $p)) { return $null }
  try { return Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}
function Get-Local($t) {   # pwsh 7 turns "...Z" into a DateTime, 5.1 keeps the string
  if (-not $t) { return $null }
  if ($t -is [DateTime]) { return $t.ToLocalTime() }
  try { return [DateTime]::Parse([string]$t, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind).ToLocalTime() } catch { return $null }
}
function Format-Time($t) {
  $d = Get-Local $t
  if (-not $d) { return '?' }
  if ($d.Date -eq (Get-Date).Date) { return $d.ToString('HH:mm') }
  return $d.ToString('dd.MM HH:mm')
}
function Test-Running {
  $m = $null
  try { $ok = [Threading.Mutex]::TryOpenExisting($mutexName, [ref]$m) } catch [UnauthorizedAccessException] { return $true }
  if ($m) { $m.Dispose() }
  return $ok
}
function Test-ProcessIs($procId, $start) {   # pid reuse guard, as in run-task.ps1
  if (-not $procId) { return $false }
  $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
  return [bool]($p -and [long]$p.StartTime.ToUniversalTime().Ticks -eq [long]$start)
}
function Get-Autostart { Get-ScheduledTask -TaskPath $taskPath -TaskName $taskName -ErrorAction SilentlyContinue }

function Invoke-Register {
  $user = "$env:USERDOMAIN\$env:USERNAME"
  $action = New-ScheduledTaskAction -Execute $pwsh -WorkingDirectory $root `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$conductor`""
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
  $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
  $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskPath $taskPath -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "AgentFlow conductor for $root (.agentflow\tools\conductor-panel.ps1)" -Force | Out-Null
  return "Автозапуск включён: Планировщик заданий > AgentFlow > $taskName"
}
function Invoke-Unregister {
  if (Get-Autostart) { Unregister-ScheduledTask -TaskPath $taskPath -TaskName $taskName -Confirm:$false }
  return 'Автозапуск выключен (сторож, если работает, продолжает до закрытия окна)'
}
function Invoke-Start {
  if (Test-Running) { return 'Сторож уже работает' }
  Start-Process $pwsh -WorkingDirectory $root -WindowStyle Minimized `
    -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$conductor`"" | Out-Null
  return 'Сторож запущен (свёрнутое окно на панели задач)'
}
function Invoke-Stop {
  $c = (Read-Json 'conductor.json').conductor
  if ($c -and (Test-ProcessIs $c.pid $c.pidStart)) {
    Stop-Process -Id $c.pid -Force
    return 'Сторож остановлен. Фоновая сессия оркестратора, если идёт, доработает в своём окне'
  }
  return 'Сторож не работает'
}
function Invoke-Shortcut {
  $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) "AgentFlow - $project.lnk"
  $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
  $sc.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
  $sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`""
  $sc.WorkingDirectory = $root
  $sc.IconLocation = (Join-Path $env:SystemRoot 'System32\shell32.dll') + ',21'
  $sc.Description = "AgentFlow: сторож и оркестрация проекта $project"
  $sc.Save()
  return "Ярлык на рабочем столе: $lnk"
}

# --- what the window and -Status show: [name, text, state] with state ok | off | warn | none
function Get-Rows {
  $rows = @()
  $a = Get-Autostart
  $rows += , @('Автозапуск при входе', $(if ($a) { "включён ($($a.State))" } else { 'выключен' }), $(if ($a) { 'ok' } else { 'warn' }))
  $cs = Read-Json 'conductor.json'
  $c = $cs.conductor
  if (Test-Running) {
    $txt = 'работает'
    if ($c) { $txt += " с $(Format-Time $c.startedAt), последний круг $(Format-Time $c.lastRound)" }
    $rows += , @('Сторож', $txt, 'ok')
  } else { $rows += , @('Сторож', 'не работает: проект без оркестратора стоит', 'warn') }
  $hb = Read-Json 'orchestrator.json'
  $at = Get-Local $hb.at
  if ($hb -and $at -and ((Get-Date) - $at).TotalMinutes -lt $stale) {
    $rows += , @('Оркестратор', "$($hb.holder), отметка $([int]((Get-Date) - $at).TotalMinutes) мин назад", 'ok')
  } elseif ($hb -and $at) { $rows += , @('Оркестратор', "нет (последний: $($hb.holder), $(Format-Time $hb.at))", 'none') }
  else { $rows += , @('Оркестратор', 'нет', 'none') }
  $s = $cs.session
  if ($s -and -not $s.finishedAt -and (Test-ProcessIs $s.pid $s.pidStart)) {
    $rows += , @('Фоновая сессия', "$($s.tool) работает с $(Format-Time $s.startedAt)", 'ok')
  } elseif ($s) {
    $end = if ($s.limitHit) { 'упёрлась в лимит' } else { "код $($s.exitCode)" }
    $rows += , @('Фоновая сессия', "последняя: $($s.tool), $(Format-Time $s.startedAt) - $(Format-Time $s.finishedAt), $end", 'none')
  } else { $rows += , @('Фоновая сессия', 'не было', 'none') }
  $t = Read-Json 'tick.json'
  if ($t) {
    $rows += , @('Последний шаг', ("$(Format-Time $t.at): сделано $(@($t.acted).Count), нужно решение $(@($t.needs).Count), ждут $(@($t.waits).Count)"),
      $(if (@($t.needs).Count) { 'warn' } else { 'ok' }))
  } else { $rows += , @('Последний шаг', 'ещё не было', 'none') }
  $lim = Read-Json 'tool-limits.json'
  $active = @()
  if ($lim) {
    foreach ($p in $lim.PSObject.Properties) {
      if ($p.Name -eq '_seen') { continue }
      $u = Get-Local $p.Value.until
      if ($u -and $u -gt (Get-Date)) { $active += "$($p.Name) до $(Format-Time $p.Value.until)" }
    }
  }
  $rows += , @('Лимиты инструментов', $(if ($active.Count) { $active -join ', ' } else { 'нет' }), $(if ($active.Count) { 'warn' } else { 'ok' }))
  $q = 0
  if (Test-Path $questions) { $q = @(Get-Content $questions -Encoding UTF8 | Where-Object { $_ -match '^##\s' }).Count }
  $rows += , @('Вопросы к тебе', $(if ($q) { "$q - кнопка «Вопросы»" } else { 'нет' }), $(if ($q) { 'warn' } else { 'ok' }))
  return , $rows
}
function Get-Details {
  $t = Read-Json 'tick.json'
  if (-not $t) { return 'Сторож ещё не делал шагов.' }
  $out = @()
  foreach ($x in @($t.acted)) { $out += "сделано  $($x.task): $($x.action) $($x.detail)" }
  foreach ($x in @($t.needs)) { $out += "решение  $($x.task): $($x.detail)" }
  foreach ($x in @($t.waits)) { $out += "ждёт     $($x.task): $($x.detail)" }
  if (-not $out.Count) { $out += 'Открытых задач нет.' }
  return ("Шаг $(Format-Time $t.at):`r`n" + ($out -join "`r`n"))
}

# --- command line ------------------------------------------------------------------------------------------------------
if ($Install) { Invoke-Register; Invoke-Shortcut; Invoke-Start; return }
if ($Register) { Invoke-Register; return }
if ($Unregister) { Invoke-Unregister; return }
if ($Shortcut) { Invoke-Shortcut; return }
if ($Start) { Invoke-Start; return }
if ($Stop) { Invoke-Stop; return }
if ($Status) {
  try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch {}
  foreach ($r in (Get-Rows)) { "{0}: {1}" -f $r[0], $r[1] }
  if (Test-Running) { exit 0 } else { exit 1 }
}

# --- the window ----------------------------------------------------------------------------------------------------------
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$font = New-Object Drawing.Font('Segoe UI', 10)
$colors = @{ ok = [Drawing.Color]::FromArgb(22, 128, 61); warn = [Drawing.Color]::FromArgb(185, 28, 28); off = [Drawing.Color]::Gray; none = [Drawing.Color]::FromArgb(90, 90, 90) }

$form = New-Object Windows.Forms.Form
$form.Text = "AgentFlow - $project"
$form.Font = $font
$form.StartPosition = 'CenterScreen'
$form.ClientSize = New-Object Drawing.Size(760, 600)
$form.MinimumSize = New-Object Drawing.Size(640, 520)
$form.BackColor = [Drawing.Color]::White

$title = New-Object Windows.Forms.Label
$title.Text = "Сторож проекта $project"
$title.Font = New-Object Drawing.Font('Segoe UI Semibold', 14)
$title.AutoSize = $true
$title.Location = New-Object Drawing.Point(16, 12)
$form.Controls.Add($title)
$sub = New-Object Windows.Forms.Label
$sub.Text = $root
$sub.ForeColor = [Drawing.Color]::Gray
$sub.AutoSize = $true
$sub.Location = New-Object Drawing.Point(18, 44)
$form.Controls.Add($sub)

$grid = New-Object Windows.Forms.TableLayoutPanel
$grid.Location = New-Object Drawing.Point(16, 72)
$grid.Size = New-Object Drawing.Size(728, 230)
$grid.Anchor = 'Top, Left, Right'
$grid.ColumnCount = 2
[void]$grid.ColumnStyles.Add((New-Object Windows.Forms.ColumnStyle('Absolute', 200)))
[void]$grid.ColumnStyles.Add((New-Object Windows.Forms.ColumnStyle('Percent', 100)))
$form.Controls.Add($grid)
$valueLabels = @()
foreach ($i in 0..6) {
  $n = New-Object Windows.Forms.Label
  $n.AutoSize = $true; $n.Margin = New-Object Windows.Forms.Padding(0, 6, 8, 6); $n.ForeColor = [Drawing.Color]::FromArgb(60, 60, 60)
  $v = New-Object Windows.Forms.Label
  $v.AutoSize = $true; $v.Margin = New-Object Windows.Forms.Padding(0, 6, 0, 6); $v.MaximumSize = New-Object Drawing.Size(520, 0)
  $grid.Controls.Add($n, 0, $i); $grid.Controls.Add($v, 1, $i)
  $valueLabels += , @($n, $v)
}

$buttons = New-Object Windows.Forms.FlowLayoutPanel
$buttons.Location = New-Object Drawing.Point(16, 306)
$buttons.Size = New-Object Drawing.Size(728, 76)
$buttons.Anchor = 'Top, Left, Right'
$form.Controls.Add($buttons)
function New-Button([string]$text, [scriptblock]$onClick) {
  $b = New-Object Windows.Forms.Button
  $b.Text = $text; $b.AutoSize = $true; $b.Padding = New-Object Windows.Forms.Padding(6, 2, 6, 2)
  $b.Add_Click($onClick)
  $buttons.Controls.Add($b)
  return $b
}

$details = New-Object Windows.Forms.TextBox
$details.Multiline = $true; $details.ReadOnly = $true; $details.ScrollBars = 'Vertical'
$details.Font = New-Object Drawing.Font('Consolas', 9.5)
$details.BackColor = [Drawing.Color]::FromArgb(248, 248, 248)
$details.Location = New-Object Drawing.Point(16, 388)
$details.Size = New-Object Drawing.Size(728, 196)
$details.Anchor = 'Top, Bottom, Left, Right'
$form.Controls.Add($details)
$script:message = $null

function Update-View {
  $rows = Get-Rows
  for ($i = 0; $i -lt $rows.Count; $i++) {
    $valueLabels[$i][0].Text = $rows[$i][0]
    $valueLabels[$i][1].Text = $rows[$i][1]
    $valueLabels[$i][1].ForeColor = $colors[$rows[$i][2]]
  }
  $btnAuto.Text = if (Get-Autostart) { 'Выключить автозапуск' } else { 'Включить автозапуск' }
  $btnRun.Text = if (Test-Running) { 'Остановить сторожа' } else { 'Запустить сторожа' }
  $text = Get-Details
  if ($script:message) { $text = $script:message + "`r`n`r`n" + $text }
  if ($details.Text -ne $text) { $details.Text = $text }
}
function Invoke-Action([scriptblock]$what) {
  try { $script:message = (& $what) -join "`r`n" } catch { $script:message = "Ошибка: $($_.Exception.Message)" }
  Update-View
}

$btnAuto = New-Button 'Автозапуск' { Invoke-Action { if (Get-Autostart) { Invoke-Unregister } else { Invoke-Register } } }
$btnRun = New-Button 'Сторож' {
  Invoke-Action {
    if (Test-Running) {
      $ok = [Windows.Forms.MessageBox]::Show('Остановить сторожа? Пока он не работает, никто не подхватит проект, если оркестратор упрётся в лимит.', 'AgentFlow', 'YesNo', 'Question')
      if ($ok -eq 'Yes') { Invoke-Stop } else { 'Сторож работает дальше' }
    } else { Invoke-Start }
  }
}
[void](New-Button 'Проверить сейчас' {
  Invoke-Action {
    $form.Cursor = 'WaitCursor'
    try { $o = & $pwsh -NoProfile -ExecutionPolicy Bypass -File $conductor -Once -DryRun 2>&1 | Out-String } finally { $form.Cursor = 'Default' }
    "Что сторож сделал бы сейчас (ничего не изменено):`r`n" + $o.Trim()
  }
})
[void](New-Button 'Вопросы' { if (-not (Test-Path $questions)) { New-Item -ItemType File $questions | Out-Null }; Start-Process notepad.exe -ArgumentList "`"$questions`"" })
[void](New-Button 'Журнал сессии' {
  $s = (Read-Json 'conductor.json').session
  if ($s -and $s.log -and (Test-Path $s.log)) { Start-Process notepad.exe -ArgumentList "`"$($s.log)`"" }
  else { Invoke-Action { 'Фоновых сессий ещё не было' } }
})
[void](New-Button 'Папка состояния' { Start-Process explorer.exe -ArgumentList "`"$rtDir`"" })
[void](New-Button 'Планировщик' { Start-Process taskschd.msc })

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 5000
$timer.Add_Tick({ try { Update-View } catch {} })
$form.Add_Shown({ Update-View; $timer.Start() })
$form.Add_FormClosed({ $timer.Stop() })
[void]$form.ShowDialog()
