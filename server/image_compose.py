"""
Image composition utilities for document analysis.

Lazily imports PIL/Pillow to allow the module to load without the dependency.
"""

from typing import Optional

SINGLE_IMAGE_MODEL_PREFIXES = ("llama3.2-vision",)


def is_single_image_model(name: str) -> bool:
    """
    Check if a model is known to accept only one image per request.

    Args:
        name: Model name (may include registry prefix like 'hf.co/...' and tag)

    Returns:
        True if the model is known to accept only one image; False otherwise.
    """
    if not name:
        return False

    # Strip registry prefix and tag for comparison
    # Examples: "llama3.2-vision:latest", "hf.co/x/llama3.2-vision:q4", "llama3.2-vision"
    model_base = name.lower()
    if "/" in model_base:
        # Take only the last part after the last /
        model_base = model_base.split("/")[-1]
    if ":" in model_base:
        # Remove tag
        model_base = model_base.split(":")[0]

    for prefix in SINGLE_IMAGE_MODEL_PREFIXES:
        if model_base.startswith(prefix.lower()):
            return True

    return False


def compose_comparison(
    reference_paths: list[str],
    subject_paths: list[str],
    out_path: str,
    max_width: int = 1400,
    max_height: int = 2600,
) -> str:
    """
    Build a composite PNG for signature comparison.

    Combines reference and subject images into a single image with labeled sections.

    Args:
        reference_paths: List of paths to reference specimen image(s)
        subject_paths: List of paths to subject document image(s)
        out_path: Output PNG file path
        max_width: Maximum width of the composite in pixels (default 1400)
        max_height: Maximum height of the composite in pixels (default 2600)

    Returns:
        The output path.

    Raises:
        ValueError: If no images are provided.
    """
    from PIL import Image, ImageDraw, ImageFont

    if not reference_paths and not subject_paths:
        raise ValueError("No images provided for composition")

    all_paths = reference_paths + subject_paths
    if not all_paths:
        raise ValueError("No images provided for composition")

    # Load all images
    images = []
    for path in all_paths:
        try:
            img = Image.open(path)
            # Convert to RGB (flatten RGBA/CMYK/palette modes)
            if img.mode in ("RGBA", "LA", "P"):
                # For RGBA and palette, convert with white background
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
                img = background
            elif img.mode == "CMYK":
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")
            images.append(img)
        except Exception as e:
            raise ValueError(f"Failed to load image {path}: {e}")

    # Scale images to fit max_width
    scaled_images = []
    for img in images:
        w, h = img.size
        if w > max_width:
            ratio = max_width / w
            new_h = int(h * ratio)
            img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)
        scaled_images.append(img)

    # Calculate banner height (assume ~40px for text)
    banner_height = 40

    # Calculate total height with banners
    ref_height = sum(img.height for img in scaled_images[:len(reference_paths)])
    subj_height = sum(img.height for img in scaled_images[len(reference_paths):])

    total_height = ref_height + subj_height + (2 * banner_height)
    canvas_width = max(img.width for img in scaled_images) if scaled_images else max_width

    # Scale down proportionally if exceeds max_height
    scale_factor = 1.0
    if total_height > max_height:
        scale_factor = max_height / total_height
        canvas_width = int(canvas_width * scale_factor)
        ref_height = int(ref_height * scale_factor)
        subj_height = int(subj_height * scale_factor)
        banner_height = int(banner_height * scale_factor)
        total_height = max_height

    # Create canvas
    canvas = Image.new("RGB", (canvas_width, total_height), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    # Try to get a font, fall back to default if not available
    font_size = max(12, int(banner_height * 0.6))
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except Exception:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

    # Helper to draw a banner and images
    y_offset = 0

    def add_section(label: str, image_list: list, is_dark: bool = True):
        nonlocal y_offset
        # Draw banner
        banner_color = (40, 40, 40) if is_dark else (200, 200, 200)
        text_color = (255, 255, 255) if is_dark else (0, 0, 0)
        draw.rectangle(
            [(0, y_offset), (canvas_width, y_offset + banner_height)],
            fill=banner_color,
        )
        # Draw text
        try:
            draw.text(
                (10, y_offset + banner_height // 4),
                label,
                fill=text_color,
                font=font,
            )
        except Exception:
            # Fallback if text drawing fails
            pass

        y_offset += banner_height

        # Draw images
        for img in image_list:
            if scale_factor != 1.0:
                new_w = int(img.width * scale_factor)
                new_h = int(img.height * scale_factor)
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            canvas.paste(img, (0, y_offset))
            y_offset += img.height

    # Add reference section
    add_section("REFERENCE SPECIMEN", scaled_images[:len(reference_paths)], is_dark=True)

    # Add subject section
    add_section(
        "DOCUMENT UNDER EXAMINATION",
        scaled_images[len(reference_paths):],
        is_dark=True,
    )

    # Save composite
    canvas.save(out_path, "PNG")
    return out_path
