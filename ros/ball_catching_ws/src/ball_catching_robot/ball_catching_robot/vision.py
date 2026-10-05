"""Classical color detection, metric stereo, and gravity-aware state estimation."""
import cv2
import numpy as np

GRAVITY = np.array([0., 0., -9.81])


def candidates(rgb):
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, (25, 90, 80), (85, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found = []
    for contour in contours:
        area = cv2.contourArea(contour)
        perimeter = cv2.arcLength(contour, True)
        if area < 6 or perimeter == 0 or 4 * np.pi * area / perimeter ** 2 < 0.45:
            continue
        (u, v), radius = cv2.minEnclosingCircle(contour)
        found.append((u, v, radius))
    return found


class Stereo:
    def __init__(self, focal, cx, cy, baseline, camera_origin):
        self.focal, self.cx, self.cy, self.baseline = focal, cx, cy, baseline
        self.origin = np.asarray(camera_origin)

    def point(self, left, right):
        disparity = left[0] - right[0]
        if disparity < 1 or abs(left[1] - right[1]) > 3:
            return None
        z = self.focal * self.baseline / disparity
        x = (left[0] - self.cx) * z / self.focal
        y = ((left[1] + right[1]) / 2 - self.cy) * z / self.focal
        # Optical frame (right, down, forward) to calibrated world frame.
        return self.origin + np.array([z, -x, -y])

    def detect(self, left_image, right_image, predicted=None):
        points = []
        for left in candidates(left_image):
            for right in candidates(right_image):
                if min(left[2], right[2]) / max(left[2], right[2]) < 0.65:
                    continue
                point = self.point(left, right)
                if point is not None:
                    score = np.linalg.norm(point - predicted) if predicted is not None else -left[2]
                    radius = (left[2] + right[2]) / 2 * (point[0] - self.origin[0]) / self.focal
                    points.append((score, point, radius))
        if not points:
            return None
        _, point, radius = min(points, key=lambda x: x[0])
        return point, radius


class BallFilter:
    def __init__(self):
        self.history = []
        self.time, self.state, self.covariance = None, None, None

    def predict(self, time):
        if self.state is None:
            return None
        dt = time - self.time
        return self.state[:3] + self.state[3:] * dt + 0.5 * GRAVITY * dt ** 2

    def observe(self, point, time):
        if self.time is not None and time <= self.time:
            return None
        if self.time is not None and time - self.time > 0.15:
            self.history, self.state = [], None
        if self.state is None:
            self.history.append((time, point.copy()))
            self.time = time
            if len(self.history) < 5:
                return None
            times = np.array([t - time for t, _ in self.history])
            positions = np.array([p for _, p in self.history]) - 0.5 * times[:, None] ** 2 * GRAVITY
            fit = np.linalg.lstsq(np.column_stack([np.ones(len(times)), times]), positions, rcond=None)[0]
            self.state = np.concatenate([fit[0], fit[1]])
            self.covariance = np.diag([0.0001] * 3 + [0.1] * 3)
        else:
            dt = time - self.time
            transition = np.eye(6)
            transition[:3, 3:] = dt * np.eye(3)
            state = transition @ self.state + np.concatenate([0.5 * GRAVITY * dt ** 2, GRAVITY * dt])
            covariance = transition @ self.covariance @ transition.T + np.diag([1e-6] * 3 + [1e-3] * 3)
            innovation = point - state[:3]
            if np.linalg.norm(innovation) > 0.25:
                self.state, self.history, self.time = None, [(time, point.copy())], time
                return None
            residual = covariance[:3, :3] + np.eye(3) * 0.000025
            gain = np.linalg.solve(residual, covariance[:3, :]).T
            self.state = state + gain @ innovation
            self.covariance = covariance - gain @ covariance[:3, :]
            self.time = time
        return self.state.copy()
