# Retries Oracle VM.Standard.A1.Flex launch until ARM capacity is available.
# Logs to retry_launch.log in the same directory.
# Run: powershell -ExecutionPolicy Bypass -File infra/oracle/retry_launch.ps1

$OCI = "C:\Users\grego\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\Scripts\oci.exe"
$TENANCY = "ocid1.tenancy.oc1..aaaaaaaadkpmsjveqnpao2f5riexsrollguinxvtph5y76pvszhl6rl74ksq"
$SUBNET   = "ocid1.subnet.oc1.eu-paris-1.aaaaaaaayd44efl5maudkiz5of5hc5duyfkqaybuq4kezq2phqup43kuzira"
$IMAGE    = "ocid1.image.oc1.eu-paris-1.aaaaaaaay7dy7prlkadomnftjiujye75q2mgdha4ypaw6jonnc6sogmefkoa"
$SSH_KEY  = "$env:USERPROFILE\.ssh\notiftk_oracle.pub"

$shapeFile = "$env:TEMP\notiftk_shape.json"
Set-Content -Path $shapeFile -Value '{"ocpus": 2, "memoryInGBs": 12}' -Encoding ASCII -NoNewline

$logFile = "$PSScriptRoot\retry_launch.log"

function Log {
    param([string]$msg)
    $line = "$(Get-Date -Format 'HH:mm:ss') $msg"
    Write-Output $line
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

$attempt    = 0
$maxAttempts = 200

while ($attempt -lt $maxAttempts) {
    $attempt++
    Log "Attempt $attempt - launching VM.Standard.A1.Flex..."

    $tmpOut = "$env:TEMP\oci_launch_out_$attempt.txt"
    $tmpErr = "$env:TEMP\oci_launch_err_$attempt.txt"
    $proc = Start-Process -FilePath $OCI `
        -ArgumentList @(
            "compute", "instance", "launch",
            "--compartment-id", $TENANCY,
            "--availability-domain", "DuqC:EU-PARIS-1-AD-1",
            "--display-name", "notiftk",
            "--image-id", $IMAGE,
            "--shape", "VM.Standard.A1.Flex",
            "--shape-config", "file://$shapeFile",
            "--subnet-id", $SUBNET,
            "--assign-public-ip", "true",
            "--ssh-authorized-keys-file", $SSH_KEY,
            "--boot-volume-size-in-gbs", "50",
            "--hostname-label", "notiftk"
        ) `
        -RedirectStandardOutput $tmpOut `
        -RedirectStandardError $tmpErr `
        -Wait -PassThru -NoNewWindow

    $stdout = if (Test-Path $tmpOut) { Get-Content $tmpOut -Raw } else { "" }
    $stderr = if (Test-Path $tmpErr) { Get-Content $tmpErr -Raw } else { "" }
    $output = "$stdout$stderr"

    if ($stdout -match '"lifecycle_state"') {
        Log "SUCCESS! Instance created."
        $stdout | Out-File -FilePath "$PSScriptRoot\instance_response.json" -Encoding UTF8
        Log "Full response saved to infra/oracle/instance_response.json"
        break
    }
    elseif ($output -match "Out of host capacity") {
        Log "  -> No ARM capacity. Retrying in 90s..."
        Start-Sleep -Seconds 90
    }
    elseif ($output -match "TooManyRequests") {
        Log "  -> Rate limited. Waiting 120s..."
        Start-Sleep -Seconds 120
    }
    else {
        $snippet = $output.Substring(0, [Math]::Min(300, $output.Length))
        Log "  -> Exit $($proc.ExitCode): $snippet"
        Log "  -> Retrying in 90s..."
        Start-Sleep -Seconds 90
    }
}

if ($attempt -ge $maxAttempts) {
    Log "Gave up after $maxAttempts attempts."
}
