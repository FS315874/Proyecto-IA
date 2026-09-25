param(
    [ValidateSet("es-UY", "es-AR", "es-ES")]
    [string]$Culture = "es-UY",
    [ValidateRange(1, 20)]
    [int]$MaxSeconds = 10
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$recognizer = $null

function Write-VoiceResult {
    param(
        [string]$Status,
        [string]$Transcript,
        [double]$Confidence,
        [string]$SelectedCulture,
        [double]$CaptureMs,
        [double]$TranscriptionMs
    )
    @{
        schema_version = 1
        status = $Status
        transcript = $Transcript
        confidence = $Confidence
        culture = $SelectedCulture
        capture_ms = $CaptureMs
        transcription_ms = $TranscriptionMs
    } | ConvertTo-Json -Compress
}

try {
    Add-Type -AssemblyName System.Speech
    $installed = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
    $selected = $installed | Where-Object { $_.Culture.Name -eq $Culture } | Select-Object -First 1
    if ($null -eq $selected) {
        $selected = $installed | Where-Object { $_.Culture.TwoLetterISOLanguageName -eq "es" } | Select-Object -First 1
    }
    if ($null -eq $selected) {
        Write-VoiceResult "unavailable" "" 0.0 "none" 0.0 0.0
        exit 0
    }

    $recognizer = [System.Speech.Recognition.SpeechRecognitionEngine]::new($selected.Culture)
    $recognizer.LoadGrammar([System.Speech.Recognition.DictationGrammar]::new())
    $recognizer.SetInputToDefaultAudioDevice()
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $result = $recognizer.Recognize([TimeSpan]::FromSeconds($MaxSeconds))
    $timer.Stop()
    if ($null -eq $result -or [string]::IsNullOrWhiteSpace($result.Text)) {
        Write-VoiceResult "no_speech" "" 0.0 $selected.Culture.Name $timer.Elapsed.TotalMilliseconds 0.0
        exit 0
    }
    Write-VoiceResult "ready" $result.Text $result.Confidence $selected.Culture.Name $timer.Elapsed.TotalMilliseconds 0.0
}
catch {
    Write-VoiceResult "failed" "" 0.0 "none" 0.0 0.0
}
finally {
    if ($null -ne $recognizer) {
        $recognizer.Dispose()
    }
}
