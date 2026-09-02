from cv.toolkit import VanishingPoint


def is_valid_vp_result(
    vps: list[VanishingPoint],
    image_width: int,
    image_height: int,
    min_support_lines: int = 3,
    max_distance_ratio: float = 50.0,
) -> bool:
    if not vps:
        return False

    for vp in vps:
        if vp.support_lines < min_support_lines:
            return False

        max_dim = max(image_width, image_height)
        dist = ((vp.x - image_width / 2) ** 2 + (vp.y - image_height / 2) ** 2) ** 0.5
        if dist > max_dim * max_distance_ratio:
            return False

    return True


def is_too_few_lines(lines: list, threshold: int = 5) -> bool:
    return len(lines) < threshold
