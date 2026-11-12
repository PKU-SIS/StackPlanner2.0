"""Deterministic evidence tools. No tool returns a collusion verdict."""
__version__ = "1.0.0"

def run_rule(rule_id, manifest_path, output_root, **kwargs):
    from .runner import run_rule as run
    return run(rule_id, manifest_path, output_root, **kwargs)
