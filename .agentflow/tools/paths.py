"""Keep workflow storage separate from the product repository root."""
from pathlib import Path

FLOW_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = FLOW_ROOT.parent
