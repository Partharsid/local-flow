# Live view of the parallel model download. Run: powershell -File tools\watch-download.ps1
param(
    [string]$Dir = "$env:APPDATA\LocalFlow\models\large-v3-turbo",
    [long]$Total = 1617884929,
    [int]$Parts = 8
)
$partsDir = Join-Path $Dir "model.bin.download.parts"
$final = Join-Path $Dir "model.bin"
$chunk = [math]::Ceiling($Total / $Parts)
$history = New-Object System.Collections.Generic.Queue[object]
$host.UI.RawUI.WindowTitle = "LocalFlow model download"
[Console]::CursorVisible = $false

function TrueLength([string]$path) {
    # Explorer/Get-Item show a stale size while curl still has the file open;
    # opening it with shared access returns the real current length.
    try {
        $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite, Delete')
        try { return $fs.Length } finally { $fs.Close() }
    } catch { return 0 }
}

function Bar([double]$frac, [int]$width) {
    $frac = [math]::Max(0, [math]::Min(1, $frac))
    $full = [int][math]::Floor($frac * $width)
    return ("#" * $full) + ("-" * ($width - $full))
}

try {
    while ($true) {
        $now = Get-Date
        $sizes = @()
        for ($i = 0; $i -lt $Parts; $i++) {
            $p = Join-Path $partsDir ("part{0:D2}" -f $i)
            $len = 0
            foreach ($f in @($p, "$p.tmp")) { if (Test-Path $f) { $len += TrueLength $f } }
            $want = [math]::Min($chunk, $Total - $i * $chunk)
            $sizes += [pscustomobject]@{ Have = [math]::Min($len, $want); Want = $want }
        }
        $done = ($sizes | Measure-Object Have -Sum).Sum
        $finished = (Test-Path $final) -and ((Get-Item $final).Length -eq $Total)
        if ($finished) { $done = $Total }

        $history.Enqueue([pscustomobject]@{ T = $now; B = $done })
        while ($history.Count -gt 10) { [void]$history.Dequeue() }
        $first = $history.Peek()
        $secs = ($now - $first.T).TotalSeconds
        $speed = if ($secs -gt 0) { ($done - $first.B) / $secs } else { 0 }
        $left = $Total - $done
        $eta = if ($speed -gt 1000) { [TimeSpan]::FromSeconds($left / $speed).ToString("mm\:ss") } else { "--:--" }
        $frac = $done / $Total

        $w = [math]::Max(30, [math]::Min(60, $host.UI.RawUI.WindowSize.Width - 30))
        $lines = @()
        $lines += ""
        $lines += "  LocalFlow - Whisper large-v3-turbo download"
        $lines += ""
        $lines += "  [{0}] {1,6:P1}" -f (Bar $frac $w), $frac
        $lines += "  {0,8:N1} MB of {1:N1} MB    {2,6:N2} MB/s    ETA {3}" -f ($done / 1MB), ($Total / 1MB), ($speed / 1MB), $eta
        $lines += ""
        for ($i = 0; $i -lt $Parts; $i++) {
            $s = $sizes[$i]
            $pf = if ($s.Want) { $s.Have / $s.Want } else { 1 }
            $state = if ($pf -ge 1) { "done" } elseif ($s.Have -gt 0) { "downloading" } else { "waiting" }
            $lines += "  part {0}  [{1}] {2,6:P0}  {3}" -f ($i + 1), (Bar $pf 24), $pf, $state
        }
        $lines += ""
        if ($finished) {
            $lines += "  COMPLETE - model.bin is in place. You can close this window."
        } elseif ($done -ge $Total) {
            $lines += "  All parts downloaded - joining into model.bin..."
        } else {
            $lines += "  Press Ctrl+C to close this view (the download keeps running)."
        }
        [Console]::SetCursorPosition(0, 0)
        $width = $host.UI.RawUI.WindowSize.Width - 1
        foreach ($l in $lines) { [Console]::WriteLine($l.PadRight($width)) }
        if ($finished) { break }
        Start-Sleep -Milliseconds 1000
    }
} finally {
    [Console]::CursorVisible = $true
}
