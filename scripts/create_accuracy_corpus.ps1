# Reproducible, local TTS fixtures. Only the installed English voice is required.
# Generated speech is a smoke corpus, not a substitute for human multilingual evaluation.
param([string]$OutputDirectory = "build/accuracy/corpus")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$taskOutput = [IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputDirectory))
New-Item -ItemType Directory -Force -Path $taskOutput | Out-Null
$taskSynth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$taskCases = @(
    @{ id = "conversation"; rate = 0; text = "Hello. Thank you for joining us. We need to discuss the delivery before we decide what to do next. I think we should wait until tomorrow. What do you think?" },
    @{ id = "names_numbers"; rate = 0; text = "Please ask Nakamura about the invoice. Doctor Chen ordered seventeen units for project Aurora. The meeting starts at nine thirty on October twelfth. The total is one thousand two hundred dollars." },
    @{ id = "rapid"; rate = 3; text = "We need to review the numbers carefully because the first estimate was wrong and the new delivery date depends on whether the supplier can confirm the order before the end of the day." },
    @{ id = "boundary"; rate = 0; text = "The person responsible for approving this particular purchase order is Doctor Nakamura from the international research department. She asked us to bring the revised budget and the updated schedule to the meeting tomorrow morning." }
)
$taskManifest = @()
try {
    foreach ($taskCase in $taskCases) {
        $taskSynth.Rate = $taskCase.rate
        $taskSynth.SetOutputToWaveFile((Join-Path $taskOutput ($taskCase.id + ".wav")))
        $taskSynth.Speak($taskCase.text)
        $taskSynth.SetOutputToNull()
        $taskManifest += @{ id = $taskCase.id; audio = $taskCase.id + ".wav"; reference = $taskCase.text; language = "en"; tags = @($taskCase.id, "synthetic") }
    }
    ConvertTo-Json -Depth 5 -InputObject $taskManifest | Set-Content -Encoding utf8 (Join-Path $taskOutput "manifest.json")
} finally {
    $taskSynth.Dispose()
}
