#!/usr/bin/env pwsh
# Retries Oracle VM launch until ARM capacity is available.
# Run from project root: powershell -File infra/oracle/retry_launch.ps1

$OCI = "C:\Users\grego\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\Scripts\oci.exe"
$TENANCY = "ocid1.tenancy.oc1..aaaaaaaadkpmsjveqnpao2f5riexsrollguinxvtph5y76pvszhl6rl74ksq"
$SUBNET = "ocid1.subnet.oc1.eu-paris-1.aaaaaaaayd44efl5maudkiz5of5hc5duyfkqaybuq4kezq2phqup43kuzira"
$IMAGE = "ocid1.image.oc1.eu-paris-1.aaaaaaaay7dy7prlkadomnftjiujye75q2mgdha4ypaw6jonnc6sogmefkoa"
$SSH_KEY = "$env:USERPROFILE\.ssh\notiftk_oracle.pub"

$shapeFile = "$env:TEMP\notiftk_shape.json"
'{"ocpus": 2, "memoryInGBs": 12}' | Out-File -FilePath $shapeFile -Encoding ascii -NoNewline

$attempt = 0
$maxAttempts = 200   # ~3h at 60s intervals

while ($attempt -lt $maxAttempts) {
    $attempt++
    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "[$ts] Attempt $attempt — launching VM.Standard.A1.Flex..."

    $output = & $OCI compute instance launch `
        --compartment-id $TENANCY `
        --availability-domain "DuqC:EU-PARIS-1-AD-1" `
        --display-name "notiftk" `
        --image-id $IMAGE `
        --shape "VM.Standard.A1.Flex" `
        --shape-config "file://$shapeFile" `
        --subnet-id $SUBNET `
        --assign-public-ip true `
        --ssh-authorized-keys-file $SSH_KEY `
        --boot-volume-size-in-gbs 50 `
        --hostname-label "notiftk" 2>&1 | Out-String

    if ($output -match '"id":\s*"(ocid1\.instance\.[^"]+)"') {
        $instanceId = $matches[1]
        Write-Host "SUCCESS! Instance created: $instanceId"
        Write-Host $output
        $instanceId | Out-File -FilePath "$PSScriptRoot\instance_id.txt" -Encoding ascii
        Write-Host "Instance ID saved to infra/oracle/instance_id.txt"
        break
    }
    elseif ($output -match "Out of host capacity") {
        Write-Host "  -> No capacity yet. Retrying in 90s..."
        Start-Sleep -Seconds 90
    }
    elseif ($output -match "TooManyRequests") {
        Write-Host "  -> Rate limited. Waiting 120s..."
        Start-Sleep -Seconds 120
    }
    else {
        Write-Host "  -> Unexpected error:"
        Write-Host $output
        Write-Host "Retrying in 60s..."
        Start-Sleep -Seconds 60
    }
}

if ($attempt -ge $maxAttempts) {
    Write-Host "Gave up after $maxAttempts attempts. Check Oracle capacity later."
}
