param(
    [string]$Src = "$env:TEMP\kcdshot.png",
    [string]$Dst = "$env:TEMP\kcdspot.png",
    [int]$X = 1360,
    [int]$Y = 700,
    [int]$W = 300,
    [int]$H = 140
)
Add-Type -AssemblyName System.Drawing
$src = New-Object System.Drawing.Bitmap($Src)
$crop = New-Object System.Drawing.Bitmap($W, $H)
$gfx = [System.Drawing.Graphics]::FromImage($crop)
$dstRect = New-Object System.Drawing.Rectangle(0, 0, $W, $H)
$srcRect = New-Object System.Drawing.Rectangle($X, $Y, $W, $H)
$gfx.DrawImage($src, $dstRect, $srcRect, [System.Drawing.GraphicsUnit]::Pixel)
$crop.Save($Dst, [System.Drawing.Imaging.ImageFormat]::Png)
$gfx.Dispose(); $crop.Dispose(); $src.Dispose()
Write-Output ("saved {0} ({1},{2} {3}x{4})" -f $Dst, $X, $Y, $W, $H)
