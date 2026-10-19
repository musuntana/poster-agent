"""生成无版权外部依赖的教学示例商品与 Logo，并输出示范海报。"""
from pathlib import Path
from PIL import Image, ImageDraw
from app.compositor import compose, local_background
from app.schemas import PosterSpec

out = Path('data/examples')
out.mkdir(parents=True, exist_ok=True)
product = Image.new('RGBA', (500, 680), (0, 0, 0, 0))
draw = ImageDraw.Draw(product)
draw.rounded_rectangle((180, 25, 310, 585), radius=42, fill='#e8f8ff', outline='#9cb6ca', width=8)
draw.rounded_rectangle((210, 68, 280, 90), radius=10, fill='#2295d9')
draw.rounded_rectangle((218, 310, 272, 316), radius=3, fill='#20a3d7')
draw.rounded_rectangle((221, 560, 269, 650), radius=20, fill='#adcbdc')
product.save(out/'sample_product.png')
logo = Image.new('RGBA', (310, 95), (0, 0, 0, 0))
ld = ImageDraw.Draw(logo)
ld.rounded_rectangle((0, 8, 78, 85), radius=22, fill='#69cbf8')
ld.ellipse((18, 28, 60, 67), fill='#163c7d')
logo.save(out/'sample_logo.png')
spec = PosterSpec(title='双十一限时优惠', subtitle='智能清洁 · 品质生活',price='199',original_price='399',style='蓝色科技风')
(out/'sample_preview.png').write_bytes(compose(spec, product, logo, local_background(spec.style)))
print('已生成 data/examples/sample_product.png, sample_logo.png, sample_preview.png')
