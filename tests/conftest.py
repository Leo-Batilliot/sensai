"""Shared test setup: make tests/contract_rules.py importable (`from contract_rules import ...`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
