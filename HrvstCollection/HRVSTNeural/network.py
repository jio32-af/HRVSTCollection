import json

from layer import Layer
from activations import relu, relu_backward
from loss import mse

class HRVSTNet:
    def __init__(self, sizes=None):
        if sizes is None:
            sizes = [4, 8, 8, 16, 16, 8, 8, 4, 2]
        self.sizes = sizes
        self.layers = []
        for n_in, n_out in zip(sizes[:-1], sizes[1:]):
            self.layers.append(Layer(n_in, n_out))

    def forward(self, x):
        self.cache_xs = []
        self.cache_zs = []
        for layer in self.layers[:-1]:
            self.cache_xs.append(x)
            z = layer.forward(x)
            self.cache_zs.append(z)
            x = relu(z)
        self.cache_xs.append(x)
        z = self.layers[-1].forward(x)
        self.cache_zs.append(z)
        return z

    def train_step(self, x, target, lr=0.01):
        pred = self.forward(x)
        loss = mse(pred, target)
        n = len(pred)
        dout = [2.0 * (p - t) / n for p, t in zip(pred, target)]
        for idx in reversed(range(len(self.layers))):
            layer = self.layers[idx]
            x_in = self.cache_xs[idx]
            z = self.cache_zs[idx]
            if idx != len(self.layers) - 1:
                dout = relu_backward(z, dout)
            dW = [[dout[j] * x_in[i] for i in range(layer.n_in)] for j in range(layer.n_out)]
            db = dout[:]
            dx = [sum(layer.weights[j][i] * dout[j] for j in range(layer.n_out)) for i in range(layer.n_in)]
            for j in range(layer.n_out):
                for i in range(layer.n_in):
                    layer.weights[j][i] -= lr * dW[j][i]
                layer.biases[j] -= lr * db[j]
            dout = dx
        return loss, pred

    def to_dict(self):
        return {
            "sizes": self.sizes,
            "weights": [layer.weights for layer in self.layers],
            "biases": [layer.biases for layer in self.layers],
        }

    def save(self, path):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f)

    @classmethod
    def load(cls, path):
        with open(path) as f:
            data = json.load(f)
        net = cls(sizes=data["sizes"])
        for layer, w, b in zip(net.layers, data["weights"], data["biases"]):
            layer.weights = w
            layer.biases = b
        return net
