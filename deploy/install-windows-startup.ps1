#Requires -Version 5.1
<#
.SYNOPSIS
Keep this user's WSL distribution running from Windows startup onward.
.DESCRIPTION
Run in an elevated Windows PowerShell session belonging to the Windows user
who owns the distribution. Uses S4U without storing a password. Docker must
already be enabled in WSL and the bot container must already be created.
#>
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string] $Distribution = 'Ubuntu-26.04',

    [ValidatePattern('^[a-z_][a-z0-9_-]*[$]?$')]
    [string] $LinuxUser = 'mitch',

    [ValidateNotNullOrEmpty()]
    [ValidatePattern('^[^\\/*?\[\]\x00-\x1F]+$')]
    [string] $TaskName = 'Asset Alerts - WSL',

    [switch] $IncludeLogonTrigger,

    [switch] $ReplaceExistingTask
)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$windowsPrincipal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $windowsPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this installer in an elevated Windows PowerShell session as the Windows user who owns the WSL distribution.'
}

$marker = '[asset-alerts:wsl-keepalive:v1]'
$existingTask = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
if ($existingTask -and -not ([string] $existingTask.Description).StartsWith($marker) -and -not $ReplaceExistingTask) {
    throw "Task '$TaskName' already exists and is not managed by this installer. Choose another -TaskName or explicitly use -ReplaceExistingTask."
}
if ($existingTask -and $existingTask.State -eq 'Running') {
    throw "Task '$TaskName' is running. Disable and stop that task before reinstalling it; this does not stop the Docker container."
}

$wslPath = Join-Path $env:SystemRoot 'System32\wsl.exe'
if (-not (Test-Path -LiteralPath $wslPath -PathType Leaf)) {
    throw "WSL executable not found at '$wslPath'."
}

# Confirm the current Windows account owns this distribution and Linux user.
& $wslPath --distribution $Distribution --user $LinuxUser --exec /usr/bin/true
if ($LASTEXITCODE -ne 0) {
    throw "Could not run WSL distribution '$Distribution' as Linux user '$LinuxUser'. No task was registered."
}

$ownerSid = [Security.SecurityElement]::Escape($identity.User.Value)
$command = [Security.SecurityElement]::Escape($wslPath)
$arguments = [Security.SecurityElement]::Escape("--distribution $Distribution --user $LinuxUser --exec /usr/bin/sleep infinity")
$workingDirectory = [Security.SecurityElement]::Escape((Join-Path $env:SystemRoot 'System32'))
$description = [Security.SecurityElement]::Escape("$marker Keeps $Distribution alive for Asset Alerts. Docker's restart policy controls the bot. Disable this task before intentional WSL shutdown.")
$startBoundary = (Get-Date).AddMinutes(1).ToString("yyyy-MM-dd'T'HH:mm:ss")
$logonTrigger = ''
if ($IncludeLogonTrigger) {
    $logonTrigger = @"
    <LogonTrigger>
      <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
      <Enabled>true</Enabled>
      <UserId>$ownerSid</UserId>
    </LogonTrigger>
"@
}

# A repeating time trigger also recovers after a successful WSL exit. Omitting
# Duration repeats indefinitely; IgnoreNew prevents overlapping keepalive jobs.
$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$description</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
    </BootTrigger>
    <TimeTrigger>
      <Repetition>
        <Interval>PT1M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>$startBoundary</StartBoundary>
      <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
      <Enabled>true</Enabled>
    </TimeTrigger>
$logonTrigger
  </Triggers>
  <Principals>
    <Principal id="Owner">
      <UserId>$ownerSid</UserId>
      <LogonType>S4U</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Owner">
    <Exec>
      <Command>$command</Command>
      <Arguments>$arguments</Arguments>
      <WorkingDirectory>$workingDirectory</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Xml $taskXml -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName -TaskPath '\'
$installedTask = Get-ScheduledTask -TaskName $TaskName -TaskPath '\'
[pscustomobject]@{
    Task = $installedTask.TaskName
    State = $installedTask.State
    WindowsUser = $identity.Name
    Distribution = $Distribution
    LinuxUser = $LinuxUser
    LogonType = $installedTask.Principal.LogonType
}
