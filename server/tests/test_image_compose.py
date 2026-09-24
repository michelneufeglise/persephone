"""
Tests for the image composition utilities.

Tests compose_comparison and is_single_image_model functions.
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import pytest
import image_compose as ic

pytest.importorskip("PIL")

from PIL import Image
from pathlib import Path


@pytest.fixture
def tmp_png_factory(tmp_path):
    """Factory to create temporary PNG files for testing."""
    def create_png(width: int = 100, height: int = 100, mode: str = "RGB") -> str:
        path = tmp_path / f"test_{width}x{height}_{mode}.png"
        img = Image.new(mode, (width, height), color="white")
        img.save(path, "PNG")
        return str(path)

    return create_png


class TestIsSingleImageModel:
    """Test is_single_image_model function."""

    def test_llama_vision_true(self):
        """llama3.2-vision should return True."""
        assert ic.is_single_image_model("llama3.2-vision:latest") is True
        assert ic.is_single_image_model("llama3.2-vision") is True
        assert ic.is_single_image_model("hf.co/x/llama3.2-vision:q4") is True
        assert ic.is_single_image_model("LLAMA3.2-VISION:LATEST") is True

    def test_other_models_false(self):
        """Other models should return False."""
        assert ic.is_single_image_model("qwen2.5vl:32b") is False
        assert ic.is_single_image_model("gemma3:12b") is False
        assert ic.is_single_image_model("gpt-4-vision") is False
        assert ic.is_single_image_model("llava:latest") is False

    def test_empty_and_none(self):
        """Empty/None inputs should return False."""
        assert ic.is_single_image_model("") is False
        assert ic.is_single_image_model(None) is False


class TestComposeComparison:
    """Test compose_comparison function."""

    def test_compose_basic(self, tmp_png_factory, tmp_path):
        """Basic composition with one reference and one subject image."""
        ref_img = tmp_png_factory(100, 150, "RGB")
        subj_img = tmp_png_factory(120, 180, "RGB")
        out_path = str(tmp_path / "output.png")

        result = ic.compose_comparison([ref_img], [subj_img], out_path)

        assert result == out_path
        assert Path(out_path).exists()

        # Check output dimensions
        output = Image.open(out_path)
        assert output.width <= 1400
        assert output.height <= 2600

    def test_compose_multiple_images(self, tmp_png_factory, tmp_path):
        """Composition with multiple reference and subject images."""
        ref_imgs = [tmp_png_factory(100, 100, "RGB") for _ in range(2)]
        subj_imgs = [tmp_png_factory(100, 100, "RGB") for _ in range(2)]
        out_path = str(tmp_path / "output_multi.png")

        result = ic.compose_comparison(ref_imgs, subj_imgs, out_path)

        assert Path(out_path).exists()
        output = Image.open(out_path)
        assert output.width > 0
        assert output.height > 0

    def test_compose_rgba_mode(self, tmp_path):
        """Handle RGBA images correctly."""
        # Create an RGBA image
        rgba_path = tmp_path / "rgba.png"
        img_rgba = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
        img_rgba.save(rgba_path, "PNG")

        out_path = str(tmp_path / "output_rgba.png")
        result = ic.compose_comparison([str(rgba_path)], [], out_path)

        assert Path(out_path).exists()
        output = Image.open(out_path)
        assert output.mode == "RGB"  # Should be converted to RGB

    def test_compose_l_mode(self, tmp_path):
        """Handle grayscale (L mode) images."""
        l_path = tmp_path / "grayscale.png"
        img_l = Image.new("L", (100, 100), 128)
        img_l.save(l_path, "PNG")

        out_path = str(tmp_path / "output_l.png")
        result = ic.compose_comparison([str(l_path)], [], out_path)

        assert Path(out_path).exists()
        output = Image.open(out_path)
        assert output.mode == "RGB"

    def test_compose_palette_mode(self, tmp_path):
        """Handle palette mode (P) images."""
        p_path = tmp_path / "palette.png"
        img_p = Image.new("P", (100, 100), 0)
        # Add palette
        img_p.putpalette([255, 0, 0, 0, 255, 0, 0, 0, 255] + [0] * (256 * 3 - 9))
        img_p.save(p_path, "PNG")

        out_path = str(tmp_path / "output_p.png")
        result = ic.compose_comparison([str(p_path)], [], out_path)

        assert Path(out_path).exists()
        output = Image.open(out_path)
        assert output.mode == "RGB"

    def test_compose_no_images_error(self, tmp_path):
        """Should raise ValueError if no images provided."""
        out_path = str(tmp_path / "output_error.png")

        with pytest.raises(ValueError, match="No images"):
            ic.compose_comparison([], [], out_path)

    def test_compose_respects_max_width(self, tmp_png_factory, tmp_path):
        """Output width should not exceed max_width."""
        large_img = tmp_png_factory(2000, 100, "RGB")
        out_path = str(tmp_path / "output_width.png")

        ic.compose_comparison([large_img], [], out_path, max_width=800)

        output = Image.open(out_path)
        assert output.width <= 800

    def test_compose_respects_max_height(self, tmp_png_factory, tmp_path):
        """Output height should not exceed max_height."""
        tall_img = tmp_png_factory(100, 3000, "RGB")
        out_path = str(tmp_path / "output_height.png")

        ic.compose_comparison([tall_img], [], out_path, max_height=1000)

        output = Image.open(out_path)
        assert output.height <= 1000

    def test_compose_banner_labels_present(self, tmp_png_factory, tmp_path):
        """Check that banner labels are visually present (dark pixels at top)."""
        ref_img = tmp_png_factory(200, 100, "RGB")
        subj_img = tmp_png_factory(200, 100, "RGB")
        out_path = str(tmp_path / "output_labels.png")

        ic.compose_comparison([ref_img], [subj_img], out_path)

        output = Image.open(out_path)
        # Check that top-left region has dark pixels (banner)
        pixels = output.load()
        # Sample a pixel in the banner area (top-left)
        r, g, b = pixels[10, 10][:3] if output.mode == "RGBA" else pixels[10, 10]
        # Banner should be darker than white (255, 255, 255)
        assert (r + g + b) < 500  # Dark color


class TestComposeEdgeCases:
    """Test edge cases and error handling."""

    def test_invalid_image_path(self, tmp_path):
        """Should raise ValueError for invalid image paths."""
        out_path = str(tmp_path / "output_invalid.png")

        with pytest.raises(ValueError, match="Failed to load"):
            ic.compose_comparison(["/nonexistent/image.png"], [], out_path)

    def test_empty_reference_with_subject(self, tmp_png_factory, tmp_path):
        """Should work with empty reference list and subject images."""
        subj_img = tmp_png_factory(100, 100, "RGB")
        out_path = str(tmp_path / "output_no_ref.png")

        result = ic.compose_comparison([], [subj_img], out_path)
        assert Path(out_path).exists()

    def test_empty_subject_with_reference(self, tmp_png_factory, tmp_path):
        """Should work with reference images and empty subject list."""
        ref_img = tmp_png_factory(100, 100, "RGB")
        out_path = str(tmp_path / "output_no_subj.png")

        result = ic.compose_comparison([ref_img], [], out_path)
        assert Path(out_path).exists()
