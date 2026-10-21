param(
    [string]$Out = "$env:TEMP\kcdspot.png",
    [int]$X = 0,
    [int]$Y = 0,
    [int]$W = 0,
    [int]$H = 0
)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class DpiHelper {
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr ctx);
    public static void Enable() {
        try { SetProcessDpiAwarenessContext(new IntPtr(-4)); } catch { }
    }
}
"@
[DpiHelper]::Enable()

if ($W -le 0 -or $H -le 0) {
    $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $X = $bounds.X; $Y = $bounds.Y; $W = $bounds.Width; $H = $bounds.Height
}
$bmp = New-Object System.Drawing.Bitmap($W, $H)
$gfx = [System.Drawing.Graphics]::FromImage($bmp)
$gfx.CopyFromScreen($X, $Y, 0, 0, (New-Object System.Drawing.Size($W, $H)))
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$gfx.Dispose(); $bmp.Dispose()
Write-Output ("saved {0} ({1},{2} {3}x{4})" -f $Out, $X, $Y, $W, $H)
