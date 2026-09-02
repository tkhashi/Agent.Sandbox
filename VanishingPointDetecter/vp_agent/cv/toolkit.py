from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image


@dataclass
class Line:
    x1: float
    y1: float
    x2: float
    y2: float

    def angle(self) -> float:
        return np.arctan2(self.y2 - self.y1, self.x2 - self.x1)

    def as_array(self) -> np.ndarray:
        return np.array([self.x1, self.y1, self.x2, self.y2])


@dataclass
class VanishingPoint:
    x: float
    y: float
    support_lines: int  # クラスタ内の支持直線数
    confidence: float   # [0.0, 1.0]


class CVToolkit:
    def detect_lines(self, image_path: str, canny_low: int = 50, canny_high: int = 150) -> list[Line]:
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"画像読み込み失敗: {image_path}")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, canny_low, canny_high)

        lsd = cv2.createLineSegmentDetector(0)
        lines_raw, _, _, _ = lsd.detect(edges)

        if lines_raw is None:
            return []

        lines = []
        for line in lines_raw:
            x1, y1, x2, y2 = line[0]
            length = np.hypot(x2 - x1, y2 - y1)
            if length > 20:  # 短すぎるセグメント除去
                lines.append(Line(float(x1), float(y1), float(x2), float(y2)))

        return lines

    def find_vanishing_points(self, lines: list[Line], num_vps: int) -> list[VanishingPoint]:
        if len(lines) < 3:
            return []

        # 角度でクラスタリング → 各クラスタでRANSAC交点推定
        angles = np.array([l.angle() for l in lines])
        # [-π/2, π/2] に正規化
        angles = np.mod(angles + np.pi / 2, np.pi) - np.pi / 2

        clusters = self._cluster_by_angle(lines, angles, num_vps)
        vps = []
        for cluster_lines in clusters:
            if len(cluster_lines) < 2:
                continue
            vp = self._ransac_intersection(cluster_lines)
            if vp is not None:
                vps.append(vp)

        return vps

    def draw_edges(self, image_path: str, canny_low: int = 50, canny_high: int = 150) -> Image.Image:
        img = cv2.imread(image_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, canny_low, canny_high)
        return Image.fromarray(edges)

    def draw_lines_on_image(self, image_path: str, lines: list[Line]) -> Image.Image:
        img = cv2.imread(image_path)
        for line in lines:
            cv2.line(img, (int(line.x1), int(line.y1)), (int(line.x2), int(line.y2)), (0, 0, 255), 1)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)

    def draw_clusters(self, image_path: str, lines: list[Line], num_clusters: int) -> Image.Image:
        img = cv2.imread(image_path)
        if not lines:
            return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

        angles = np.array([l.angle() for l in lines])
        angles = np.mod(angles + np.pi / 2, np.pi) - np.pi / 2
        clusters = self._cluster_by_angle(lines, angles, num_clusters)

        palette = [
            (255, 50, 50),   # 赤
            (50, 200, 50),   # 緑
            (50, 100, 255),  # 青
        ]
        for i, cluster_lines in enumerate(clusters):
            bgr = tuple(reversed(palette[i % len(palette)]))
            for line in cluster_lines:
                cv2.line(img, (int(line.x1), int(line.y1)), (int(line.x2), int(line.y2)), bgr, 1)

        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)

    def draw_overlay(self, image_path: str, vps: list[VanishingPoint]) -> Image.Image:
        img = cv2.imread(image_path)
        h, w = img.shape[:2]

        colors = [(0, 0, 255), (0, 255, 0), (255, 0, 0)]
        for i, vp in enumerate(vps):
            color = colors[i % len(colors)]
            cx, cy = int(vp.x), int(vp.y)
            # 画像内にある場合のみ描画
            if 0 <= cx < w and 0 <= cy < h:
                cv2.circle(img, (cx, cy), 10, color, -1)
                cv2.circle(img, (cx, cy), 20, color, 2)

        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)

    def _cluster_by_angle(
        self, lines: list[Line], angles: np.ndarray, num_clusters: int
    ) -> list[list[Line]]:
        if len(lines) <= num_clusters:
            return [[l] for l in lines]

        # k-meansで角度クラスタリング
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.1)
        _, labels, _ = cv2.kmeans(
            angles.astype(np.float32).reshape(-1, 1),
            num_clusters,
            None,
            criteria,
            10,
            cv2.KMEANS_PP_CENTERS,
        )

        clusters: list[list[Line]] = [[] for _ in range(num_clusters)]
        for i, label in enumerate(labels.flatten()):
            clusters[label].append(lines[i])

        return clusters

    def _ransac_intersection(self, lines: list[Line]) -> VanishingPoint | None:
        best_inliers = []
        best_point = None
        n = len(lines)
        iterations = min(100, n * (n - 1) // 2)

        rng = np.random.default_rng(42)

        for _ in range(iterations):
            idx = rng.choice(n, 2, replace=False)
            pt = self._line_intersection(lines[idx[0]], lines[idx[1]])
            if pt is None:
                continue

            inliers = []
            for line in lines:
                dist = self._point_to_line_distance(pt, line)
                if dist < 10.0:  # ピクセル距離閾値
                    inliers.append(line)

            if len(inliers) > len(best_inliers):
                best_inliers = inliers
                best_point = pt

        if best_point is None or len(best_inliers) < 2:
            return None

        confidence = len(best_inliers) / n
        return VanishingPoint(
            x=float(best_point[0]),
            y=float(best_point[1]),
            support_lines=len(best_inliers),
            confidence=confidence,
        )

    def _line_intersection(self, l1: Line, l2: Line) -> tuple[float, float] | None:
        # ax + by = c の連立方程式
        a1 = l1.y2 - l1.y1
        b1 = l1.x1 - l1.x2
        c1 = a1 * l1.x1 + b1 * l1.y1

        a2 = l2.y2 - l2.y1
        b2 = l2.x1 - l2.x2
        c2 = a2 * l2.x1 + b2 * l2.y1

        det = a1 * b2 - a2 * b1
        if abs(det) < 1e-10:
            return None

        x = (c1 * b2 - c2 * b1) / det
        y = (a1 * c2 - a2 * c1) / det
        return (x, y)

    def _point_to_line_distance(self, pt: tuple[float, float], line: Line) -> float:
        px, py = pt
        dx = line.x2 - line.x1
        dy = line.y2 - line.y1
        length = np.hypot(dx, dy)
        if length < 1e-10:
            return float("inf")
        return abs(dy * px - dx * py + line.x2 * line.y1 - line.y2 * line.x1) / length
