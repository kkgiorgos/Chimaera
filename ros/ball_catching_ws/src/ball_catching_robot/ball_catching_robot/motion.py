"""Continuous quintic motion and joint-space feasibility checks."""
import numpy as np


class Quintic:
    def __init__(self, start, duration, q, dq, ddq, target):
        self.start, self.duration = start, duration
        self.coefficients = np.zeros((6, len(q)))
        self.coefficients[:3] = [q, dq, np.asarray(ddq) / 2]
        t = duration
        a = np.array([[t ** 3, t ** 4, t ** 5], [3 * t ** 2, 4 * t ** 3, 5 * t ** 4],
                      [6 * t, 12 * t ** 2, 20 * t ** 3]])
        b = np.array([np.asarray(target) - q - dq * t - np.asarray(ddq) * t ** 2 / 2,
                      -np.asarray(dq) - np.asarray(ddq) * t, -np.asarray(ddq)])
        self.coefficients[3:] = np.linalg.solve(a, b)

    def sample(self, now):
        t = np.clip(now - self.start, 0, self.duration)
        c = self.coefficients
        q = np.array([1, t, t*t, t**3, t**4, t**5]) @ c
        dq = np.array([0, 1, 2*t, 3*t*t, 4*t**3, 5*t**4]) @ c
        ddq = np.array([0, 0, 2, 6*t, 12*t*t, 20*t**3]) @ c
        return q, dq, ddq

    def feasible(self, arm):
        for t in np.linspace(self.start, self.start + self.duration, 31):
            q, dq, ddq = self.sample(t)
            lower_speed, upper_speed = arm.speed_limits(q)
            if (np.any(q < arm.lower + 0.02) or np.any(q > arm.upper - 0.02)
                    or np.any(dq < lower_speed * 0.9) or np.any(dq > upper_speed * 0.9)
                    or np.any(np.abs(ddq) > arm.acceleration)):
                return False
        return True
