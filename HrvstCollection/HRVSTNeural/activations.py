def relu(vec):
    return [v if v > 0 else 0.0 for v in vec]


def relu_backward(z, grad_out):
    return [g if v > 0 else 0.0 for v, g in zip(z, grad_out)]
