param([Parameter(Mandatory=$true)][string]$Manifest, [Parameter(Mandatory=$true)][string]$Output, [Parameter(Mandatory=$true)][string]$Language)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$speechRoot = [System.IO.Path]::GetFullPath($Output)
$speechItems = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$speechSynth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $speechCulture = if ($Language -eq 'English') { 'en-' } elseif ($Language -eq 'Urdu') { 'ur-' } else { throw 'Unsupported speech language' }
    $speechVoice = $speechSynth.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name.StartsWith($speechCulture) } | Select-Object -First 1
    if (-not $speechVoice) { throw 'No installed voice matches the requested language' }
    $speechSynth.SelectVoice($speechVoice.VoiceInfo.Name)
    foreach ($speechItem in $speechItems) {
        if ($speechItem.id -notmatch '^[a-f0-9]{64}$') { throw 'Invalid fixed prompt identifier' }
        $speechTarget = [System.IO.Path]::GetFullPath((Join-Path $speechRoot ($speechItem.id + '.wav')))
        if (-not $speechTarget.StartsWith($speechRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Speech output escaped its directory' }
        $speechSynth.SetOutputToWaveFile($speechTarget)
        $speechSynth.Speak([string]$speechItem.text)
        $speechSynth.SetOutputToNull()
    }
    Write-Output ('Prepared ' + $speechItems.Count + ' fixed prompts with ' + $speechVoice.VoiceInfo.Name)
} finally {
    $speechSynth.Dispose()
}
