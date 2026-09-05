def allocate(total, weights):
    """Allocate integer units proportionally in input order."""
    return [total * weight // sum(weights) for weight in weights]
