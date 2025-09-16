#Requires -Version 5.1
<#
.SYNOPSIS
    UV + Conda/Mamba Environment Hook Injector for Windows PowerShell.
.DESCRIPTION
    This script injects a hook into the PowerShell profile to automatically
    sync the UV_PROJECT_ENVIRONMENT variable with the active Conda/Mamba
    environment. It is idempotent and safe to run multiple times.
.PARAMETER IgnoreBase
    Exclude conda's default 'base' environment from UV sync
.PARAMETER Help
    Show help message and exit
.EXAMPLE
    .\setup_hooks.ps1
    Setup hooks with default behavior (includes base environment)
.EXAMPLE
    .\setup_hooks.ps1 -IgnoreBase
    Setup hooks excluding base environment from sync
.NOTES
    Author: Wangnov
#>

[CmdletBinding()]
param(
    [switch]$IgnoreBase,
    [switch]$Help
)

function Show-Help {
    Write-Host @"
Usage: .\setup_hooks.ps1 [OPTIONS]

This script sets up hooks to sync UV_PROJECT_ENVIRONMENT with active Conda/Mamba environments.

OPTIONS:
    -IgnoreBase      Exclude conda's default 'base' environment from UV sync
    -Help            Show this help message and exit

EXAMPLES:
    .\setup_hooks.ps1                Setup hooks with default behavior (includes base environment)
    .\setup_hooks.ps1 -IgnoreBase    Setup hooks excluding base environment from sync

"@
}

if ($Help) {
    Show-Help
    exit 0
}

function Inject-UvCondaHook {
    [CmdletBinding()]
    param(
        [bool]$IgnoreBase = $false
    )

    Write-Host "› Setting up UV + Conda/Mamba Hook for PowerShell..."
    if ($IgnoreBase) {
        Write-Host "› Configured to ignore 'base' environment"
    }

    # Determine the correct profile path. $PROFILE is the simplest way.
    $configFile = $PROFILE
    $startMarker = "# UV-CONDA-HOOK-POWERSHELL-START"
    $endMarker = "# UV-CONDA-HOOK-POWERSHELL-END"

    # Ensure the directory for the profile exists.
    $configDir = Split-Path -Path $configFile -Parent
    if (-not (Test-Path -Path $configDir)) {
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    }
    # Ensure the profile file itself exists.
    if (-not (Test-Path -Path $configFile)) {
        New-Item -ItemType File -Path $configFile -Force | Out-Null
    }

    # Remove existing hook code block if present
    if (Select-String -Path $configFile -Pattern $startMarker -Quiet) {
        Write-Host "› Removing existing PowerShell hook from $configFile..."
        $content = Get-Content -Path $configFile
        $newContent = @()
        $inHookBlock = $false
        
        foreach ($line in $content) {
            if ($line -match [regex]::Escape($startMarker)) {
                $inHookBlock = $true
                continue
            }
            if ($line -match [regex]::Escape($endMarker)) {
                $inHookBlock = $false
                continue
            }
            if (-not $inHookBlock) {
                $newContent += $line
            }
        }
        
        Set-Content -Path $configFile -Value $newContent
        Write-Host "› Existing hook removed successfully."
    }

    Write-Host "› Injecting PowerShell hook into $configFile..."
    
    # Generate hook code based on ignore_base setting
    if ($IgnoreBase) {
        $hookCondition = @"
    if ((Test-Path Env:CONDA_PREFIX) -and ((Split-Path -Leaf `$env:CONDA_PREFIX) -ne "base")) {
        if (-not (Test-Path Env:UV_PROJECT_ENVIRONMENT) -or (`$env:UV_PROJECT_ENVIRONMENT -ne `$env:CONDA_PREFIX)) {
            `$env:UV_PROJECT_ENVIRONMENT = `$env:CONDA_PREFIX
        }
    } else {
        if (Test-Path Env:UV_PROJECT_ENVIRONMENT) {
            Remove-Item -ErrorAction SilentlyContinue Env:UV_PROJECT_ENVIRONMENT
        }
    }
"@
    } else {
        $hookCondition = @"
    if (Test-Path Env:CONDA_PREFIX) {
        if (-not (Test-Path Env:UV_PROJECT_ENVIRONMENT) -or (`$env:UV_PROJECT_ENVIRONMENT -ne `$env:CONDA_PREFIX)) {
            `$env:UV_PROJECT_ENVIRONMENT = `$env:CONDA_PREFIX
        }
    } else {
        if (Test-Path Env:UV_PROJECT_ENVIRONMENT) {
            Remove-Item -ErrorAction SilentlyContinue Env:UV_PROJECT_ENVIRONMENT
        }
    }
"@
    }
    
    # Define the code block to be injected.
    $codeBlock = @"

$startMarker
# Auto-sync UV_PROJECT_ENVIRONMENT with Conda/Mamba environment (PowerShell).
# This function is registered to run before each prompt is displayed.
# ignore_base=$IgnoreBase
Register-EngineEvent -SourceIdentifier PowerShell.OnIdle -Action {
$hookCondition}
$endMarker
"@
    
    # Append the code block to the profile file.
    Add-Content -Path $configFile -Value $codeBlock
    Write-Host "› Successfully injected hook for PowerShell."
}

# --- Main Execution ---
Write-Host "--- Setting up UV + Conda/Mamba Hooks for Windows ---"
Inject-UvCondaHook -IgnoreBase $IgnoreBase
Write-Host "---------------------------------------------------"
Write-Host "Setup complete. Please restart your PowerShell session to apply changes."