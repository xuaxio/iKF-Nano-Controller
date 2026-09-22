# -*- coding: utf-8 -*-
"""正式打包图标: 纯黑底 + Segoe UI Black "1KF"(数字1低调替换I), 抗锯齿大字号"""
from PIL import Image, ImageDraw, ImageFont

SIZE = 512
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 255))
d = ImageDraw.Draw(img)
font_path = r"C:\Windows\Fonts\seguibl.ttf"
text = "1KF"
best = None
for size in range(440, 60, -2):
    fnt = ImageFont.truetype(font_path, size)
    b = fnt.getbbox(text)
    tw = d.textlength(text, font=fnt)
    th = b[3] - b[1]
    if tw <= SIZE * 0.92 and th <= SIZE * 0.90:
        best = (size, fnt); break
if best is None:
    best = (200, ImageFont.truetype(font_path, 200))
size, fnt = best
b = fnt.getbbox(text)
tw = d.textlength(text, font=fnt)
th = b[3] - b[1]
x = (SIZE - tw) // 2
y = (SIZE - th) // 2 - b[1]
d.text((x, y), text, font=fnt, fill=(255, 255, 255, 255))

img.save("ikf_icon_512.png")
img.resize((256, 256), Image.LANCZOS).save(
    "ikf_icon.ico", sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])
print(f"正式图标: {font_path} 字号={size}  => ikf_icon.ico + ikf_icon_512.png")