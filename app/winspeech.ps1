# Text-to-speech for cues on Windows (the counterpart of macOS `say`), called by cues.py.
# Runs in Windows PowerShell 5.1, which ships with Windows; nothing to install.
#   -Mode list                 one voice per line: engine<TAB>id<TAB>name<TAB>language<TAB>gender
#   -Mode speak -Voice e|id -Rate 1.1 -TextFile in.txt -Out out.wav
# Two engines: WinRT (Windows 10/11 voices, including Thai "Pattara" once the Thai speech
# pack is installed) and SAPI (older desktop voices). The text comes from a UTF-8 file so
# nothing typed is ever parsed as a command.
param(
  [string]$Mode = "list",
  [string]$Voice = "",
  [double]$Rate = 1.0,
  [string]$TextFile = "",
  [string]$Out = ""
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Get-WinRtVoices {
  try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
    return @([Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices)
  } catch {
    return @()
  }
}

function Wait-Operation($Operation, [Type]$ResultType) {
  $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq "AsTask" -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
  } | Select-Object -First 1
  $task = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
  $null = $task.Wait(-1)
  return $task.Result
}

if ($Mode -eq "list") {
  foreach ($v in Get-WinRtVoices) {
    "winrt`t$($v.Id)`t$($v.DisplayName)`t$($v.Language)`t$($v.Gender)"
  }
  try {
    Add-Type -AssemblyName System.Speech
    $sapi = New-Object System.Speech.Synthesis.SpeechSynthesizer
    foreach ($v in $sapi.GetInstalledVoices()) {
      if ($v.Enabled) {
        $info = $v.VoiceInfo
        "sapi`t$($info.Name)`t$($info.Name)`t$($info.Culture.Name)`t$($info.Gender)"
      }
    }
    $sapi.Dispose()
  } catch {}
  exit 0
}

$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$engine, $id = $Voice.Split("|", 2)
if ($engine -eq "winrt") {
  $null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]
  $synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
  $synth.Voice = Get-WinRtVoices | Where-Object { $_.Id -eq $id } | Select-Object -First 1
  try { $synth.Options.SpeakingRate = $Rate } catch {}
  $stream = Wait-Operation ($synth.SynthesizeTextToStreamAsync($text)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
  $reader = [Windows.Storage.Streams.DataReader]::new($stream.GetInputStreamAt(0))
  $size = [uint32]$stream.Size
  $null = Wait-Operation ($reader.LoadAsync($size)) ([uint32])
  $bytes = New-Object byte[] $size
  $reader.ReadBytes($bytes)
  [System.IO.File]::WriteAllBytes($Out, $bytes)
  $stream.Dispose()
  $synth.Dispose()
} else {
  Add-Type -AssemblyName System.Speech
  $sapi = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $sapi.SelectVoice($id)
  $sapi.Rate = [int][Math]::Max(-10, [Math]::Min(10, [Math]::Round(($Rate - 1.0) * 10)))
  $sapi.SetOutputToWaveFile($Out)
  $sapi.Speak($text)
  $sapi.Dispose()
}
