# Keeps the video render from ever filling the CPU: pins it to 4 logical cores and idle priority.
# Only touches processes the render created (playwright's Edge profile, node driver, render_frames/make_video).
$mask = [IntPtr]15
while ($true) {
    $targets = Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -eq 'msedge.exe' -and $_.CommandLine -match 'playwright') -or
        ($_.Name -eq 'node.exe' -and $_.CommandLine -match 'playwright') -or
        ($_.Name -eq 'python.exe' -and $_.CommandLine -match 'render_frames.py|make_video.py')
    }
    if (-not $targets) { Start-Sleep 5; if (-not (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'make_video.py' })) { break } ; continue }
    foreach ($t in $targets) {
        try { $p = Get-Process -Id $t.ProcessId -ErrorAction Stop; $p.ProcessorAffinity = $mask; $p.PriorityClass = 'Idle' } catch {}
    }
    Start-Sleep 1
}
