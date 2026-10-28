import math
import random

class Layer:
    def __init__(self, n_in, n_out):
        self.n_in = n_in
        self.n_out = n_out
        std = math.sqrt(2.0 / n_in)
        self.weights = [[random.gauss(0.0, std) for _ in range(n_in)] for _ in range(n_out)]
        self.biases = [0.0 for _ in range(n_out)]

    def forward(self, x):
        out = []
        for j in range(self.n_out):
            s = self.biases[j]
            for i in range(self.n_in):
                s += self.weights[j][i] * x[i]
            out.append(s)
        return out
