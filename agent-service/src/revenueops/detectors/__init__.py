"""Detectors: plain rules that turn store data into revenue-at-risk case candidates. No AI here."""

from revenueops.detectors.rules import (
    DETECTORS,
    CaseCandidate,
    DetectionResult,
    Detector,
    Thresholds,
    run_detectors,
)

__all__ = ["DETECTORS", "CaseCandidate", "DetectionResult", "Detector", "Thresholds", "run_detectors"]
