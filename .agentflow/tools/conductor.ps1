<#
.SYNOPSIS
  Keep the project moving while no Orchestrator session is live: mechanical ticks, and a background Orchestrator
  session on the first tool that is not limited when a step needs judgment.

.EXAMPLE
  .agentflow\tools\conductor.ps1                 # every 3 minutes until the window is closed
  .agentflow\tools\conductor.ps1 -Once -DryRun   # one look: what would it do now? changes nothing
  .agentflow\tools\conductor.ps1 -IntervalMinutes 5 -CooldownMinutes 90
  .agentflow\tools\conductor-panel.ps1           # status window; autostart at logon, desktop shortcut

.DESCRIPTION
  Rules: .agentflow/docs/ai-handoff-protocol.md, section "Autonomous orchestration". The human starts the conductor;
  it starts background Orchestrator sessions with full access, as a Developer worker has.
  Each round:
   1. A background Orchestrator session started earlier is still running: wait for it.
   2. python .agentflow/tools/tick.py run - accepts and launches what needs no judgment (report only while an
      Orchestrator heartbeat is fresh: the human's session or a background one owns the project then).
   3. tick.json lists needs and no Orchestrator is live: start a background Orchestrator session in a visible window,
      on the first tool of AGENTFLOW_ORCHESTRATORS (default claude,codex,devin,agy) that tick.py does not know as
      limited. The same needs are not handed over again before -CooldownMinutes, unless the last session hit a limit.
   4. A Windows notification for: acceptances and launches, a background session started or finished, every tool
      limited, a change in .agentflow/state/questions.md.
  Tool command lines are the developer ones of run-task.ps1 (the Orchestrator merges and writes memory). Machine
  settings: AGENTFLOW_<TOOL> (executable), AGENTFLOW_ORCHESTRATOR_<TOOL>_ARGS (arguments for an Orchestrator session;
  default AGENTFLOW_<TOOL>_ARGS), AGENTFLOW_ORCHESTRATORS, AGENTFLOW_PYTHON.
  State: tasks\.runtime\conductor.json; session logs tasks\.runtime\orchestrator.<n>.log.
  Background Orchestrator sessions share machine_capacity.py tool slots with workers from every updated project.
#>
param(
  [double]$IntervalMinutes = 3,
  [double]$CooldownMinutes = 60,
  [switch]$Once,
  [switch]$DryRun,
  [switch]$NoNotify,
  [ValidateSet('', 'codex', 'claude', 'agy', 'devin')][string]$Orchestrate   # internal: inside the session window
)
$ErrorActionPreference = 'Stop'
$flowRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$root = (Resolve-Path (Join-Path $flowRoot '..')).Path
$rtDir = Join-Path $flowRoot 'tasks\.runtime'
$machine = Join-Path $PSScriptRoot 'machine_capacity.py'
$stateFile = Join-Path $rtDir 'conductor.json'
$questions = Join-Path $flowRoot 'state\questions.md'
# Keep the configured tool order intact. Account-level access failures use the
# same temporary-unavailability path as quotas, so the next tool takes over now
# and the preferred tool is retried after the recorded reset/backoff time.
$limitPattern = 'usage limit|rate limit|quota|session limit|hit your limit|out of credits|disabled[^\r\n]*subscription access|subscription access[^\r\n]*disabled|ask your admin to enable access'
New-Item -ItemType Directory -Force $rtDir | Out-Null
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = $OutputEncoding = [Text.UTF8Encoding]::new($false)   # tick.py prints UTF-8

if ($Orchestrate) {   # environment handed over by the conductor (a Store-packaged pwsh does not pass it to a window)
  $envFile = Join-Path $rtDir 'conductor.env.json'
  if (Test-Path $envFile) {
    $handed = Get-Content $envFile -Raw -Encoding utf8 | ConvertFrom-Json
    foreach ($p in $handed.PSObject.Properties) { Set-Item "env:$($p.Name)" $p.Value }
  }
}

function Resolve-Exe([string]$name, [string]$override) {   # same rules as run-task.ps1
  if (-not $override) { return $name }
  $m = Get-Item $override -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
  if ($m) { $m.FullName } else { $override }
}
$python = @(if ($env:AGENTFLOW_PYTHON) { Resolve-Exe 'python' $env:AGENTFLOW_PYTHON }
  elseif (($c = Get-Command python -ErrorAction SilentlyContinue) -and $c.Source -notmatch '\\WindowsApps\\') { 'python' }
  elseif (Get-Command py -ErrorAction SilentlyContinue) { 'py'; '-3' }
  else { 'python' })
function Invoke-Tick([string[]]$a) {
  $py = $python[0]; $pyArgs = @($python | Select-Object -Skip 1)
  & $py @pyArgs (Join-Path $flowRoot 'tools\tick.py') @a
}
function Invoke-Machine([string[]]$argv) {
  $py = $python[0]; $pyArgs = @($python | Select-Object -Skip 1)
  $reply = & $py @pyArgs $machine @argv 2>&1
  return [pscustomobject]@{ code = $LASTEXITCODE; text = (@($reply) -join "`n").Trim() }
}
function Invoke-Upstream([string[]]$argv) {
  $py = $python[0]; $pyArgs = @($python | Select-Object -Skip 1)
  & $py @pyArgs (Join-Path $flowRoot 'tools\upstream.py') @argv
}
function Now { [DateTime]::UtcNow.ToString('s') + 'Z' }
function Get-Utc($t) {   # pwsh 7 ConvertFrom-Json already turns "...Z" into a DateTime; re-parsing its text shifts the zone
  if ($t -is [DateTime]) { return $t.ToUniversalTime() }
  return [DateTime]::Parse([string]$t, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()
}
function Read-State { if (Test-Path $stateFile) { Get-Content $stateFile -Raw -Encoding utf8 | ConvertFrom-Json } else { [pscustomobject]@{} } }
function Write-State($s) {
  $tmp = "$stateFile.tmp"
  [IO.File]::WriteAllText($tmp, ($s | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
  Move-Item -Force $tmp $stateFile
}
function Set-Prop($o, [string]$name, $value) { $o | Add-Member -NotePropertyName $name -NotePropertyValue $value -Force }
function Test-Alive($s) {
  if (-not $s -or -not $s.pid -or $s.finishedAt) { return $false }
  $p = Get-Process -Id $s.pid -ErrorAction SilentlyContinue
  return [bool]($p -and [long]$p.StartTime.ToUniversalTime().Ticks -eq [long]$s.pidStart)   # pid reuse guard
}
function Get-Hash([string]$text) {
  $b = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($text))
  return ([BitConverter]::ToString($b) -replace '-', '').Substring(0, 16)
}

function Send-Notice([string]$title, [string]$text) {   # Windows notification; never stops the conductor
  Write-Host "[$((Get-Date).ToString('HH:mm'))] $title - $text"
  if ($NoNotify -or $DryRun) { return }
  try {
    $t = $title.Replace("'", "''"); $x = $text.Replace("'", "''")
    if ($x.Length -gt 250) { $x = $x.Substring(0, 247) + '...' }
    $script = "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; `$n = New-Object System.Windows.Forms.NotifyIcon; " +
      "`$n.Icon = [System.Drawing.SystemIcons]::Information; `$n.Visible = `$true; `$n.ShowBalloonTip(10000, '$t', '$x', 'Info'); " +
      "Start-Sleep -Seconds 12; `$n.Dispose()"
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-EncodedCommand', $enc | Out-Null
  } catch {
    try { msg $env:USERNAME /TIME:60 "$title - $text" } catch { Write-Warning "notification failed: $($_.Exception.Message)" }
  }
}

function Get-OrchestratorArgv([string]$tool, [string]$prompt) {   # developer command lines of run-task.ps1
  $own = [Environment]::GetEnvironmentVariable("AGENTFLOW_ORCHESTRATOR_$($tool.ToUpper())_ARGS")
  $common = [Environment]::GetEnvironmentVariable("AGENTFLOW_$($tool.ToUpper())_ARGS")
  $extra = @(if ($own) { $own.Trim() -split '\s+' } elseif ($common) { $common.Trim() -split '\s+' })
  switch ($tool) {
    'codex' { return @('exec') + $extra + @('--sandbox', 'danger-full-access', $prompt) }
    'claude' { return @('-p', $prompt, '--dangerously-skip-permissions') + $extra }
    'agy' { return @('-p', $prompt, '--dangerously-skip-permissions', '--output-format', 'stream-json') + $extra }
    'devin' { return @('-p', $prompt, '--permission-mode', 'dangerous', '--respect-workspace-trust', 'false') + $extra }
  }
}

# --- -Orchestrate: one background Orchestrator session, inside its own window --------------------------------------
if ($Orchestrate) {
  [Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
  try {   # QuickEdit off: a click in the console would pause the session (same as run-task.ps1)
    Add-Type -Namespace AgentFlow -Name Console -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern System.IntPtr GetStdHandle(int h);
[DllImport("kernel32.dll")] public static extern bool GetConsoleMode(System.IntPtr h, out uint m);
[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(System.IntPtr h, uint m);
'@
    $hIn = [AgentFlow.Console]::GetStdHandle(-10); $mode = 0
    if ([AgentFlow.Console]::GetConsoleMode($hIn, [ref]$mode)) { [void][AgentFlow.Console]::SetConsoleMode($hIn, ($mode -band (-bnot 0x40)) -bor 0x80) }
  } catch {}
  $ErrorActionPreference = 'Continue'   # native stderr must not abort the session
  $holder = "$Orchestrate-background"
  $s = Read-State; $log = $s.session.log
  $prompt = "You are the Orchestrator in background mode, holder name '$holder'. Your role: .agentflow/roles/orchestrator.md. " +
    "First read .agentflow/docs/ai-handoff-protocol.md, section 'Autonomous orchestration': its limits override everything else. " +
    "The conductor started you because no Orchestrator was live; the open needs are in .agentflow/tasks/.runtime/tick.json."
  $code = 1
  try {
    if ($s.session.machineLease) {
      $started = [long](Get-Process -Id $PID).StartTime.ToUniversalTime().Ticks
      $activation = Invoke-Machine @('activate', $s.session.machineLease, "$PID", '--pid-start', "$started")
      if ($activation.code) { throw "machine capacity activation failed: $($activation.text)" }
    }
    Set-Location -LiteralPath $root
    $exe = Resolve-Exe $Orchestrate ([Environment]::GetEnvironmentVariable("AGENTFLOW_$($Orchestrate.ToUpper())"))
    $argv = Get-OrchestratorArgv $Orchestrate $prompt
    & $exe @argv 2>&1 | Tee-Object -FilePath $log -Append
    $code = $LASTEXITCODE
  } catch { "launch error: $_" | Tee-Object -FilePath $log -Append }
  $tail = if (Test-Path $log) { (Get-Content $log -Tail 50) -join "`n" } else { '' }
  $limit = [bool]($tail -match $limitPattern)
  if ($limit) { Invoke-Tick @('limit', $Orchestrate, $tail) | Out-Null }
  Invoke-Tick @('release', '--holder', $holder) | Out-Null
  $s = Read-State
  Set-Prop $s.session 'exitCode' $code; Set-Prop $s.session 'limitHit' $limit; Set-Prop $s.session 'finishedAt' (Now)
  Write-State $s
  if ($s.session.machineLease) { [void](Invoke-Machine @('release', $s.session.machineLease)) }
  Write-Host "`nbackground Orchestrator ($Orchestrate) finished, exit $code$(if ($limit) { ', usage limit' })."
  return   # the window closes here
}

function Start-Orchestrator([string]$tool, $state, [string]$why) {
  $launcherStart = [long](Get-Process -Id $PID).StartTime.ToUniversalTime().Ticks
  $claim = Invoke-Machine @('claim', $tool, $root, 'orchestrator', '--pid', "$PID", '--pid-start', "$launcherStart")
  if ($claim.code -eq 4) { Write-Host "  waiting for shared machine capacity: $($claim.text)"; return $null }
  if ($claim.code) { throw "machine capacity failed: $($claim.text)" }
  $leaseToken = $claim.text
  try {
  $n = [int]($state.sessions) + 1
  $handover = [ordered]@{ PATH = $env:PATH }
  Get-ChildItem env: | Where-Object { $_.Name -like 'AGENTFLOW_*' } | ForEach-Object { $handover[$_.Name] = $_.Value }
  [IO.File]::WriteAllText((Join-Path $rtDir 'conductor.env.json'), ($handover | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
  $session = [pscustomobject][ordered]@{ n = $n; tool = $tool; pid = $null; pidStart = $null; startedAt = (Now)
    finishedAt = $null; exitCode = $null; limitHit = $false; log = (Join-Path $rtDir "orchestrator.$n.log")
    machineLease = $leaseToken; why = $why }
  Set-Prop $state 'session' $session; Set-Prop $state 'sessions' $n
  Write-State $state
  $ps = if (Get-Command pwsh -ErrorAction SilentlyContinue) { 'pwsh' } else { 'powershell' }
  $p = Start-Process $ps -PassThru -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Orchestrate', $tool
  $wp = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
  $state = Read-State
  if ($wp -and -not $state.session.finishedAt) {
    Set-Prop $state.session 'pid' $p.Id; Set-Prop $state.session 'pidStart' ([long]$wp.StartTime.ToUniversalTime().Ticks)
    Write-State $state
  }
  return $state
  } catch {
    [void](Invoke-Machine @('release', $leaseToken))
    throw
  }
}

# --- one conductor per project: a second one (the logon task, another window) exits at once. The name is shared with
# conductor-panel.ps1, which reads it to tell whether the conductor runs. A dry run only looks and needs no lock.
if (-not $DryRun) {
  $mutex = [Threading.Mutex]::new($false, "Local\AgentFlow-Conductor-$(Get-Hash $root.ToLowerInvariant())")
  try { $own = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $own = $true }   # the last one crashed
  if (-not $own) { Write-Host "conductor for $root is already running: this one exits."; return }
  $me = Get-Process -Id $PID
  $state = Read-State
  Set-Prop $state 'conductor' ([pscustomobject][ordered]@{ pid = $PID; pidStart = [long]$me.StartTime.ToUniversalTime().Ticks
    startedAt = (Now); lastRound = $null; intervalMinutes = $IntervalMinutes })
  Write-State $state
}

# --- the loop --------------------------------------------------------------------------------------------------------
Write-Host "conductor: $root, every $IntervalMinutes min$(if ($DryRun) { ', dry run' }). Close the window to stop."
while ($true) {
  try {
    $state = Read-State
    $s = $state.session
    if ($s -and $s.finishedAt -and $state.reportedFinish -ne $s.n) {
      Send-Notice 'AgentFlow' ("Background Orchestrator ($($s.tool)) finished" + $(if ($s.limitHit) { ': usage limit, the next tool takes over' } else { " (exit $($s.exitCode))" }))
      Set-Prop $state 'reportedFinish' $s.n; Write-State $state
    }
    if (Test-Alive $s) {
      Write-Host "[$((Get-Date).ToString('HH:mm'))] background Orchestrator $($s.tool) is working (pid $($s.pid))"
    } else {
      if ($s -and -not $s.finishedAt -and -not $DryRun) {   # window closed without a final state
        Set-Prop $s 'finishedAt' (Now); Set-Prop $s 'exitCode' -1; Set-Prop $state 'reportedFinish' $s.n; Write-State $state
        if ($s.machineLease) { [void](Invoke-Machine @('release', $s.machineLease)) }
        Invoke-Tick @('release', '--holder', "$($s.tool)-background") | Out-Null
      }
      $tickArgs = @('run', '--json') + $(if ($DryRun) { @('--dry-run') } else { @() })
      $rep = (Invoke-Tick $tickArgs) -join "`n" | ConvertFrom-Json
      if ($rep.skipped) { Write-Host "tick skipped: $($rep.skipped)" }
      else {
        $acted = @($rep.acted | ForEach-Object { "$($_.action) $($_.task)" })
        if ($acted.Count) { Send-Notice 'AgentFlow' ($acted -join ', ') }
        foreach ($w in @($rep.would)) { Write-Host "  would $($w.action) $($w.task): $($w.detail)" }
        foreach ($w in @($rep.waits)) { Write-Host "  wait $($w.task): $($w.detail)" }
        $needs = @($rep.needs)
        foreach ($x in $needs) { Write-Host "  need $($x.task): $($x.detail)" }
        if ($rep.live) { Write-Host "  Orchestrator $($rep.live.holder) is live (heartbeat $($rep.live.at))" }
        elseif ($needs.Count) {
          $key = Get-Hash (($needs | ForEach-Object { "$($_.task)|$($_.detail)" } | Sort-Object) -join "`n")
          $since = if ($s -and $s.startedAt) { ([DateTime]::UtcNow - (Get-Utc $s.startedAt)).TotalMinutes } else { [double]::MaxValue }
          $fresh = $key -ne $state.needsKey -or $since -ge $CooldownMinutes -or ($s -and $s.limitHit)
          $tool = ((Invoke-Tick @('next-orchestrator')) -join '').Trim()
          if (-not $fresh) {
            Write-Host "  the same needs were handed over $([int]$since) min ago: waiting (cooldown $CooldownMinutes min; see questions.md)"
          } elseif (-not $tool) {
            if ($state.allLimitedKey -ne $key) {
              $lim = @($rep.limited.PSObject.Properties | ForEach-Object { "$($_.Name) until $($_.Value)" }) -join ', '
              $reason = if ($lim) { "No Orchestrator tool is free. $lim" } else { 'Orchestrator tools are busy in other AgentFlow projects; retry next round' }
              Send-Notice 'AgentFlow: all tools limited' $reason
              if (-not $DryRun) { Set-Prop $state 'allLimitedKey' $key; Write-State $state }
            }
          } elseif ($DryRun) {
            Write-Host "  would start a background Orchestrator on $tool for $($needs.Count) need(s)"
          } else {
            $why = ($needs | Select-Object -First 3 | ForEach-Object { "$($_.task): $($_.detail)" }) -join '; '
            $tried = @()
            while ($tool -and $tried.Count -lt 4 -and $tool -notin $tried) {
              $started = Start-Orchestrator $tool $state $why
              if ($started) {
                $state = $started
                Set-Prop $state 'needsKey' $key; Write-State $state
                Send-Notice 'AgentFlow: Orchestrator handed over' "$tool took over $($needs.Count) step(s): $why"
                break
              }
              $tried += $tool
              $tool = ((Invoke-Tick @('next-orchestrator', '--skip', ($tried -join ','))) -join '').Trim()
            }
          }
        }
      }
    }
    if ((Test-Path $questions) -and -not $DryRun) {
      $qh = Get-Hash (Get-Content $questions -Raw -Encoding utf8)
      $state = Read-State
      if ($state.questionsHash -and $state.questionsHash -ne $qh) {
        Send-Notice 'AgentFlow: a question for you' 'New entry in .agentflow/state/questions.md'
      }
      if ($state.questionsHash -ne $qh) { Set-Prop $state 'questionsHash' $qh; Write-State $state }
    }
    if (-not $DryRun) {   # once a day: does this project drift from its recorded template?
      $today = [DateTime]::UtcNow.ToString('yyyy-MM-dd')
      $state = Read-State
      if ($state.upstreamCheck -ne $today -and (Test-Path (Join-Path $flowRoot 'template-source.json')) -and
          (Test-Path (Join-Path $flowRoot 'tools\upstream.py'))) {
        Set-Prop $state 'upstreamCheck' $today; Write-State $state
        Invoke-Upstream @('--check', '--project', $root) | Out-Null
        if ($LASTEXITCODE -eq 1) {
          Send-Notice 'AgentFlow: template drift' 'This project differs from its AgentFlow template; run .agentflow\tools\upstream.py for the report'
        }
      }
    }
    if (-not $DryRun) {   # the panel shows when the conductor last looked
      $state = Read-State
      if ($state.conductor) { Set-Prop $state.conductor 'lastRound' (Now); Write-State $state }
    }
  } catch {
    Write-Warning "conductor round failed: $($_.Exception.Message)"
  }
  if ($Once) { break }
  Start-Sleep -Seconds ([int]($IntervalMinutes * 60))
}
