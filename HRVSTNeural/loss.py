def mse(pred, target):
    n = len(pred)
    return sum((p - t) ** 2 for p, t in zip(pred, target)) / n


class LossTracker:
    def __init__(self, decay=0.997):
        self.decay = decay
        self.smooth = None

    def update(self, current):
        if self.smooth is None:
            self.smooth = current
        else:
            self.smooth = self.decay * self.smooth + (1.0 - self.decay) * current
        return self.smooth
