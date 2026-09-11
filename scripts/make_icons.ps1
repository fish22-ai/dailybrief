# 生成 site/ 下三张 PWA 图标：暗底 + 白色「勢」。
#
#   powershell -ExecutionPolicy Bypass -File scripts/make_icons.ps1
#
# 为什么要这个脚本：纯 Python 没法光栅化汉字（不引 Pillow），图标一度是手搓的
# 二进制资源，字号 0.6×边长 —— 字几乎顶到边框，装到桌面又大又挤。这里用系统自带
# 的 System.Drawing 重画：字号 0.32×边长（汉字墨迹约为字号的 0.9，实际四周留白
# 约 35%），字小、留白大，缩小到任务栏那么小也不堵；同时稳稳落在 maskable 的
# 80% 安全圆里，安卓切圆形/方形都不会切到字。改配色或字号只动下面两个数，
# 别去改 PNG。
Add-Type -AssemblyName System.Drawing

$scale = 0.32                       # 字号 = 边长 × 这个数（0.46 墨迹占 42%，0.36 约占 33%，0.32 约占 29%）
$fgHex = '#faf8f3'                  # 字色（同 --bg 暖白）
$bgHex = '#191918'                  # 底色（同 --ink 墨黑）
$outDir = Join-Path $PSScriptRoot '..\site'
$files = @(
  @(512, 'icon-512.png'),
  @(192, 'icon-192.png'),
  @(180, 'apple-touch-icon.png')    # iOS 加到主屏用的尺寸
)

$bg = [System.Drawing.ColorTranslator]::FromHtml($bgHex)
$fg = [System.Drawing.ColorTranslator]::FromHtml($fgHex)
$font = New-Object System.Drawing.FontFamily 'SimSun'
$fmt = New-Object System.Drawing.StringFormat
$brush = New-Object System.Drawing.SolidBrush $fg

foreach ($f in $files) {
  $size = $f[0]
  $bmp = New-Object System.Drawing.Bitmap $size, $size
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = 'AntiAlias'
  $g.Clear($bg)
  # 走 GraphicsPath 而不是 DrawString：DrawString 居中的是**行盒**，汉字的行盒
  # 上下留白不对称（SimSun 尤其明显），画出来字会偏上 1/8 个格子。GetBounds() 拿到的
  # 是墨迹本身的外框，按它居中才是真的居中。
  $p = New-Object System.Drawing.Drawing2D.GraphicsPath
  $p.AddString('勢', $font, ([System.Drawing.FontStyle]::Bold), ($size * $scale),
               (New-Object System.Drawing.PointF 0, 0), $fmt)
  $b = $p.GetBounds()
  $m = New-Object System.Drawing.Drawing2D.Matrix
  $m.Translate([single](($size - $b.Width) / 2 - $b.X),
               [single](($size - $b.Height) / 2 - $b.Y))
  $p.Transform($m)
  $g.FillPath($brush, $p)
  $g.Dispose()
  $path = Join-Path $outDir $f[1]
  $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
  $bmp.Dispose()
  Write-Host "写出 $($f[1]) ($size x $size)"
}
