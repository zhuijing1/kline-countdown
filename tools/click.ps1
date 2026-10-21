param([int]$X = 100, [int]$Y = 250, [string]$Btn = "R")
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Mouse {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, int dx, int dy, uint dwData, int dwExtraInfo);
    public const uint LEFTDOWN = 0x0002, LEFTUP = 0x0004, RIGHTDOWN = 0x0008, RIGHTUP = 0x0010;
    public void Click(string btn) {
        SetCursorPos($X, $Y);
        if (btn == "R") { mouse_event(RIGHTDOWN, 0, 0, 0, 0); System.Threading.Thread.Sleep(30); mouse_event(RIGHTUP, 0, 0, 0, 0); }
        else { mouse_event(LEFTDOWN, 0, 0, 0, 0); System.Threading.Thread.Sleep(30); mouse_event(LEFTUP, 0, 0, 0, 0); }
    }
}
"@
$m = New-Object Mouse
$m.Click($Btn)
Write-Output ("clicked $Btn at $X,$Y")
