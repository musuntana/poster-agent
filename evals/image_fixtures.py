"""Generate 20 ACTUAL PNG images with deliberately controlled text defects.

Images are code-rendered test fixtures with synthetic product art. They are not
customer images, not human annotations, and do not measure AI generation.

For each image, the expected_spec represents the campaign's intended copy;
the rendered spec deliberately differs in selected fields. Ground truth is
derived from those deterministic input differences, never from an OCR guess.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field, model_validator

from app.compositor import compose, local_background
from app.schemas import PosterSpec


class ImageFixture(BaseModel):
    case_id: str
    category: str
    image_file: str
    image_sha256: str
    rendered_spec: PosterSpec
    truth_defects: list[str]


class ImageManifest(BaseModel):
    dataset_id: str
    dataset_kind: str
    caveat: str
    expected_spec: PosterSpec
    target_fields: list[str]
    fixtures: list[ImageFixture] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_fixture_set(self):
        if self.dataset_kind != "code_rendered_png_fixtures":
            raise ValueError("Only generated code-rendered fixtures supported")
        if len({case.case_id for case in self.fixtures}) != len(self.fixtures):
            raise ValueError("Repeated case ID")
        for case in self.fixtures:
            if not set(case.truth_defects).issubset(set(self.target_fields)):
                raise ValueError("Label outside target fields")
            if Path(case.image_file).name != case.image_file:
                raise ValueError("Image paths must be basenames")
            derived_labels = [
                field for field in self.target_fields
                if getattr(case.rendered_spec, field) != getattr(self.expected_spec, field)
            ]
            if derived_labels != case.truth_defects:
                raise ValueError("programmatic ground truth does not match rendered spec")
        return self


EXPECTED = PosterSpec(
    title="双十一优惠", subtitle="", price="199", original_price=None,
    cta="立即抢购", style="蓝色科技风", background_mode="local",
)


@dataclass(frozen=True)
class Scenario:
    name: str
    category: str
    title: str = EXPECTED.title
    price: str = "199"
    cta: str = EXPECTED.cta
    style: str = "蓝色科技风"


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("F01", "clean"),
    Scenario("F02", "clean", style="粉色浪漫"),
    Scenario("F03", "clean", style="自然绿色"),
    Scenario("F04", "clean", style="简约科技蓝"),
    Scenario("F05", "price_error", price="189"),
    Scenario("F06", "price_error", price="1990"),
    Scenario("F07", "price_error", price="199.90"),
    Scenario("F08", "price_error", price="299", style="粉色浪漫"),
    Scenario("F09", "price_error", price="19.9", style="自然绿色"),
    Scenario("F10", "title_error", title="双十二优惠"),
    Scenario("F11", "title_error", title="双十一促销"),
    Scenario("F12", "title_error", title="会员限时优惠", style="粉色浪漫"),
    Scenario("F13", "title_error", title="十一月优惠", style="自然绿色"),
    Scenario("F14", "cta_error", cta="立即购买"),
    Scenario("F15", "cta_error", cta="现在开抢", style="粉色浪漫"),
    Scenario("F16", "cta_error", cta="马上下单", style="自然绿色"),
    Scenario("F17", "multi_error", title="双十二优惠", price="189"),
    Scenario("F18", "multi_error", title="双十一大促", cta="立即购买"),
    Scenario("F19", "multi_error", price="299", cta="去看看"),
    Scenario("F20", "multi_error", title="限时秒杀", price="299", cta="立即购买"),
)


def make_product() -> Image.Image:
    """Locally generated illustrative toy SKU, not any actual brand."""
    product = Image.new("RGBA", (500, 680), (0, 0, 0, 0))
    draw = ImageDraw.Draw(product)
    draw.rounded_rectangle((170, 28, 325, 591), radius=52,
                           fill="#DFEDF7", outline="#83A9C7", width=9)
    draw.rounded_rectangle((205, 80, 291, 101), radius=10, fill="#2A8CB8")
    draw.ellipse((230, 230, 263, 265), fill="#A5C7D9")
    draw.rounded_rectangle((227, 580, 269, 653), radius=16, fill="#8AB9CF")
    return product


def make_logo() -> Image.Image:
    logo = Image.new("RGBA", (260, 90), (0, 0, 0, 0))
    draw = ImageDraw.Draw(logo)
    draw.rounded_rectangle((10, 5, 92, 86), radius=20, fill="#72C5F4")
    draw.ellipse((35, 29, 71, 64), fill="#1B3D75")
    return logo


def generate_fixture_set(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    product = make_product()
    logo = make_logo()
    cases: list[ImageFixture] = []
    for scenario in SCENARIOS:
        rendered = EXPECTED.model_copy(update={
            "title": scenario.title,
            "price": EXPECTED.price.__class__(scenario.price),
            "cta": scenario.cta,
            "style": scenario.style,
        })
        # A new validated spec, so mistakes in authored scenario data fail early.
        rendered = PosterSpec.model_validate(rendered.model_dump())
        truth = [
            field for field in ("title", "price", "cta")
            if getattr(rendered, field) != getattr(EXPECTED, field)
        ]
        png = compose(rendered, product, logo, local_background(rendered.style))
        image_file = scenario.name + ".png"
        (output_dir / image_file).write_bytes(png)
        cases.append(ImageFixture(
            case_id=scenario.name, category=scenario.category, image_file=image_file,
            image_sha256=hashlib.sha256(png).hexdigest(),
            rendered_spec=rendered, truth_defects=truth,
        ))
    manifest = ImageManifest(
        dataset_id="m3b_rendered_image_fixtures_v1",
        dataset_kind="code_rendered_png_fixtures",
        caveat="20 rendered PNG test fixtures with programmatic labels. NOT customer data or real OCR metrics.",
        expected_spec=EXPECTED,
        target_fields=["title", "price", "cta"],
        fixtures=cases,
    )
    destination = output_dir / "manifest.json"
    destination.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination
